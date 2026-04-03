"""
Sortie — Photo Classifier

Sorts drone photos into nadir/oblique folders based on gimbal pitch angle.
Uses sentinel-core for EXIF/XMP extraction and platform detection.

This module contains the core logic — no GUI dependency.
Can be used standalone via CLI or called from the Tkinter app.

Usage (CLI):
    python photo_classifier.py D:\\DronePhotos\\JobSite1
    python photo_classifier.py D:\\DronePhotos\\JobSite1 --threshold -75
    python photo_classifier.py D:\\DronePhotos\\JobSite1 --dry-run
    python photo_classifier.py D:\\DronePhotos\\JobSite1 --metadata-only
"""

import os
import sys
import json
import shutil
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field

# ─── SENTINEL-CORE IMPORTS ──────────────────────────────────────────────────

from sentinel_core.metadata import extract_gps_from_exif, extract_xmp_gimbal
from sentinel_core.platform import detect_platform_from_file

PIPELINE_AVAILABLE = True  # sentinel-core is always installed


def get_gimbal_data(filepath):
    """Extract gimbal pitch/roll/yaw from a photo."""
    return extract_xmp_gimbal(filepath)


def get_gps_data(filepath):
    """Extract GPS [lon, lat, alt] from a photo."""
    return extract_gps_from_exif(filepath)


def get_platform(filepath):
    """Detect drone platform. Returns (platform, method) or (None, None)."""
    return detect_platform_from_file(filepath)


# ─── CLASSIFICATION ─────────────────────────────────────────────────────────

from sentinel_core.constants import PHOTO_EXTENSIONS

@dataclass
class PhotoMeta:
    filename: str
    path: str
    pitch: float = None
    roll: float = None
    yaw: float = None
    latitude: float = None
    longitude: float = None
    altitude: float = None
    relative_altitude: float = None
    platform: str = None
    classification: str = "unknown"  # nadir, oblique, unknown


@dataclass
class PanoramaSet:
    """A group of source photos that form one panorama."""
    folder: str
    photo_count: int
    photos: list = field(default_factory=list)  # list of file paths
    stitched_path: str = ""  # set after stitching
    stitch_error: str = ""   # set if stitching fails


@dataclass
class ClassificationResult:
    source_dir: str
    nadir_count: int = 0
    oblique_count: int = 0
    unknown_count: int = 0
    panorama_count: int = 0
    total: int = 0
    pitch_min: float = None
    pitch_max: float = None
    platform: str = None
    photos: list = field(default_factory=list)
    panorama_sets: list = field(default_factory=list)  # list of PanoramaSet
    nadir_dir: str = ""
    oblique_dir: str = ""
    unknown_dir: str = ""
    panorama_dir: str = ""
    threshold: float = -70.0
    created_at: str = ""
    failed_transfers: list = field(default_factory=list)

    @property
    def gps_bounds(self):
        """Return (min_lat, max_lat, min_lon, max_lon) from all photos with GPS."""
        lats = [p.latitude for p in self.photos if p.latitude is not None]
        lons = [p.longitude for p in self.photos if p.longitude is not None]
        if not lats or not lons:
            return None
        return (min(lats), max(lats), min(lons), max(lons))


def classify_pitch(pitch, threshold=-70.0):
    """Classify a gimbal pitch angle as nadir or oblique.

    DJI convention: -90 = straight down, 0 = horizon.
    Default: anything from -95 to threshold is nadir.
    """
    if pitch is None:
        return "unknown"
    if -95 <= pitch <= threshold:
        return "nadir"
    return "oblique"


def scan_photos(source_dir):
    """Find all photo files in a directory tree (recursive)."""
    source = Path(source_dir)
    photos = []
    for root, dirs, files in os.walk(source):
        for f in sorted(files):
            fpath = Path(root) / f
            if fpath.suffix.lower() in PHOTO_EXTENSIONS:
                photos.append(fpath)
    return sorted(photos)


def scan_panoramas(source_dir):
    """Detect panorama sets in a PANORAMA/ subfolder.

    DJI drones store panorama source photos in:
        source_dir/PANORAMA/<set_id>/PANO_*.JPG

    Also checks the parent directory for a PANORAMA/ folder,
    since DJI SD cards put PANORAMA/ alongside DJI_xxx/ photo folders.

    Returns a list of PanoramaSet objects.
    """
    source = Path(source_dir)
    pano_dir = source / "PANORAMA"
    if not pano_dir.is_dir():
        # Check parent — SD card layout: parent/DJI_xxx/ + parent/PANORAMA/
        parent_pano = source.parent / "PANORAMA"
        if parent_pano.is_dir():
            pano_dir = parent_pano
        else:
            return []

    sets = []
    for subfolder in sorted(pano_dir.iterdir()):
        if not subfolder.is_dir():
            continue
        photos = sorted({
            f for f in subfolder.iterdir()
            if f.is_file() and f.suffix.lower() in {".jpg", ".jpeg"}
        })
        if photos:
            sets.append(PanoramaSet(
                folder=str(subfolder),
                photo_count=len(photos),
                photos=[str(p) for p in photos],
            ))

    return sets


def classify_photos(source_dir, threshold=-70.0, progress_callback=None):
    """Read metadata and classify all photos in source_dir.

    Args:
        source_dir: Path to folder containing drone photos
        threshold: Pitch angle cutoff (default -70). Nadir = [-95, threshold]
        progress_callback: Optional callable(current, total, filename) for GUI updates

    Returns:
        ClassificationResult with all photo metadata and counts
    """
    log = logging.getLogger(__name__)
    photos = scan_photos(source_dir)

    if not photos:
        log.warning(f"No photos found in {source_dir}")
        return ClassificationResult(source_dir=str(source_dir))

    result = ClassificationResult(
        source_dir=str(source_dir),
        total=len(photos),
        threshold=threshold,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )

    # Detect platform from first photo
    platform, _ = get_platform(str(photos[0]))
    result.platform = platform

    pitches = []

    for i, photo_path in enumerate(photos):
        meta = PhotoMeta(filename=photo_path.name, path=str(photo_path))

        # Extract gimbal data
        gimbal = get_gimbal_data(str(photo_path))
        if gimbal:
            meta.pitch = gimbal["pitch"]
            meta.roll = gimbal["roll"]
            meta.yaw = gimbal["yaw"]
            meta.relative_altitude = gimbal.get("relative_altitude")

        # Extract GPS
        gps = get_gps_data(str(photo_path))
        if gps:
            meta.longitude = gps[0]
            meta.latitude = gps[1]
            meta.altitude = gps[2]

        meta.platform = platform
        meta.classification = classify_pitch(meta.pitch, threshold)

        if meta.classification == "nadir":
            result.nadir_count += 1
        elif meta.classification == "oblique":
            result.oblique_count += 1
        else:
            result.unknown_count += 1

        if meta.pitch is not None:
            pitches.append(meta.pitch)

        result.photos.append(meta)

        if progress_callback and ((i + 1) % 50 == 0 or (i + 1) == len(photos)):
            progress_callback(i + 1, len(photos), photo_path.name)

    if pitches:
        result.pitch_min = min(pitches)
        result.pitch_max = max(pitches)

    # Detect panorama sets
    pano_sets = scan_panoramas(source_dir)
    result.panorama_sets = pano_sets
    result.panorama_count = len(pano_sets)

    return result


def sort_photos(result, copy=True, progress_callback=None):
    """Copy (or move) classified photos into nadir/oblique/unknown subfolders.

    Args:
        result: ClassificationResult from classify_photos()
        copy: If True, copy files. If False, move them.
        progress_callback: Optional callable(current, total, filename)

    Returns:
        Updated ClassificationResult with nadir_dir/oblique_dir/unknown_dir paths set.
        Any per-file failures are recorded in result.failed_transfers.
    """
    log = logging.getLogger(__name__)
    source = Path(result.source_dir)

    nadir_dir = source / "nadir"
    oblique_dir = source / "oblique"
    unknown_dir = source / "unknown"

    nadir_dir.mkdir(exist_ok=True)
    oblique_dir.mkdir(exist_ok=True)

    result.nadir_dir = str(nadir_dir)
    result.oblique_dir = str(oblique_dir)
    result.failed_transfers = []

    transfer = shutil.copy2 if copy else shutil.move

    for i, photo in enumerate(result.photos):
        if photo.classification == "nadir":
            dest = nadir_dir / photo.filename
        elif photo.classification == "oblique":
            dest = oblique_dir / photo.filename
        else:
            unknown_dir.mkdir(exist_ok=True)
            result.unknown_dir = str(unknown_dir)
            dest = unknown_dir / photo.filename

        try:
            if dest.exists():
                log.warning(f"Skipping collision: {dest}")
                result.failed_transfers.append((photo.filename, "file already exists"))
                continue
            transfer(photo.path, dest)
        except (OSError, shutil.Error) as e:
            log.error(f"Failed to {'copy' if copy else 'move'} {photo.filename}: {e}")
            result.failed_transfers.append((photo.filename, str(e)))

        if progress_callback and ((i + 1) % 100 == 0 or (i + 1) == len(result.photos)):
            progress_callback(i + 1, len(result.photos), photo.filename)

    if result.failed_transfers:
        log.warning(f"{len(result.failed_transfers)} file(s) failed to transfer")

    # Stitch panoramas if any detected
    if result.panorama_sets:
        panorama_out = source / "panorama"
        panorama_out.mkdir(exist_ok=True)
        result.panorama_dir = str(panorama_out)
        stitch_panoramas(result.panorama_sets, str(panorama_out),
                         progress_callback=progress_callback)

    return result


def stitch_panoramas(panorama_sets, output_dir, progress_callback=None):
    """Stitch each panorama set and save to output_dir.

    Args:
        panorama_sets: List of PanoramaSet objects
        output_dir: Folder to save stitched panoramas
        progress_callback: Optional callable(current, total, filename)
    """
    log = logging.getLogger(__name__)

    try:
        import cv2
        cv2.ocl.setUseOpenCL(False)
    except ImportError:
        log.warning("OpenCV not installed — skipping panorama stitching")
        for ps in panorama_sets:
            ps.stitch_error = "OpenCV not installed"
        return

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    for i, ps in enumerate(panorama_sets):
        folder_name = Path(ps.folder).name
        log.info(f"Stitching panorama {i + 1}/{len(panorama_sets)}: {folder_name} ({ps.photo_count} photos)")

        images = []
        for photo_path in ps.photos:
            img = cv2.imread(photo_path)
            if img is None:
                continue
            h, w = img.shape[:2]
            if w > 2000:
                scale = 2000 / w
                img = cv2.resize(img, (int(w * scale), int(h * scale)))
            images.append(img)

        if len(images) < 2:
            ps.stitch_error = f"Only {len(images)} readable images"
            log.warning(f"  Skipping {folder_name}: {ps.stitch_error}")
            continue

        stitcher = cv2.Stitcher.create(cv2.Stitcher_PANORAMA)
        status, pano = stitcher.stitch(images)

        if status != cv2.Stitcher_OK:
            errors = {
                cv2.Stitcher_ERR_NEED_MORE_IMGS: "not enough overlap",
                cv2.Stitcher_ERR_HOMOGRAPHY_EST_FAIL: "homography failed",
                cv2.Stitcher_ERR_CAMERA_PARAMS_ADJUST_FAIL: "camera params failed",
            }
            ps.stitch_error = errors.get(status, f"unknown error {status}")
            log.warning(f"  Failed {folder_name}: {ps.stitch_error}")
            continue

        output_path = out / f"{folder_name}_panorama.jpg"
        cv2.imwrite(str(output_path), pano, [cv2.IMWRITE_JPEG_QUALITY, 95])
        ps.stitched_path = str(output_path)
        log.info(f"  Saved: {output_path.name} ({pano.shape[1]}x{pano.shape[0]})")

        # Copy to central panorama gallery for DroneInvoice uploads
        pano_gallery = Path(os.environ.get("PANO_GALLERY", r"E:\Portfolio\_Panoramas"))
        try:
            pano_gallery.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(str(output_path), str(pano_gallery / output_path.name))
            log.info(f"  Copied to gallery: {pano_gallery / output_path.name}")
        except OSError as e:
            log.warning(f"  Gallery copy failed: {e}")

        if progress_callback:
            progress_callback(i + 1, len(panorama_sets), folder_name)


def filter_photos(result, bbox=None, classification=None):
    """Filter a ClassificationResult to a subset of photos.

    Args:
        result: ClassificationResult to filter
        bbox: (min_lat, max_lat, min_lon, max_lon) — GPS bounding box
        classification: "nadir", "oblique", or None for all

    Returns:
        New ClassificationResult with only matching photos
    """
    filtered = []
    for p in result.photos:
        if classification and p.classification != classification:
            continue
        if bbox:
            min_lat, max_lat, min_lon, max_lon = bbox
            if p.latitude is None or p.longitude is None:
                continue
            if not (min_lat <= p.latitude <= max_lat and min_lon <= p.longitude <= max_lon):
                continue
        filtered.append(p)

    nadir = sum(1 for p in filtered if p.classification == "nadir")
    oblique = sum(1 for p in filtered if p.classification == "oblique")
    unknown = sum(1 for p in filtered if p.classification == "unknown")
    pitches = [p.pitch for p in filtered if p.pitch is not None]

    return ClassificationResult(
        source_dir=result.source_dir,
        nadir_count=nadir,
        oblique_count=oblique,
        unknown_count=unknown,
        panorama_count=result.panorama_count,
        total=len(filtered),
        pitch_min=min(pitches) if pitches else None,
        pitch_max=max(pitches) if pitches else None,
        platform=result.platform,
        photos=filtered,
        panorama_sets=result.panorama_sets,
        threshold=result.threshold,
        created_at=result.created_at,
    )


def export_photos(result, output_dir, copy=True, progress_callback=None):
    """Export filtered photos to a flat output directory.

    Unlike sort_photos which creates nadir/oblique subdirs, this copies
    all photos in the result into a single folder. Useful for exporting
    a GPS-filtered subset to feed directly into WebODM.

    Args:
        result: ClassificationResult (possibly filtered)
        output_dir: Destination folder
        copy: If True copy, if False move
        progress_callback: Optional callable(current, total, filename)

    Returns:
        Path to output directory. Any failures are recorded in result.failed_transfers.
    """
    log = logging.getLogger(__name__)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    transfer = shutil.copy2 if copy else shutil.move
    result.failed_transfers = []

    for i, photo in enumerate(result.photos):
        dest = out / photo.filename
        try:
            if dest.exists():
                log.warning(f"Skipping collision: {dest}")
                result.failed_transfers.append((photo.filename, "file already exists"))
                continue
            transfer(photo.path, dest)
        except (OSError, shutil.Error) as e:
            log.error(f"Failed to export {photo.filename}: {e}")
            result.failed_transfers.append((photo.filename, str(e)))

        if progress_callback and ((i + 1) % 100 == 0 or (i + 1) == len(result.photos)):
            progress_callback(i + 1, len(result.photos), photo.filename)

    if result.failed_transfers:
        log.warning(f"{len(result.failed_transfers)} file(s) failed to export")

    return str(out)


def write_manifest(result, output_path=None):
    """Write classification manifest to JSON.

    Args:
        result: ClassificationResult
        output_path: Where to write. Defaults to source_dir/manifest.json
    """
    if output_path is None:
        output_path = Path(result.source_dir) / "manifest.json"

    manifest = {
        "sortie_version": "1.0",
        "source_dir": result.source_dir,
        "created_at": result.created_at,
        "platform": result.platform,
        "threshold": result.threshold,
        "summary": {
            "total": result.total,
            "nadir": result.nadir_count,
            "oblique": result.oblique_count,
            "unknown": result.unknown_count,
            "panoramas": result.panorama_count,
        },
        "pitch_range": {
            "min": result.pitch_min,
            "max": result.pitch_max,
        },
        "gps_bounds": {
            "min_lat": result.gps_bounds[0] if result.gps_bounds else None,
            "max_lat": result.gps_bounds[1] if result.gps_bounds else None,
            "min_lon": result.gps_bounds[2] if result.gps_bounds else None,
            "max_lon": result.gps_bounds[3] if result.gps_bounds else None,
        },
        "output_dirs": {
            "nadir": result.nadir_dir,
            "oblique": result.oblique_dir,
            "unknown": result.unknown_dir,
            "panorama": result.panorama_dir,
        },
        "panoramas": [
            {
                "folder": ps.folder,
                "photo_count": ps.photo_count,
                "stitched_path": ps.stitched_path,
                "stitch_error": ps.stitch_error,
            }
            for ps in result.panorama_sets
        ],
        "photos": [
            {
                "filename": p.filename,
                "classification": p.classification,
                "pitch": p.pitch,
                "yaw": p.yaw,
                "latitude": p.latitude,
                "longitude": p.longitude,
                "altitude": p.altitude,
                "relative_altitude": p.relative_altitude,
            }
            for p in result.photos
        ],
    }

    with open(output_path, "w") as f:
        json.dump(manifest, f, indent=2)

    return str(output_path)


# ─── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Sortie — Photo Classifier",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Sorts DJI drone photos into nadir (straight down) and oblique (angled) folders
based on gimbal pitch angle from EXIF/XMP metadata.

Area filtering lets you extract a subset of photos by GPS bounding box —
useful for producing deliverables from a specific part of a larger site.

Examples:
  python photo_classifier.py D:\\DronePhotos\\JobSite1
  python photo_classifier.py D:\\DronePhotos\\JobSite1 --threshold -75
  python photo_classifier.py D:\\DronePhotos\\JobSite1 --dry-run
  python photo_classifier.py D:\\DronePhotos\\JobSite1 --metadata-only
  python photo_classifier.py D:\\DronePhotos\\JobSite1 --move

  # Export only nadir photos from the NE corner for volume measurement:
  python photo_classifier.py D:\\DronePhotos\\JobSite1 --metadata-only
  python photo_classifier.py D:\\DronePhotos\\JobSite1 --filter nadir \\
      --bbox 36.827,36.829,-76.415,-76.413 --export D:\\WebODM\\stockpile_job
        """,
    )
    parser.add_argument("source", help="Folder containing drone photos")
    parser.add_argument("--threshold", type=float, default=-70.0,
                        help="Nadir pitch cutoff in degrees (default: -70)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Classify and show results without copying files")
    parser.add_argument("--metadata-only", action="store_true",
                        help="Extract metadata and write manifest only")
    parser.add_argument("--move", action="store_true",
                        help="Move files instead of copying (originals will be relocated)")
    parser.add_argument("--no-manifest", action="store_true",
                        help="Skip writing manifest.json")
    parser.add_argument("--filter", choices=["nadir", "oblique"],
                        help="Only include photos of this type")
    parser.add_argument("--bbox", type=str,
                        help="GPS bounding box: min_lat,max_lat,min_lon,max_lon")
    parser.add_argument("--export", type=str, metavar="DIR",
                        help="Export filtered photos to this folder (flat, no nadir/oblique split)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger(__name__)

    source = os.path.abspath(args.source)
    if not os.path.isdir(source):
        sys.exit(f"Error: Directory not found: {source}")

    log.info("Pipeline modules: sentinel-core")
    log.info(f"Scanning: {source}")
    log.info(f"Threshold: {args.threshold} degrees (nadir = -95 to {args.threshold})")
    log.info("")

    # Classify
    def on_classify_progress(current, total, filename):
        log.info(f"  Reading metadata: {current}/{total}")

    result = classify_photos(source, threshold=args.threshold,
                             progress_callback=on_classify_progress)

    if result.total == 0:
        sys.exit("No photos found")

    # Show full-set summary
    log.info("")
    log.info(f"Platform:  {result.platform or 'unknown'}")
    log.info(f"Total:     {result.total} photos")
    log.info(f"Nadir:     {result.nadir_count}")
    log.info(f"Oblique:   {result.oblique_count}")
    if result.unknown_count:
        log.info(f"Unknown:   {result.unknown_count} (no pitch data)")
    if result.panorama_count:
        log.info(f"Panoramas: {result.panorama_count} sets")
        for ps in result.panorama_sets:
            log.info(f"  {Path(ps.folder).name}: {ps.photo_count} photos")
    if result.pitch_min is not None:
        log.info(f"Pitch:     {result.pitch_min:.1f} to {result.pitch_max:.1f} degrees")
    if result.gps_bounds:
        b = result.gps_bounds
        log.info(f"GPS area:  {b[0]:.6f},{b[2]:.6f} to {b[1]:.6f},{b[3]:.6f}")

    # Apply filters if requested
    bbox = None
    if args.bbox:
        try:
            parts = [float(x.strip()) for x in args.bbox.split(",")]
            if len(parts) != 4:
                raise ValueError
            bbox = tuple(parts)
        except ValueError:
            sys.exit("--bbox must be 4 comma-separated numbers: min_lat,max_lat,min_lon,max_lon")

    if args.filter or bbox:
        result = filter_photos(result, bbox=bbox, classification=args.filter)
        log.info("")
        filters = []
        if args.filter:
            filters.append(f"type={args.filter}")
        if bbox:
            filters.append(f"bbox={args.bbox}")
        log.info(f"Filter:    {', '.join(filters)}")
        log.info(f"Matched:   {result.total} photos")
        if result.total == 0:
            sys.exit("No photos match the filter criteria")

    # Export filtered subset to a flat folder
    if args.export:
        log.info("")
        def on_export_progress(current, total, filename):
            log.info(f"  Exporting: {current}/{total}")
        export_path = export_photos(result, args.export, copy=True,
                                     progress_callback=on_export_progress)
        log.info(f"Exported {result.total} photos to: {export_path}")
        if not args.no_manifest:
            manifest_path = write_manifest(result, Path(export_path) / "manifest.json")
            log.info(f"Manifest:  {manifest_path}")
        return

    # Dry run or metadata-only — write manifest and stop
    if args.dry_run or args.metadata_only:
        if not args.no_manifest:
            manifest_path = write_manifest(result)
            log.info(f"\nManifest:  {manifest_path}")
        if args.dry_run:
            log.info("\n[DRY RUN] No files were copied.")
        return

    # Sort into nadir/oblique subfolders
    log.info("")

    def on_sort_progress(current, total, filename):
        log.info(f"  {'Moving' if args.move else 'Copying'}: {current}/{total}")

    result = sort_photos(result, copy=not args.move, progress_callback=on_sort_progress)

    if not args.no_manifest:
        manifest_path = write_manifest(result)
        log.info(f"\nManifest:  {manifest_path}")

    # Next steps
    log.info("")
    log.info("Next steps:")
    log.info(f"  Orthophoto/DSM/volume: process {result.nadir_dir}")
    log.info(f"  3D model:             process all photos from original folder")


if __name__ == "__main__":
    main()
