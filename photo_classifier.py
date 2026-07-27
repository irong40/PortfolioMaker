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
import re
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
from sentinel_core.spatial import haversine

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
    latitude: float = None
    longitude: float = None
    prestitched_path: str = ""
    viewer_path: str = ""
    viewer_error: str = ""
    status: str = "pending"
    source_type: str = "source_photos"


@dataclass
class TransferStats:
    """Summary statistics for a file transfer operation."""
    transferred: int = 0
    skipped: int = 0
    failed: int = 0
    renamed: int = 0  # collision resolved via _N suffix

    @property
    def total_attempted(self):
        return self.transferred + self.skipped + self.failed


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
    panorama_stragglers: list = field(default_factory=list)
    nadir_dir: str = ""
    oblique_dir: str = ""
    unknown_dir: str = ""
    panorama_dir: str = ""
    threshold: float = -70.0
    created_at: str = ""
    failed_transfers: list = field(default_factory=list)
    transfer_stats: TransferStats = field(default_factory=TransferStats)

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


OUTPUT_DIRS = {"nadir", "oblique", "unknown", "panorama"}

# Photos captured this far apart or closer are treated as one panorama
# position. Referenced by the report methodology text, so change it in one
# place only — report_generator interpolates this value.
PANORAMA_CLUSTER_RADIUS_M = 5.0

# Subfolder that side-deliverable panoramas are written to on the NodeODM and
# mipmap paths. gallery_filename_prefix() climbs out of it.
PANORAMA_SUBDIR = "panoramas"

# Minimum photos before a cluster counts as a deliverable panorama rather
# than a straggler.
PANORAMA_MIN_PHOTOS = 8


def scan_photos(source_dir):
    """Find all photo files in a directory tree (recursive).

    Skips output subdirectories created by sort_photos() (nadir/, oblique/,
    unknown/, panorama/) to avoid rescanning already-sorted output.
    """
    source = Path(source_dir)
    photos = []
    for root, dirs, files in os.walk(source):
        # Prune output directories so os.walk doesn't descend into them
        dirs[:] = [d for d in dirs if d.lower() not in OUTPUT_DIRS]
        for f in sorted(files):
            fpath = Path(root) / f
            if fpath.suffix.lower() in PHOTO_EXTENSIONS:
                photos.append(fpath)
    return sorted(photos)


def cluster_panorama_photos(located_photos, radius_m=PANORAMA_CLUSTER_RADIUS_M):
    """Group ``(path, latitude, longitude)`` records by GPS position.

    Input and output ordering is deterministic. Each new photo is compared to
    the running geographic centre of existing clusters using sentinel-core's
    haversine helper.
    """
    clusters = []
    for photo in sorted(located_photos, key=lambda item: str(item[0]).lower()):
        _, latitude, longitude = photo
        best = None
        best_distance = None
        for cluster in clusters:
            centre_lat = sum(item[1] for item in cluster) / len(cluster)
            centre_lon = sum(item[2] for item in cluster) / len(cluster)
            distance = haversine(latitude, longitude, centre_lat, centre_lon)
            if distance <= radius_m and (
                    best_distance is None or distance < best_distance):
                best = cluster
                best_distance = distance
        if best is None:
            clusters.append([photo])
        else:
            best.append(photo)
    return clusters


def _find_panorama_root(source_dir):
    """Return the DJI PANORAMA directory associated with ``source_dir``."""
    source = Path(source_dir)
    if source.is_dir() and source.name.lower() == "panorama":
        return source
    child = source / "PANORAMA"
    if child.is_dir():
        return child
    sibling = source.parent / "PANORAMA"
    if sibling.is_dir():
        return sibling
    return None


def find_prestitched_panoramas(source_dir):
    """Return likely DJI-generated equirectangular JPEGs."""
    try:
        from PIL import Image
    except ImportError:
        return []

    source = Path(source_dir)
    roots = []
    for root in (source, _find_panorama_root(source_dir)):
        if root is not None and root.is_dir() and root not in roots:
            roots.append(root)

    candidates = []
    for root in roots:
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg"}:
                continue
            stem = path.stem.lower()
            if "pano" not in stem and "panorama" not in stem:
                continue
            try:
                with Image.open(path) as image:
                    width, height = image.size
            except OSError as e:
                # Unreadable candidate silently vanishes otherwise, and the
                # operator has no way to see why their DJI pano was skipped.
                logging.getLogger(__name__).debug(
                    "Skipping unreadable panorama candidate %s: %s", path, e)
                continue
            if height > 0 and width / height >= 1.8:
                candidates.append(str(path))
    return sorted(set(candidates), key=str.lower)


def _nearest_set_within(candidate_lat, candidate_lon, panorama_sets, radius_m):
    """Return the closest located set within radius_m, or None."""
    nearby = [
        (haversine(candidate_lat, candidate_lon, ps.latitude, ps.longitude), ps)
        for ps in panorama_sets
        if ps.latitude is not None and ps.longitude is not None
    ]
    nearby = [(distance, ps) for distance, ps in nearby if distance <= radius_m]
    if not nearby:
        return None
    return min(nearby, key=lambda item: item[0])[1]


def _attach_by_gps(unmatched_sets, candidates, radius_m):
    """Bind candidates to their nearest set. Returns candidates left over.

    Mutates unmatched_sets: every set that takes a candidate is removed.
    """
    leftover = []
    for candidate in candidates:
        gps = get_gps_data(candidate)
        if not (gps and len(gps) >= 2
                and gps[0] is not None and gps[1] is not None):
            leftover.append(candidate)
            continue
        longitude, latitude = float(gps[0]), float(gps[1])
        matched = _nearest_set_within(
            latitude, longitude, unmatched_sets, radius_m)
        if matched is None:
            leftover.append(candidate)
            continue
        matched.prestitched_path = candidate
        unmatched_sets.remove(matched)
    return leftover


def _attach_by_filename_ids(unmatched_sets, candidates):
    """Fallback for candidates with no usable GPS: match on shared digit runs.

    A candidate binds only when exactly one set shares a digit run with it, so
    an ambiguous filename is left unmatched rather than guessed at. The
    single-set/single-candidate case is the one exception — with nothing else
    it could belong to, pairing them is unambiguous.
    """
    only_possible_pairing = len(unmatched_sets) == 1 and len(candidates) == 1
    for candidate in candidates:
        candidate_ids = set(re.findall(r"\d{3,}", Path(candidate).stem))
        matches = [
            ps for ps in unmatched_sets
            if candidate_ids & set(re.findall(r"\d{3,}", Path(ps.folder).name))
        ] if candidate_ids else []

        if len(matches) == 1:
            matches[0].prestitched_path = candidate
            unmatched_sets.remove(matches[0])
        elif only_possible_pairing:
            unmatched_sets[0].prestitched_path = candidate
            unmatched_sets.pop(0)


def attach_prestitched_panoramas(panorama_sets, candidates,
                                 radius_m=PANORAMA_CLUSTER_RADIUS_M):
    """Point each panorama set at its DJI pre-stitched JPEG, where one exists.

    Mutates panorama_sets in place (sets .prestitched_path). GPS proximity is
    tried first; filename digit runs are the fallback for candidates whose
    EXIF has no usable position.
    """
    unmatched_sets = list(panorama_sets)
    leftover = _attach_by_gps(
        unmatched_sets, sorted(candidates, key=str.lower), radius_m)
    _attach_by_filename_ids(unmatched_sets, leftover)


def _sets_for_folder(subfolder):
    """Return the panorama sets held by one DJI PANORAMA subfolder.

    A subfolder is normally one capture, so clustering happens WITHIN it and
    never across folders — pooling merges panoramas shot from the same launch
    point (two altitudes over one spot sit well inside the cluster radius)
    into a single unstitchable set. Clustering here still splits the rare
    folder that genuinely holds two positions.
    """
    photos = sorted(
        f for f in subfolder.iterdir()
        if f.is_file() and f.suffix.lower() in {".jpg", ".jpeg"}
    )
    if not photos:
        return []

    located = []
    for photo in photos:
        gps = get_gps_data(str(photo))
        if gps and len(gps) >= 2 and gps[0] is not None and gps[1] is not None:
            located.append((str(photo), float(gps[1]), float(gps[0])))

    if not located:
        return [PanoramaSet(
            folder=str(subfolder),
            photo_count=len(photos),
            photos=[str(path) for path in photos],
        )]

    folder_sets = [
        PanoramaSet(
            folder=str(subfolder),
            photo_count=len(cluster),
            photos=[item[0] for item in cluster],
            latitude=sum(item[1] for item in cluster) / len(cluster),
            longitude=sum(item[2] for item in cluster) / len(cluster),
        )
        for cluster in cluster_panorama_photos(located)
    ]

    # Untagged photos belong to the same capture as their folder-mates. Attach
    # them to the largest set rather than emitting a duplicate set.
    located_paths = {item[0] for item in located}
    untagged = [str(p) for p in photos if str(p) not in located_paths]
    if untagged:
        largest = max(folder_sets, key=lambda ps: ps.photo_count)
        largest.photos = sorted(largest.photos + untagged, key=str.lower)
        largest.photo_count = len(largest.photos)

    return folder_sets


def _panorama_sort_key(panorama_set):
    """Order sets by position, with unlocated sets last, always deterministic."""
    return (
        panorama_set.latitude is None,
        panorama_set.latitude if panorama_set.latitude is not None else 0.0,
        panorama_set.longitude if panorama_set.longitude is not None else 0.0,
        panorama_set.photos[0].lower() if panorama_set.photos
        else panorama_set.folder.lower(),
    )


def scan_panorama_sets(source_dir, min_photos=PANORAMA_MIN_PHOTOS):
    """Return ``(valid_sets, straggler_sets)`` for a panorama source."""
    pano_dir = _find_panorama_root(source_dir)
    if pano_dir is None:
        return [], []

    candidates = []
    for subfolder in sorted(pano_dir.iterdir()):
        if subfolder.is_dir():
            candidates.extend(_sets_for_folder(subfolder))

    valid_sets = []
    stragglers = []
    for candidate in sorted(candidates, key=_panorama_sort_key):
        if candidate.photo_count >= min_photos:
            valid_sets.append(candidate)
        else:
            candidate.status = "skipped_straggler"
            stragglers.append(candidate)

    attach_prestitched_panoramas(
        valid_sets, find_prestitched_panoramas(source_dir))
    return valid_sets, stragglers


def scan_panoramas(source_dir, min_photos=PANORAMA_MIN_PHOTOS):
    """Return valid panorama sets from the selected DJI photo layout."""
    valid_sets, _ = scan_panorama_sets(source_dir, min_photos=min_photos)
    return valid_sets


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
    pano_sets, pano_stragglers = scan_panorama_sets(source_dir)
    result.panorama_sets = pano_sets
    result.panorama_stragglers = pano_stragglers
    result.panorama_count = len(pano_sets)

    return result


def _resolve_collision(dest):
    """Return a non-colliding path by appending _1, _2, etc."""
    if not dest.exists():
        return dest, False
    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    for i in range(1, 10000):
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate, True
    # Extremely unlikely — fall through
    return dest, False


def _write_transfer_journal(journal_path, entries):
    """Write a JSON transfer journal for diagnosing partial failures."""
    with open(journal_path, "w") as f:
        json.dump({
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "transfers": entries,
        }, f, indent=2)


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
    stats = TransferStats()
    journal_entries = []

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
            dest, was_renamed = _resolve_collision(dest)
            if was_renamed:
                stats.renamed += 1
                log.info(f"Collision resolved: {photo.filename} -> {dest.name}")
            transfer(photo.path, dest)
            stats.transferred += 1
            journal_entries.append({
                "source": photo.path,
                "destination": str(dest),
                "status": "ok",
                "renamed": was_renamed,
            })
        except (OSError, shutil.Error) as e:
            log.error(f"Failed to {'copy' if copy else 'move'} {photo.filename}: {e}")
            result.failed_transfers.append((photo.filename, str(e)))
            stats.failed += 1
            journal_entries.append({
                "source": photo.path,
                "destination": str(dest),
                "status": "failed",
                "error": str(e),
            })

        if progress_callback and ((i + 1) % 100 == 0 or (i + 1) == len(result.photos)):
            progress_callback(i + 1, len(result.photos), photo.filename)

    result.transfer_stats = stats

    # Write transfer journal for diagnosing partial failures
    journal_path = source / "transfer_journal.json"
    try:
        _write_transfer_journal(journal_path, journal_entries)
    except OSError as e:
        log.warning(f"Could not write transfer journal: {e}")

    if result.failed_transfers:
        log.warning(f"{stats.failed} file(s) failed to transfer")
    log.info(f"Transfer complete: {stats.transferred} transferred, "
             f"{stats.renamed} renamed, {stats.failed} failed")

    # Stitch panoramas if any detected
    if result.panorama_sets:
        panorama_out = source / "panorama"
        panorama_out.mkdir(exist_ok=True)
        result.panorama_dir = str(panorama_out)
        stitch_panoramas(result.panorama_sets, str(panorama_out),
                         progress_callback=progress_callback)

    return result


# Per-set stitch timeout. cv2.Stitcher on a 34-photo sphere set normally
# finishes in a few minutes; past this it has hung or is thrashing swap.
STITCH_TIMEOUT_SECONDS = 900

_WORKER_PATH = Path(__file__).resolve().parent / "pano_stitch_worker.py"
PANNELLUM_ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "pannellum"


def generate_panorama_viewer(panorama_path, output_dir=None, title="",
                             assets_dir=None):
    """Generate an offline Pannellum viewer for one panorama image."""
    panorama_path = Path(panorama_path)
    output = Path(output_dir) if output_dir else panorama_path.parent
    output.mkdir(parents=True, exist_ok=True)
    assets_source = Path(assets_dir) if assets_dir else PANNELLUM_ASSETS_DIR

    target_panorama = output / panorama_path.name
    if panorama_path.resolve() != target_panorama.resolve():
        shutil.copy2(panorama_path, target_panorama)

    assets_output = output / "assets"
    assets_output.mkdir(parents=True, exist_ok=True)
    for asset_name in ("pannellum.js", "pannellum.css", "LICENSE.txt"):
        source_asset = assets_source / asset_name
        if not source_asset.is_file():
            raise FileNotFoundError(f"Missing vendored Pannellum asset: {source_asset}")
        shutil.copy2(source_asset, assets_output / asset_name)

    config = json.dumps({
        "type": "equirectangular",
        "panorama": target_panorama.name,
        "autoLoad": True,
        "showFullscreenCtrl": True,
        "title": title or target_panorama.stem,
    }, indent=2, ensure_ascii=True)
    config = (config.replace("<", "\\u003c")
                    .replace(">", "\\u003e")
                    .replace("&", "\\u0026"))
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Panorama Viewer</title>
  <link rel="stylesheet" href="assets/pannellum.css">
  <script src="assets/pannellum.js"></script>
  <style>
    html, body, #panorama {{ width: 100%; height: 100%; margin: 0; background: #111; }}
  </style>
</head>
<body>
  <div id="panorama"></div>
  <script>
    pannellum.viewer("panorama", {config});
  </script>
</body>
</html>
"""
    viewer_path = output / f"{target_panorama.stem}.html"
    viewer_path.write_text(html, encoding="utf-8", newline="\n")
    return str(viewer_path)


def write_panorama_launcher(output_dir, first_viewer, port=8765):
    """Write the Windows helper that serves locally vendored viewer files.

    Args:
        output_dir: Folder holding the viewers; the .bat lands here
        first_viewer: Path (or bare filename) of the viewer to open
        port: Loopback port for the throwaway static server

    This ships to clients, so it cannot assume Python is installed. Pannellum
    needs a real HTTP origin — opening the .html off the filesystem fails on
    the texture load — so there is no silent fallback, only a clear message.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    viewer_name = Path(first_viewer).name
    content = (
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "where python >nul 2>nul\r\n"
        "if errorlevel 1 (\r\n"
        "  echo Python was not found on this PC.\r\n"
        "  echo.\r\n"
        "  echo The 360 viewer needs a local web server to load the panorama.\r\n"
        "  echo Install Python from https://python.org and run this file again,\r\n"
        "  echo or reply to your delivery email and we will send a hosted link.\r\n"
        "  echo.\r\n"
        "  pause\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        f"start \"\" \"http://127.0.0.1:{port}/{viewer_name}\"\r\n"
        f"python -m http.server {port} --bind 127.0.0.1\r\n"
    )
    launcher_path = output / "view_panoramas.bat"
    launcher_path.write_text(content, encoding="utf-8", newline="")
    return str(launcher_path)


def _stitch_one_set(ps, output_path):
    """Stitch a single PanoramaSet in a worker subprocess.

    cv2.Stitcher peaks at several GB per set and CPython/OpenCV never
    return that heap to the OS, so the work runs in a short-lived
    subprocess and the memory comes back when it exits.

    Returns a result dict: {"ok": bool, "error"/"width"/"height": ...}
    """
    import subprocess
    import tempfile

    job = {
        "photos": ps.photos,
        "output_path": str(output_path),
        "max_width": 2000,
    }
    job_file = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as f:
            json.dump(job, f)
            job_file = f.name

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run(
            [sys.executable, str(_WORKER_PATH), job_file],
            capture_output=True, text=True,
            timeout=STITCH_TIMEOUT_SECONDS,
            creationflags=creationflags,
        )
        try:
            return json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return {"ok": False,
                    "error": f"stitch worker crashed (exit {proc.returncode})"}
    except subprocess.TimeoutExpired:
        return {"ok": False,
                "error": f"stitch timed out after {STITCH_TIMEOUT_SECONDS}s"}
    except OSError as e:
        return {"ok": False, "error": f"could not start stitch worker: {e}"}
    finally:
        if job_file:
            try:
                os.unlink(job_file)
            except OSError:
                pass


def gallery_filename_prefix(output_dir):
    """Return a per-job filename prefix for copies into the shared gallery.

    Panoramas are named set-NNN.jpg inside a job's own output folder, which is
    unambiguous there but collides in PANO_GALLERY — every job would write
    set-001.jpg over the last one. Job output dirs are named
    <site>_<job_type>_<date>, which makes the gallery copy unique per job.
    """
    folder = Path(output_dir)
    name = folder.name
    if name.lower() == PANORAMA_SUBDIR:  # nodeodm/mipmap jobs nest panoramas/
        name = folder.parent.name
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")
    return slug or "job"


def _produce_panorama_image(ps, output_path, log):
    """Write one panorama to output_path. Returns True when an image exists.

    Prefers the DJI pre-stitched JPEG and falls back to the OpenCV worker,
    recording status/source_type/stitch_error on the set either way.
    """
    if ps.prestitched_path:
        try:
            shutil.copy2(ps.prestitched_path, output_path)
            ps.stitched_path = str(output_path)
            ps.status = ps.source_type = "dji_prestitched"
            log.info(f"  Copied DJI panorama: {output_path.name}")
            return True
        except (OSError, shutil.Error) as e:
            log.warning(f"  DJI panorama copy failed, stitching sources: {e}")

    result = _stitch_one_set(ps, output_path)
    if not result.get("ok"):
        ps.stitch_error = result.get("error", "unknown error")
        ps.status = "failed"
        log.warning(f"  Failed {Path(ps.folder).name}: {ps.stitch_error}")
        if ps.stitch_error == "OpenCV not installed":
            log.warning("OpenCV not installed — continuing to check later "
                        "sets for DJI pre-stitched panoramas")
        return False

    ps.stitched_path = str(output_path)
    ps.status = ps.source_type = "opencv_stitched"
    log.info(f"  Saved: {output_path.name} "
             f"({result['width']}x{result['height']})")
    return True


def _copy_to_gallery(output_path, pano_gallery, prefix, log):
    """Copy a finished panorama into the central gallery for DroneInvoice."""
    gallery_path = pano_gallery / f"{prefix}_{output_path.name}"
    try:
        pano_gallery.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(output_path), str(gallery_path))
        log.info(f"  Copied to gallery: {gallery_path}")
    except OSError as e:
        log.warning(f"  Gallery copy failed: {e}")


def stitch_panoramas(panorama_sets, output_dir, progress_callback=None,
                     site_name=""):
    """Stitch each panorama set and save to output_dir.

    Each set is stitched in its own subprocess (see _stitch_one_set) so
    OpenCV's multi-GB peak is isolated and returned to the OS per set.

    Args:
        panorama_sets: List of PanoramaSet objects
        output_dir: Folder to save stitched panoramas
        progress_callback: Optional callable(current, total, filename)
        site_name: Optional site label used in viewer titles
    """
    log = logging.getLogger(__name__)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    prefix = gallery_filename_prefix(out)
    pano_gallery = Path(os.environ.get("PANO_GALLERY", r"E:\Portfolio\_Panoramas"))
    total = len(panorama_sets)

    for i, ps in enumerate(panorama_sets):
        folder_name = Path(ps.folder).name
        log.info(f"Stitching panorama {i + 1}/{total}: {folder_name} ({ps.photo_count} photos)")

        output_path = out / f"set-{i + 1:03d}.jpg"
        if _produce_panorama_image(ps, output_path, log):
            _copy_to_gallery(output_path, pano_gallery, prefix, log)
            title = (f"{site_name} Panorama {i + 1}" if site_name
                     else f"Panorama {i + 1}")
            try:
                ps.viewer_path = generate_panorama_viewer(
                    output_path, output_dir=out, title=title)
            except (OSError, ValueError) as e:
                ps.viewer_error = str(e)
                log.warning(f"  Viewer generation failed: {e}")

        # Fires on failures too — a stalled bar is worse than an honest one.
        if progress_callback:
            progress_callback(i + 1, total, folder_name)

    viewers = [ps.viewer_path for ps in panorama_sets if ps.viewer_path]
    if viewers:
        try:
            write_panorama_launcher(out, viewers[0])
        except OSError as e:
            log.warning(f"  Panorama launcher generation failed: {e}")


    # ─── CLIENT PROFILE SYSTEM ─────────────────────────────────────────────────


PROFILES_DIR = Path(__file__).resolve().parent / "profiles"

METERS_PER_FOOT = 0.3048


def load_profile(name):
    """Load a client profile JSON by name (without .json extension)."""
    path = PROFILES_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Profile not found: {path}")
    with open(path, "r") as f:
        return json.load(f)


def list_profiles():
    """Return list of (filename_stem, display_name) for all available profiles."""
    profiles = []
    if PROFILES_DIR.is_dir():
        for p in sorted(PROFILES_DIR.glob("*.json")):
            try:
                with open(p, "r") as f:
                    data = json.load(f)
                profiles.append((p.stem, data.get("name", p.stem)))
            except (json.JSONDecodeError, OSError):
                profiles.append((p.stem, p.stem))
    return profiles


@dataclass
class ProfileCategory:
    """A single category within a client profile classification."""
    name: str
    label: str
    photos: list = field(default_factory=list)
    min_count: int = 0
    max_count: int = 0  # 0 = unlimited

    @property
    def met(self):
        if self.min_count and len(self.photos) < self.min_count:
            return False
        return True


@dataclass
class ProfileResult:
    """Result of classifying photos against a client profile."""
    profile_name: str
    categories: list = field(default_factory=list)  # list of ProfileCategory
    unmatched: list = field(default_factory=list)    # PhotoMeta not matching any rule
    total: int = 0
    all_met: bool = False
    validation_errors: list = field(default_factory=list)


def _alt_meters(photo):
    """Get relative altitude in meters, preferring relative_altitude over GPS altitude."""
    if photo.relative_altitude is not None:
        return photo.relative_altitude
    return None


# ─── COMPASS DIRECTION LOGIC ────────────────────────────────────────────────

# Cardinal directions in degrees (clockwise from North)
COMPASS_DIRECTIONS = {
    "N": 0, "NE": 45, "E": 90, "SE": 135,
    "S": 180, "SW": 225, "W": 270, "NW": 315,
}

# Relative directions mapped from yaw relative to front-facing direction
# Front = facing the structure, Back = behind it, etc.
RELATIVE_SIDES = ["front", "right", "back", "left"]


def normalize_angle(angle):
    """Normalize an angle to 0-360 range."""
    return angle % 360


def yaw_to_relative_side(yaw, front_bearing):
    """Convert a photo's yaw to a relative side of the structure.

    The photo's yaw tells us which compass direction the camera is pointing.
    A photo pointing AT the front of the house means the camera is facing
    the opposite direction of front_bearing (camera faces the wall).

    Actually, for drone inspection photos, the yaw is where the camera
    is looking FROM, toward the structure. So:
    - If front of house faces South (180°), a photo taken from the South
      looking North (yaw ~0°/360°) is a FRONT photo.
    - A photo taken from the North looking South (yaw ~180°) is a BACK photo.

    We compute: relative_angle = (yaw - front_bearing + 180) % 360
    Then map to quadrants: 0=front, 90=right, 180=back, 270=left

    Args:
        yaw: Camera yaw in degrees (0-360, 0=North)
        front_bearing: Compass bearing the front of the structure faces (0-360)

    Returns:
        One of: "front", "front_right", "right", "back_right",
                "back", "back_left", "left", "front_left"
    """
    if yaw is None:
        return "unknown"

    # The camera points toward the structure. If the front faces South (180),
    # a camera at yaw 0 (pointing North) is shooting the front.
    # relative = how far clockwise the camera position is from the front.
    relative = normalize_angle(yaw - front_bearing + 180)

    # 8 sectors of 45 degrees each
    sector = int((relative + 22.5) % 360 / 45)
    sides_8 = ["front", "front_right", "right", "back_right",
               "back", "back_left", "left", "front_left"]
    return sides_8[sector]


def yaw_to_quadrant(yaw, front_bearing):
    """Map photo yaw to which side of the structure the camera sees.

    Drone yaw = direction camera points. The camera faces the structure.
    If the front of the house faces South (180°):
      - Camera yaw 0° (pointing North) → camera is South of house, seeing FRONT
      - Camera yaw 90° (pointing East) → camera is West of house, seeing RIGHT
        (standing at front door looking out, the right side is to the West)
      - Camera yaw 180° (pointing South) → camera is North of house, seeing BACK
      - Camera yaw 270° (pointing West) → camera is East of house, seeing LEFT

    The camera position is opposite its yaw. Relative position of camera
    around the structure: camera_pos = (yaw + 180) % 360
    Then we find which side that corresponds to when facing the front.

    Args:
        yaw: Camera yaw in degrees (direction camera points)
        front_bearing: Compass bearing the front of the structure faces

    Returns:
        One of: "front", "right", "back", "left"
    """
    if yaw is None:
        return "unknown"

    # Camera position is opposite its pointing direction
    camera_position = normalize_angle(yaw + 180)
    # How far clockwise is the camera from the front-facing direction?
    relative = normalize_angle(camera_position - front_bearing)
    sector = int((relative + 45) % 360 / 90)
    return RELATIVE_SIDES[sector]


def compass_to_bearing(direction):
    """Convert a compass direction string to degrees.

    Args:
        direction: "N", "NE", "E", "SE", "S", "SW", "W", "NW" or a number

    Returns:
        float bearing in degrees (0-360)
    """
    direction = str(direction).strip().upper()
    if direction in COMPASS_DIRECTIONS:
        return float(COMPASS_DIRECTIONS[direction])
    try:
        return float(direction) % 360
    except ValueError:
        raise ValueError(f"Invalid compass direction: {direction}")


def _matches_category(photo, cat_def, front_bearing=None):
    """Check if a photo matches a category definition's pitch/altitude/direction rules."""
    pitch = photo.pitch
    alt_m = _alt_meters(photo)

    # Pitch filter
    pitch_min = cat_def.get("pitch_min")
    pitch_max = cat_def.get("pitch_max")
    if pitch_min is not None or pitch_max is not None:
        if pitch is None:
            return False
        if pitch_min is not None and pitch < pitch_min:
            return False
        if pitch_max is not None and pitch > pitch_max:
            return False

    # Altitude filter (specified in feet in the profile, compared in meters)
    alt_min_ft = cat_def.get("alt_min_ft")
    alt_max_ft = cat_def.get("alt_max_ft")
    if alt_min_ft is not None or alt_max_ft is not None:
        if alt_m is None:
            return False
        alt_ft = alt_m / METERS_PER_FOOT
        if alt_min_ft is not None and alt_ft < alt_min_ft:
            return False
        if alt_max_ft is not None and alt_ft > alt_max_ft:
            return False

    # Direction filter (requires front_bearing)
    direction = cat_def.get("direction")
    if direction is not None:
        if front_bearing is None or photo.yaw is None:
            return False
        quadrant = yaw_to_quadrant(photo.yaw, front_bearing)
        if quadrant != direction:
            return False

    return True


def classify_with_profile(result, profile, front_bearing=None):
    """Re-classify an existing ClassificationResult using a client profile.

    Photos are assigned to the first matching category in order.
    Photos that match no category go into 'unmatched'.

    Args:
        result: ClassificationResult from classify_photos()
        profile: dict loaded from a profile JSON
        front_bearing: Compass bearing (0-360) the front of the structure faces.
                       Required for profiles with directional categories.
                       Can also be a string like "N", "SE", etc.

    Returns:
        ProfileResult with categories, counts, and validation status
    """
    # Convert front_bearing from string if needed
    if front_bearing is not None and isinstance(front_bearing, str):
        front_bearing = compass_to_bearing(front_bearing)

    cat_defs = profile.get("categories", [])
    categories = []
    for cd in cat_defs:
        categories.append(ProfileCategory(
            name=cd["name"],
            label=cd.get("label", cd["name"]),
            min_count=cd.get("min_count", 0),
            max_count=cd.get("max_count", 0),
        ))

    # Filter out excluded extensions if profile specifies them
    exclude_exts = {e.lower() for e in profile.get("exclude_extensions", [])}
    if exclude_exts:
        photos = [p for p in result.photos if p.path.suffix.lower() not in exclude_exts]
    else:
        photos = result.photos

    unmatched = []

    for photo in photos:
        placed = False
        for i, cd in enumerate(cat_defs):
            if _matches_category(photo, cd, front_bearing=front_bearing):
                categories[i].photos.append(photo)
                placed = True
                break
        if not placed:
            unmatched.append(photo)

    # Sort bird's-eye categories by yaw if requested
    for i, cd in enumerate(cat_defs):
        if cd.get("sort_by_yaw") and categories[i].photos:
            categories[i].photos.sort(
                key=lambda p: p.yaw if p.yaw is not None else 999)

    # Validate counts
    errors = []
    for cat in categories:
        if cat.min_count and len(cat.photos) < cat.min_count:
            errors.append(
                f"{cat.label}: {len(cat.photos)}/{cat.min_count} "
                f"(need {cat.min_count - len(cat.photos)} more)")
        if cat.max_count and len(cat.photos) > cat.max_count:
            errors.append(
                f"{cat.label}: {len(cat.photos)}/{cat.max_count} "
                f"(have {len(cat.photos) - cat.max_count} extra)")

    pr = ProfileResult(
        profile_name=profile.get("name", "Unknown"),
        categories=categories,
        unmatched=unmatched,
        total=len(photos),
        all_met=len(errors) == 0,
        validation_errors=errors,
    )
    return pr


def sort_with_profile(result, profile, output_dir, site_name="",
                      copy=True, progress_callback=None, front_bearing=None):
    """Sort and rename photos according to a client profile.

    Args:
        result: ClassificationResult from classify_photos()
        profile: dict loaded from a profile JSON
        output_dir: Destination folder
        site_name: Site/address name for rename pattern
        copy: If True copy, if False move
        progress_callback: Optional callable(current, total, filename)
        front_bearing: Compass bearing for the front of the structure.
                       Required for profiles with directional categories.

    Returns:
        ProfileResult with categories and transfer info
    """
    log = logging.getLogger(__name__)
    pr = classify_with_profile(result, profile, front_bearing=front_bearing)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    transfer = shutil.copy2 if copy else shutil.move

    rename_pattern = profile.get("rename_pattern", "{site}_{category}_{seq:03d}")
    output_structure = profile.get("output_structure", "flat")

    total_photos = sum(len(c.photos) for c in pr.categories) + len(pr.unmatched)
    done = 0

    # Clean site name for filenames
    safe_site = site_name.replace(" ", "_").replace("/", "-").replace("\\", "-")

    for cat in pr.categories:
        if output_structure == "by_category":
            cat_dir = out / cat.name
            cat_dir.mkdir(exist_ok=True)
        else:
            cat_dir = out

        for seq, photo in enumerate(cat.photos, 1):
            ext = Path(photo.filename).suffix.lower()
            new_name = rename_pattern.format(
                site=safe_site,
                category=cat.name,
                seq=seq,
            )
            if not new_name.endswith(ext):
                new_name += ext

            dest = cat_dir / new_name
            dest, _ = _resolve_collision(dest)
            try:
                transfer(photo.path, dest)
            except (OSError, shutil.Error) as e:
                log.error(f"Failed to transfer {photo.filename}: {e}")

            done += 1
            if progress_callback and (done % 50 == 0 or done == total_photos):
                progress_callback(done, total_photos, new_name)

    # Copy unmatched to separate folder
    if pr.unmatched:
        unmatched_dir = out / "_unmatched"
        unmatched_dir.mkdir(exist_ok=True)
        for photo in pr.unmatched:
            dest = unmatched_dir / photo.filename
            dest, _ = _resolve_collision(dest)
            try:
                transfer(photo.path, dest)
            except (OSError, shutil.Error) as e:
                log.error(f"Failed to transfer unmatched {photo.filename}: {e}")
            done += 1

    return pr


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
    stats = TransferStats()
    journal_entries = []

    for i, photo in enumerate(result.photos):
        dest = out / photo.filename
        try:
            dest, was_renamed = _resolve_collision(dest)
            if was_renamed:
                stats.renamed += 1
                log.info(f"Collision resolved: {photo.filename} -> {dest.name}")
            transfer(photo.path, dest)
            stats.transferred += 1
            journal_entries.append({
                "source": photo.path,
                "destination": str(dest),
                "status": "ok",
                "renamed": was_renamed,
            })
        except (OSError, shutil.Error) as e:
            log.error(f"Failed to export {photo.filename}: {e}")
            result.failed_transfers.append((photo.filename, str(e)))
            stats.failed += 1
            journal_entries.append({
                "source": photo.path,
                "destination": str(dest),
                "status": "failed",
                "error": str(e),
            })

        if progress_callback and ((i + 1) % 100 == 0 or (i + 1) == len(result.photos)):
            progress_callback(i + 1, len(result.photos), photo.filename)

    result.transfer_stats = stats

    # Write transfer journal for diagnosing partial failures
    journal_path = out / "transfer_journal.json"
    try:
        _write_transfer_journal(journal_path, journal_entries)
    except OSError as e:
        log.warning(f"Could not write transfer journal: {e}")

    if result.failed_transfers:
        log.warning(f"{stats.failed} file(s) failed to export")
    log.info(f"Export complete: {stats.transferred} transferred, "
             f"{stats.renamed} renamed, {stats.failed} failed")

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
            "panorama_straggler_photos": sum(
                ps.photo_count for ps in result.panorama_stragglers),
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
                "latitude": ps.latitude,
                "longitude": ps.longitude,
                "prestitched_path": ps.prestitched_path,
                "stitched_path": ps.stitched_path,
                "stitch_error": ps.stitch_error,
                "viewer_path": ps.viewer_path,
                "viewer_error": ps.viewer_error,
                "status": ps.status,
                "source_type": ps.source_type,
            }
            for ps in result.panorama_sets
        ],
        "panorama_stragglers": [
            {
                "folder": ps.folder,
                "photo_count": ps.photo_count,
                "latitude": ps.latitude,
                "longitude": ps.longitude,
                "status": ps.status,
            }
            for ps in result.panorama_stragglers
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
