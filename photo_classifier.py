"""
Sentinel Portfolio Maker — Photo Classifier

Sorts drone photos into nadir/oblique folders based on gimbal pitch angle.
Reuses EXIF/XMP extraction from the drone-pipeline (ingest.py, platform_detect.py).

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
from dataclasses import dataclass, field, asdict

# ─── DRONE-PIPELINE IMPORTS ─────────────────────────────────────────────────
# Add drone-pipeline to path so we can reuse its battle-tested EXIF logic

DRONE_PIPELINE_DIR = Path(r"C:\Users\redle.SOULAAN\Documents\drone-pipeline")
if DRONE_PIPELINE_DIR.exists() and str(DRONE_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(DRONE_PIPELINE_DIR))

try:
    from ingest import extract_gps_from_exif, extract_xmp_gimbal, parse_dji_filename
    from platform_detect import detect_platform_from_file
    PIPELINE_AVAILABLE = True
except ImportError:
    PIPELINE_AVAILABLE = False

# ─── FALLBACK XMP EXTRACTION ────────────────────────────────────────────────
# If drone-pipeline isn't available, use a minimal local implementation

import re

def _fallback_extract_xmp_gimbal(filepath):
    """Minimal XMP gimbal extraction — used only if drone-pipeline not on path."""
    try:
        with open(filepath, "rb") as f:
            data = f.read(500000)
        start = data.find(b"<x:xmpmeta")
        if start < 0:
            return None
        end = data.find(b"</x:xmpmeta>", start) + len(b"</x:xmpmeta>")
        xmp = data[start:end].decode("utf-8", errors="ignore")
        fields = dict(re.findall(r'drone-dji:(\w+)="([^"]+)"', xmp))
        return {
            "pitch": float(fields.get("GimbalPitchDegree", 0)),
            "roll": float(fields.get("GimbalRollDegree", 0)),
            "yaw": float(fields.get("GimbalYawDegree", 0)),
            "relative_altitude": float(fields.get("RelativeAltitude", 0)),
            "absolute_altitude": float(fields.get("AbsoluteAltitude", 0)),
        }
    except Exception:
        return None


def _fallback_extract_gps(filepath):
    """Minimal GPS extraction — used only if drone-pipeline not on path."""
    try:
        from PIL import Image
        img = Image.open(filepath)
        exif = img.getexif()
        if not exif:
            return None
        gps_info = exif.get_ifd(0x8825)
        if not gps_info:
            return None

        def dms_to_decimal(dms, ref):
            d, m, s = float(dms[0]), float(dms[1]), float(dms[2])
            dec = d + m / 60 + s / 3600
            if ref in ("S", "W"):
                dec = -dec
            return dec

        lat = dms_to_decimal(gps_info[2], gps_info[1])
        lon = dms_to_decimal(gps_info[4], gps_info[3])
        alt = float(gps_info.get(6, 0))
        return [lon, lat, alt]
    except Exception:
        return None


# ─── UNIFIED INTERFACE ──────────────────────────────────────────────────────

def get_gimbal_data(filepath):
    """Extract gimbal pitch/roll/yaw from a photo. Uses drone-pipeline if available."""
    if PIPELINE_AVAILABLE:
        return extract_xmp_gimbal(filepath)
    return _fallback_extract_xmp_gimbal(filepath)


def get_gps_data(filepath):
    """Extract GPS [lon, lat, alt] from a photo. Uses drone-pipeline if available."""
    if PIPELINE_AVAILABLE:
        return extract_gps_from_exif(filepath)
    return _fallback_extract_gps(filepath)


def get_platform(filepath):
    """Detect drone platform. Returns (platform, method) or (None, None)."""
    if PIPELINE_AVAILABLE:
        return detect_platform_from_file(filepath)
    return None, None


# ─── CLASSIFICATION ─────────────────────────────────────────────────────────

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".dng"}

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
class ClassificationResult:
    source_dir: str
    nadir_count: int = 0
    oblique_count: int = 0
    unknown_count: int = 0
    total: int = 0
    pitch_min: float = None
    pitch_max: float = None
    platform: str = None
    photos: list = field(default_factory=list)
    nadir_dir: str = ""
    oblique_dir: str = ""
    threshold: float = -70.0
    created_at: str = ""


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
    """Find all photo files in a directory (non-recursive, skips subdirs)."""
    source = Path(source_dir)
    photos = []
    for f in sorted(source.iterdir()):
        if f.is_file() and f.suffix.lower() in PHOTO_EXTENSIONS:
            photos.append(f)
    return photos


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

    return result


def sort_photos(result, copy=True, progress_callback=None):
    """Copy (or move) classified photos into nadir/oblique/unknown subfolders.

    Args:
        result: ClassificationResult from classify_photos()
        copy: If True, copy files. If False, move them.
        progress_callback: Optional callable(current, total, filename)

    Returns:
        Updated ClassificationResult with nadir_dir/oblique_dir paths set
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

    transfer = shutil.copy2 if copy else shutil.move

    for i, photo in enumerate(result.photos):
        if photo.classification == "nadir":
            dest = nadir_dir / photo.filename
        elif photo.classification == "oblique":
            dest = oblique_dir / photo.filename
        else:
            unknown_dir.mkdir(exist_ok=True)
            dest = unknown_dir / photo.filename

        transfer(photo.path, dest)

        if progress_callback and ((i + 1) % 100 == 0 or (i + 1) == len(result.photos)):
            progress_callback(i + 1, len(result.photos), photo.filename)

    return result


def write_manifest(result, output_path=None):
    """Write classification manifest to JSON.

    Args:
        result: ClassificationResult
        output_path: Where to write. Defaults to source_dir/manifest.json
    """
    if output_path is None:
        output_path = Path(result.source_dir) / "manifest.json"

    manifest = {
        "portfolio_maker_version": "1.0",
        "source_dir": result.source_dir,
        "created_at": result.created_at,
        "platform": result.platform,
        "threshold": result.threshold,
        "summary": {
            "total": result.total,
            "nadir": result.nadir_count,
            "oblique": result.oblique_count,
            "unknown": result.unknown_count,
        },
        "pitch_range": {
            "min": result.pitch_min,
            "max": result.pitch_max,
        },
        "output_dirs": {
            "nadir": result.nadir_dir,
            "oblique": result.oblique_dir,
        },
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
        description="Sentinel Portfolio Maker — Photo Classifier",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Sorts DJI drone photos into nadir (straight down) and oblique (angled) folders
based on gimbal pitch angle from EXIF/XMP metadata.

Examples:
  python photo_classifier.py D:\\DronePhotos\\JobSite1
  python photo_classifier.py D:\\DronePhotos\\JobSite1 --threshold -75
  python photo_classifier.py D:\\DronePhotos\\JobSite1 --dry-run
  python photo_classifier.py D:\\DronePhotos\\JobSite1 --metadata-only
  python photo_classifier.py D:\\DronePhotos\\JobSite1 --move
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
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger(__name__)

    source = os.path.abspath(args.source)
    if not os.path.isdir(source):
        sys.exit(f"Error: Directory not found: {source}")

    log.info(f"Pipeline modules: {'loaded from drone-pipeline' if PIPELINE_AVAILABLE else 'using fallback'}")
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

    # Summary
    log.info("")
    log.info(f"Platform:  {result.platform or 'unknown'}")
    log.info(f"Total:     {result.total} photos")
    log.info(f"Nadir:     {result.nadir_count}")
    log.info(f"Oblique:   {result.oblique_count}")
    if result.unknown_count:
        log.info(f"Unknown:   {result.unknown_count} (no pitch data)")
    if result.pitch_min is not None:
        log.info(f"Pitch:     {result.pitch_min:.1f} to {result.pitch_max:.1f} degrees")

    # Dry run or metadata-only — write manifest and stop
    if args.dry_run or args.metadata_only:
        if not args.no_manifest:
            manifest_path = write_manifest(result)
            log.info(f"\nManifest:  {manifest_path}")
        if args.dry_run:
            log.info("\n[DRY RUN] No files were copied.")
        return

    # Sort
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
