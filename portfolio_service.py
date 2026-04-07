"""
Sortie — Portfolio Service

Orchestration layer: scan, filter, submit to NodeODM, download outputs.
No GUI dependency — called by sortie.py or CLI.
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

from photo_classifier import (
    classify_photos, filter_photos, export_photos, write_manifest,
    stitch_panoramas,
)
from odm_presets import get_preset
from mipmap_service import run_mipmap_pipeline, copy_splat_outputs, check_mipmap
from sentinel_core.nodeodm import (
    check_nodeodm as _check_nodeodm,
    submit_task,
    poll_task,
    download_outputs as _download_outputs,
)

PORTFOLIO_ROOT = os.environ.get("PORTFOLIO_ROOT", r"E:\Portfolio")
NODEODM_URL = os.environ.get("NODEODM_URL", "http://localhost:3000")


def check_nodeodm(base_url=None):
    """Check if NodeODM is reachable. Returns server info dict or None."""
    return _check_nodeodm(base_url or NODEODM_URL)


def scan_for_job(classification_result, preset):
    """Apply the preset's photo filter to a ClassificationResult.

    Args:
        classification_result: From classify_photos()
        preset: From get_preset(job_type)

    Returns:
        Filtered ClassificationResult (or original if no filter).
    """
    photo_filter = preset.get("photo_filter")
    if photo_filter:
        return filter_photos(classification_result, classification=photo_filter)
    return classification_result


def build_output_dir(site_name, date_str=None, job_type=None):
    """Build the output directory path for a portfolio job.

    Returns: str path like E:\\Portfolio\\MallTest\\2026-03-16\\property_survey\\
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    parts = [PORTFOLIO_ROOT, site_name, date_str]
    if job_type:
        parts.append(job_type)
    return str(Path(*parts))


def write_site_info(output_dir, site_name, job_type):
    """Write or update site_info.json in the site root folder."""
    output_path = Path(output_dir)
    # site_info lives in the site root (one level above the date folder)
    site_root = output_path.parent if output_path.name != site_name else output_path
    site_root.mkdir(parents=True, exist_ok=True)

    info_path = site_root / "site_info.json"

    info = {}
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    info.update({
        "site_name": site_name,
        "job_type": job_type,
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })

    # Track visits
    visits = info.get("visits", [])
    visits.append(output_path.name)
    info["visits"] = sorted(set(visits))

    info_path.write_text(json.dumps(info, indent=2))
    return str(info_path)


def submit_to_nodeodm(photo_paths, odm_options, task_name="portfolio",
                       base_url=None, poll_interval=30, max_hours=6,
                       progress_callback=None, cancel_event=None):
    """Submit photos to NodeODM and poll until complete.

    Returns:
        (task_uuid, task_info) on success, (None, error_msg) on failure.
    """
    url = base_url or NODEODM_URL

    task_uuid = submit_task(url, photo_paths, options=odm_options, name=task_name)
    if not task_uuid:
        return None, "Task submission failed"

    info = poll_task(url, task_uuid, poll_interval=poll_interval,
                     max_hours=max_hours, progress_callback=progress_callback,
                     cancel_event=cancel_event)

    if info is None:
        return None, "Task timed out"

    status_code = info.get("status", {}).get("code", -1)
    if status_code == 40:
        return task_uuid, info
    if status_code == -2:
        return None, "Canceled by user"
    if status_code == -3:
        error = info.get("status", {}).get("errorMessage", "NodeODM offline")
        return None, error
    if status_code == 30:
        error = info.get("status", {}).get("errorMessage", "unknown")
        return None, f"Task failed: {error}"
    if status_code == 50:
        return None, "Task was canceled"

    return None, f"Unexpected status: {status_code}"


def download_outputs(task_uuid, output_dir, download_list, base_url=None):
    """Download specific outputs from a completed NodeODM task.

    Returns:
        Dict of {asset_name: local_path} for successful downloads.
    """
    url = base_url or NODEODM_URL
    return _download_outputs(url, task_uuid, output_dir, download_list=download_list)


def process_job(source_dir, job_type, site_name, threshold=-70.0,
                bbox=None, base_url=None, progress_callback=None,
                output_dir=None, cancel_event=None):
    """Full portfolio job: scan → filter → submit → download.

    This is the main entry point called by the GUI or CLI.

    Args:
        source_dir: Folder containing drone photos
        job_type: Key from PRESETS (e.g., "construction_progress")
        site_name: Human name for the site (e.g., "MallTest")
        threshold: Nadir pitch threshold
        bbox: Optional (min_lat, max_lat, min_lon, max_lon)
        base_url: NodeODM URL override
        progress_callback: Optional callable(stage, detail) for GUI updates

    Returns:
        dict with keys: output_dir, downloaded, task_uuid, classification, etc.
        On error, dict contains "error" key.
    """
    log = logging.getLogger(__name__)

    def notify(stage, detail=""):
        if progress_callback:
            progress_callback(stage, detail)
        log.info(f"[{stage}] {detail}")

    # 1. Classify
    notify("scan", f"Scanning {source_dir}")
    classification = classify_photos(source_dir, threshold=threshold)
    if classification.total == 0:
        return {"error": "No photos found"}

    # Get preset with platform-specific overrides
    platform = classification.platform
    preset = get_preset(job_type, platform=platform)
    if platform:
        notify("platform", f"Detected {platform} — applied platform overrides")

    # 2. Filter by preset + optional bbox
    working_set = scan_for_job(classification, preset)
    if bbox:
        working_set = filter_photos(working_set, bbox=bbox)
    if working_set.total == 0:
        return {"error": "No photos match filter criteria"}

    notify("filtered", f"{working_set.total} photos selected ({preset['label']})")

    # 3. Build output dir
    date_str = datetime.now().strftime("%Y-%m-%d")
    if not output_dir:
        output_dir = build_output_dir(site_name, date_str, job_type)
    os.makedirs(output_dir, exist_ok=True)

    # 4. Write site info
    write_site_info(output_dir, site_name, job_type)

    # 5. Route to engine
    engine = preset.get("engine", "nodeodm")
    downloaded = {}

    if engine == "mipmap":
        # MipMap pipeline — export filtered photos to staging dir
        mipmap_settings = preset.get("mipmap_settings", {})
        working_dir = Path(output_dir) / "_mipmap_work"
        staging_dir = working_dir / "photos"
        staging_dir.mkdir(parents=True, exist_ok=True)
        for photo in working_set.photos:
            import shutil as _shutil
            _shutil.copy2(photo.path, staging_dir / photo.filename)
        notify("submit", f"Launching MipMap with {working_set.total} photos")

        mipmap_result = run_mipmap_pipeline(
            photo_dir=str(staging_dir),
            working_dir=working_dir,
            progress_callback=lambda pct: notify("processing", f"MipMap {pct:.0f}%"),
            resolution_level=mipmap_settings.get("resolution_level", 3),
            mesh_decimate_ratio=mipmap_settings.get("mesh_decimate_ratio", 0.5),
        )

        if mipmap_result.get("returncode", 1) != 0:
            return {"error": f"MipMap failed (exit code {mipmap_result.get('returncode')})",
                    "output_dir": output_dir, "classification": classification,
                    "working_set": working_set}

        notify("download", "Copying splat outputs")
        downloaded = copy_splat_outputs(working_dir, output_dir)
        notify("processing", "MipMap processing complete")
    else:
        # NodeODM pipeline
        photo_paths = [p.path for p in working_set.photos]
        task_name = f"portfolio-{site_name[:20]}-{date_str}"
        notify("submit", f"Submitting {len(photo_paths)} photos to NodeODM")

        def on_nodeodm_progress(pct):
            notify("nodeodm_progress", f"{pct:.0f}")

        task_uuid, result = submit_to_nodeodm(
            photo_paths, preset["odm_options"], task_name=task_name, base_url=base_url,
            progress_callback=on_nodeodm_progress, cancel_event=cancel_event,
        )

        if task_uuid is None:
            return {"error": f"NodeODM: {result}", "output_dir": output_dir,
                    "classification": classification, "working_set": working_set}

        notify("processing", "NodeODM processing complete")

        notify("download", "Downloading outputs")
        downloaded = download_outputs(task_uuid, output_dir, preset["downloads"], base_url=base_url)

    # 7. Stitch panoramas if detected
    if classification.panorama_sets:
        pano_dir = str(Path(output_dir) / "panoramas")
        notify("panorama", f"Stitching {len(classification.panorama_sets)} panorama(s)")
        stitch_panoramas(classification.panorama_sets, pano_dir)
        stitched = [ps for ps in classification.panorama_sets if ps.stitched_path]
        failed = [ps for ps in classification.panorama_sets if ps.stitch_error]
        if stitched:
            notify("panorama", f"{len(stitched)} panorama(s) stitched")
        if failed:
            notify("warning", f"{len(failed)} panorama(s) failed to stitch")

    # 8. Write manifest
    write_manifest(working_set, Path(output_dir) / "manifest.json")

    # 9. AI analysis + image preparation + report generation
    report_result = None
    try:
        from report_generator import generate_report

        ortho_path = downloaded.get("orthophoto.tif")
        dsm_path = downloaded.get("dsm.tif")
        photos = working_set.photos

        # AI photo analysis (skipped gracefully if no API key)
        ai_analysis = None
        try:
            from report_ai import analyze_photos
            notify("report", "Analyzing photos with AI...")
            ai_analysis = analyze_photos(photos, job_type, site_name)
            if ai_analysis:
                notify("report", f"AI found {len(ai_analysis.get('observations', []))} observations")
        except ImportError:
            log.info("report_ai not available — skipping AI analysis")

        # Prepare images for embedding
        images = None
        try:
            from report_images import prepare_report_images
            notify("report", "Preparing report images...")
            images = prepare_report_images(photos, ortho_path, dsm_path, output_dir)
        except ImportError:
            log.info("report_images not available — skipping image embedding")

        # Point cloud / mesh operations
        pc_results = {}
        try:
            from point_cloud_ops import (
                find_previous_visit, compare_dsms, save_change_map,
                cleanup_mesh, get_mesh_stats,
            )

            # Mesh cleanup + stats (roof, structures, real estate)
            mesh_path = downloaded.get("textured_model.zip")
            if mesh_path:
                notify("report", "Analyzing mesh...")
                mesh_stats = get_mesh_stats(mesh_path)
                if mesh_stats:
                    pc_results["mesh_stats"] = mesh_stats

            # DSM comparison with previous visit (construction, survey)
            if dsm_path:
                prev = find_previous_visit(output_dir, site_name, date_str)
                if prev and prev.get("previous_dsm"):
                    notify("report", f"Comparing with previous visit ({prev['previous_date']})...")
                    dsm_diff = compare_dsms(dsm_path, prev["previous_dsm"])
                    if dsm_diff:
                        pc_results["dsm_comparison"] = {
                            k: v for k, v in dsm_diff.items() if k != "change_map"
                        }
                        pc_results["previous_date"] = prev["previous_date"]
                        # Save change map image for report
                        change_img = save_change_map(
                            dsm_diff["change_map"],
                            str(Path(output_dir) / "_report_thumbs" / "change_map.jpg"),
                        )
                        if change_img:
                            pc_results["change_map_image"] = change_img
                            if images:
                                images["change_map"] = change_img
                        notify("report",
                               f"Volume change: fill={dsm_diff['fill_volume_m3']:.0f}m\u00b3, "
                               f"cut={dsm_diff['cut_volume_m3']:.0f}m\u00b3")
        except ImportError:
            log.info("point_cloud_ops not available — skipping 3D analysis")

        notify("report", f"Generating {preset['report_type']} report")
        report_data = {
            "site_name": site_name,
            "date": date_str,
            "job_type": job_type,
            "total_photos": classification.total,
            "nadir_count": classification.nadir_count,
            "oblique_count": classification.oblique_count,
            "platform": classification.platform,
            "gps_bounds": classification.gps_bounds,
            "ortho_path": ortho_path,
            "dsm_path": dsm_path,
            "downloads": downloaded,
            "engine": engine,
            "mipmap_settings": preset.get("mipmap_settings", {}),
            "photos": photos,
            "ai_analysis": ai_analysis,
            "images": images,
            "pc_results": pc_results,
        }
        report_result = generate_report(preset["report_type"], report_data, output_dir)
        if report_result is None:
            notify("warning", "Report generation failed — deliverable incomplete")
    except ImportError:
        log.warning("report_generator not available — skipping report")

    notify("complete", f"Output: {output_dir}")

    result = {
        "output_dir": output_dir,
        "classification": classification,
        "working_set": working_set,
        "downloaded": downloaded,
        "task_uuid": None if engine == "mipmap" else task_uuid,
        "preset": preset,
        "date": date_str,
        "report": report_result,
    }
    if report_result is None:
        result["warning"] = "Report generation failed"
    return result


def portfolio_only(source_dir, job_type, site_name, threshold=-70.0,
                    bbox=None, progress_callback=None, output_dir=None):
    """Local sort only — no NodeODM. For portfolio photo organization.

    Returns:
        dict with output_dir, classification, working_set.
    """
    log = logging.getLogger(__name__)

    def notify(stage, detail=""):
        if progress_callback:
            progress_callback(stage, detail)
        log.info(f"[{stage}] {detail}")

    notify("scan", f"Scanning {source_dir}")
    classification = classify_photos(source_dir, threshold=threshold)
    if classification.total == 0:
        return {"error": "No photos found"}

    platform = classification.platform
    preset = get_preset(job_type, platform=platform)
    if platform:
        notify("platform", f"Detected {platform} — applied platform overrides")

    working_set = scan_for_job(classification, preset)
    if bbox:
        working_set = filter_photos(working_set, bbox=bbox)
    if working_set.total == 0:
        return {"error": "No photos match filter criteria"}

    notify("sort", f"Sorting {working_set.total} photos locally")

    date_str = datetime.now().strftime("%Y-%m-%d")
    if not output_dir:
        output_dir = build_output_dir(site_name, date_str, job_type)
    os.makedirs(output_dir, exist_ok=True)

    write_site_info(output_dir, site_name, job_type)

    # Export filtered photos to output_dir (not sort into source folder)
    export_photos(working_set, output_dir, copy=True)

    # Stitch panoramas if detected
    if classification.panorama_sets:
        pano_dir = str(Path(output_dir) / "panoramas")
        notify("panorama", f"Stitching {len(classification.panorama_sets)} panorama(s)")
        stitch_panoramas(classification.panorama_sets, pano_dir)
        stitched = [ps for ps in classification.panorama_sets if ps.stitched_path]
        failed = [ps for ps in classification.panorama_sets if ps.stitch_error]
        if stitched:
            notify("panorama", f"{len(stitched)} panorama(s) stitched")
        if failed:
            notify("warning", f"{len(failed)} panorama(s) failed to stitch")

    write_manifest(working_set, Path(output_dir) / "manifest.json")

    # Generate report even in portfolio-only mode (with AI + images)
    report_result = None
    try:
        from report_generator import generate_report
        photos = working_set.photos

        # AI photo analysis
        ai_analysis = None
        try:
            from report_ai import analyze_photos
            notify("report", "Analyzing photos with AI...")
            ai_analysis = analyze_photos(photos, job_type, site_name)
        except ImportError:
            log.info("report_ai not available — skipping AI analysis")

        # Prepare images for embedding
        images = None
        try:
            from report_images import prepare_report_images
            notify("report", "Preparing report images...")
            images = prepare_report_images(photos, None, None, output_dir)
        except ImportError:
            log.info("report_images not available — skipping image embedding")

        notify("report", f"Generating {preset['report_type']} report")
        report_data = {
            "site_name": site_name,
            "date": date_str,
            "job_type": job_type,
            "total_photos": classification.total,
            "nadir_count": classification.nadir_count,
            "oblique_count": classification.oblique_count,
            "platform": classification.platform,
            "gps_bounds": classification.gps_bounds,
            "downloads": {},
            "photos": photos,
            "ai_analysis": ai_analysis,
            "images": images,
        }
        report_result = generate_report(preset["report_type"], report_data, output_dir)
        if report_result is None:
            notify("warning", "Report generation failed — deliverable incomplete")
    except ImportError:
        log.warning("report_generator not available — skipping report")

    notify("complete", f"Output: {output_dir}")

    result = {
        "output_dir": output_dir,
        "classification": classification,
        "working_set": working_set,
        "downloaded": {},
        "task_uuid": None,
        "preset": preset,
        "date": date_str,
        "report": report_result,
    }
    if report_result is None:
        result["warning"] = "Report generation failed"
    return result
