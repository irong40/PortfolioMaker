"""
Sentinel Portfolio Maker — MipMap Desktop Service

Drives MipMap Desktop's reconstruct_full_engine.exe CLI for Gaussian Splat
generation.  Provides task JSON construction, engine launch, log monitoring,
and output copy utilities.

No REST API exists — control is exclusively through the filesystem:
write a task JSON, launch the engine subprocess, tail logs/log.txt for
[Progress]<float> lines.
"""

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

MIPMAP_ENGINE = Path(
    r"C:\Program Files\MipMap\MipMapDesktop\resources\resources\catch3d"
    r"\reconstruct_full_engine.exe"
)

RECONSTRUCT_TYPE_AT = 0   # Aerotriangulation
RECONSTRUCT_TYPE_R3D = 1  # 3D Reconstruction


# ─── AVAILABILITY CHECK ──────────────────────────────────────────────────────

def check_mipmap() -> bool:
    """Return True if MipMap engine is installed and gs_dlls extension exists.

    Checks two things:
    1. reconstruct_full_engine.exe exists on disk
    2. gs_dlls extension directory exists at %APPDATA%/mipmap-desktop/extentions/gs_dlls

    Logs a warning if the engine exists but gs_dlls is missing (splat output
    will silently fail without the extension).
    """
    engine_found = MIPMAP_ENGINE.exists()
    appdata = os.environ.get("APPDATA", "")
    gs_dlls_path = os.path.join(appdata, "mipmap-desktop", "extentions", "gs_dlls")
    gs_dlls_found = os.path.isdir(gs_dlls_path)

    if engine_found and not gs_dlls_found:
        log.warning(
            "MipMap engine found but gs_dlls extension missing at %s — "
            "Gaussian Splat output will not be generated", gs_dlls_path
        )

    return engine_found


# ─── TASK JSON BUILDER ────────────────────────────────────────────────────────

def build_splat_task_json(
    working_dir: Path,
    resolution_level: int = 3,
    mesh_decimate_ratio: float = 0.5,
) -> dict:
    """Build a MipMap task JSON dict for Gaussian Splat generation.

    Args:
        working_dir: Directory where MipMap will write outputs.
        resolution_level: Processing resolution (1=highest, 5=lowest). Default 3.
        mesh_decimate_ratio: Mesh simplification ratio (0-1). Default 0.5.

    Returns:
        Dict ready to be written as at_task.json or r3d_task.json.
    """
    appdata = os.environ.get("APPDATA", "")
    extension_base = os.path.join(appdata, "mipmap-desktop", "extentions")

    return {
        "license_id": 9000,
        "working_dir": str(working_dir),
        "extension_paths": [
            os.path.join(extension_base, "gs_dlls"),
            os.path.join(extension_base, "ml_dlls"),
        ],
        "gdal_folder": r"C:\ProgramData\MipMap\MipMapDesktop\gdal_data",
        "input_image_type": 1,
        "output_block_change_xml": True,
        "boundary_from_image": None,
        # Disable all non-splat outputs
        "generate_2D_from_3D_model": False,
        "generate_3d_tiles": False,
        "generate_obj": False,
        "generate_osgb": False,
        "generate_las": False,
        "generate_ply": False,
        "generate_fbx": False,
        "generate_skp": False,
        "generate_glb": False,
        "generate_pc_osgb": False,
        "generate_pc_pnts": False,
        "generate_pc_ply": False,
        # Splat outputs — only these two enabled
        "generate_gs_ply": True,
        "generate_gs_splat": False,
        "generate_gs_splat_sog_tiles": True,
        "generate_gs_sog": False,
        # Other processing flags
        "fill_water_area_with_AI": False,
        "generate_geotiff": False,
        "generate_tile_2D": False,
        "resolution_level": resolution_level,
        "coordinate_system_2d": {
            "type": 3,
            "type_name": "Projected",
            "label": "WGS 84 / UTM zone 18N",
            "epsg_code": 32618,
        },
        "keep_undistort_images": False,
        "build_overview": False,
        "cut_frame_2d": False,
        "cut_frame_width": 4096,
        "mesh_decimate_ratio": mesh_decimate_ratio,
        "remove_small_islands": False,
        "dom_gsd": 0,
        "camera_meta_data": [],
        "image_meta_data": [],
    }


# ─── LOG MONITOR ──────────────────────────────────────────────────────────────

def monitor_mipmap_log(log_path, progress_callback, stop_event):
    """Tail MipMap log.txt for [Progress] lines.  Run in a daemon thread.

    Args:
        log_path: Path to the MipMap log file (logs/log.txt).
        progress_callback: Called with float 0-100 for each progress update.
        stop_event: threading.Event — set to stop monitoring.
    """
    last_pos = 0
    while not stop_event.is_set():
        try:
            if Path(log_path).exists():
                with open(log_path, "r", errors="replace") as f:
                    f.seek(last_pos)
                    for line in f:
                        if "[Progress]" in line:
                            try:
                                val = float(line.split("[Progress]")[1].strip())
                                pct = min(100.0, val)
                                if progress_callback:
                                    progress_callback(pct)
                            except (ValueError, IndexError):
                                pass
                    last_pos = f.tell()
        except OSError:
            pass
        # Short sleep for test responsiveness; 1s in production
        time.sleep(0.1)


# ─── ENGINE LAUNCHER ─────────────────────────────────────────────────────────

def launch_mipmap_stage(task_json_path, reconstruct_type, log_path, progress_callback):
    """Launch one stage of MipMap reconstruction and monitor log for progress.

    Args:
        task_json_path: Path to the task JSON file (at_task.json or r3d_task.json).
        reconstruct_type: 0 for AT (aerotriangulation), 1 for R3D (reconstruction).
        log_path: Path to the log file to monitor.
        progress_callback: Called with float 0-100 for progress updates.

    Returns:
        Process return code (0 = success).
    """
    cmd = [
        str(MIPMAP_ENGINE),
        f"-task_json={task_json_path}",
        f"-reconstruct_type={reconstruct_type}",
    ]

    log.info("Launching MipMap stage %d: %s", reconstruct_type, " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    stop_event = threading.Event()

    t = threading.Thread(
        target=monitor_mipmap_log,
        args=(log_path, progress_callback, stop_event),
        daemon=True,
    )
    t.start()

    proc.wait()
    stop_event.set()
    t.join(timeout=2)

    log.info("MipMap stage %d finished with returncode %d", reconstruct_type, proc.returncode)
    return proc.returncode


# ─── PIPELINE ORCHESTRATOR ────────────────────────────────────────────────────

def run_mipmap_pipeline(
    photo_dir,
    working_dir,
    progress_callback=None,
    resolution_level=3,
    mesh_decimate_ratio=0.5,
):
    """Orchestrate full MipMap Gaussian Splat pipeline (AT then R3D).

    Args:
        photo_dir: Directory containing input photos.
        working_dir: Directory for MipMap working files and outputs.
        progress_callback: Called with float 0-100 for combined progress.
        resolution_level: Processing resolution (1-5). Default 3.
        mesh_decimate_ratio: Mesh simplification (0-1). Default 0.5.

    Returns:
        Dict with: returncode, working_dir, gs_ply_dir, gs_sog_dir.
    """
    working_dir = Path(working_dir)
    logs_dir = working_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    log_path = logs_dir / "log.txt"

    # Build task JSON
    task_json = build_splat_task_json(
        working_dir,
        resolution_level=resolution_level,
        mesh_decimate_ratio=mesh_decimate_ratio,
    )

    # --- AT Stage (0-50%) ---
    at_task_path = working_dir / "at_task.json"
    with open(at_task_path, "w") as f:
        json.dump(task_json, f, indent=2)

    def at_progress(pct):
        if progress_callback:
            progress_callback(pct * 0.5)  # Map AT 0-100 to 0-50

    at_rc = launch_mipmap_stage(
        str(at_task_path), RECONSTRUCT_TYPE_AT, str(log_path), at_progress
    )

    if at_rc != 0:
        log.error("AT stage failed with returncode %d", at_rc)
        return {
            "returncode": at_rc,
            "working_dir": str(working_dir),
            "gs_ply_dir": None,
            "gs_sog_dir": None,
        }

    # --- R3D Stage (50-100%) ---
    r3d_task_path = working_dir / "r3d_task.json"
    with open(r3d_task_path, "w") as f:
        json.dump(task_json, f, indent=2)

    def r3d_progress(pct):
        if progress_callback:
            progress_callback(50.0 + pct * 0.5)  # Map R3D 0-100 to 50-100

    r3d_rc = launch_mipmap_stage(
        str(r3d_task_path), RECONSTRUCT_TYPE_R3D, str(log_path), r3d_progress
    )

    gs_ply_dir = working_dir / "3D" / "model-gs-ply"
    gs_sog_dir = working_dir / "3D" / "model-gs-sog-tile"

    return {
        "returncode": r3d_rc,
        "working_dir": str(working_dir),
        "gs_ply_dir": str(gs_ply_dir) if gs_ply_dir.exists() else None,
        "gs_sog_dir": str(gs_sog_dir) if gs_sog_dir.exists() else None,
    }


# ─── OUTPUT COPIER ────────────────────────────────────────────────────────────

def copy_splat_outputs(working_dir, dest_dir):
    """Copy Gaussian Splat outputs from MipMap working dir to destination.

    Copies:
    - working_dir/3D/model-gs-ply/   -> dest_dir/model-gs-ply/
    - working_dir/3D/model-gs-sog-tile/ -> dest_dir/model-gs-sog-tile/

    Args:
        working_dir: MipMap working directory (contains 3D/ subdirectory).
        dest_dir: Destination directory for copied outputs.

    Returns:
        Dict of {dir_name: dest_path} for successfully copied directories.
    """
    working_dir = Path(working_dir)
    dest_dir = Path(dest_dir)
    copied = {}

    output_dirs = ["model-gs-ply", "model-gs-sog-tile"]
    for dir_name in output_dirs:
        src = working_dir / "3D" / dir_name
        dst = dest_dir / dir_name
        if src.exists() and src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            copied[dir_name] = str(dst)
            log.info("Copied %s -> %s", src, dst)
        else:
            log.warning("Expected output not found: %s", src)

    return copied
