"""
Sortie — Lightroom Classic Auto-QA Bridge

Bridges the gap between Sortie's photo sorting and WebODM processing by
inserting a Lightroom Classic QA step. Photos flow through three stages:

    1. push_to_lightroom()  — Copy nadir photos to a Lightroom watched folder
    2. (Manual step)        — User processes in Lightroom Classic
    3. pull_from_lightroom() — Pick up exports, rename, move to processing folder

Lightroom Classic Auto Import Setup
====================================
1. Open Lightroom Classic > File > Auto Import > Auto Import Settings...
2. Set "Watched Folder" to: E:\\DroneWorkflow\\LightroomWatch\\{site_name}
   (or your configured lightroom_watch_dir + site subfolder)
3. Set "Destination" to your Lightroom catalog's normal import location.
4. Optionally assign a Develop preset on import (recommended:
   exposure normalization, lens correction, sharpening).
5. Enable Auto Import: File > Auto Import > Enable Auto Import.

Lightroom QA Workflow
=====================
Once photos are imported:
- Review in Library grid view (press G).
- Flag rejects with the X key (blurry, over/underexposed, obstructed).
- Select all picks/unflagged (Ctrl+A after filtering).
- Export keepers: File > Export to E:\\DroneWorkflow\\LightroomExport\\{site_name}
  with JPEG quality 90-100, no resize, sRGB color space.

After export, run pull_from_lightroom() to pick up the keepers.

Usage (as module):
    from lightroom_bridge import push_to_lightroom, pull_from_lightroom, get_qa_status

    # Stage 1: After Sortie sorts photos
    result = push_to_lightroom("E:/DroneWorkflow/Sorted/JobSite1", "JobSite1")

    # Stage 2: User processes in Lightroom Classic (manual)

    # Stage 3: Pick up exports
    qa = pull_from_lightroom("E:/DroneWorkflow/LightroomExport/JobSite1", "JobSite1")
    print(f"Rejection rate: {qa['rejection_rate']:.1%}")
"""

import os
import json
import shutil
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── CONSTANTS ───────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = SCRIPT_DIR / "sortie_settings.json"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".dng", ".arw", ".cr2", ".nef"}

DEFAULT_WATCH_DIR = r"E:\DroneWorkflow\LightroomWatch"
DEFAULT_EXPORT_DIR = r"E:\DroneWorkflow\LightroomExport"
DEFAULT_PROCESSING_DIR = r"E:\DroneWorkflow\ProcessingInput"


# ─── SETTINGS ────────────────────────────────────────────────────────────────

def _load_settings() -> dict:
    """Load lightroom bridge settings from sortie_settings.json."""
    try:
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _get_setting(key: str, default: str) -> str:
    """Get a single setting value with fallback."""
    settings = _load_settings()
    return settings.get(key, default)


def get_watch_dir() -> str:
    return _get_setting("lightroom_watch_dir", DEFAULT_WATCH_DIR)


def get_export_dir() -> str:
    return _get_setting("lightroom_export_dir", DEFAULT_EXPORT_DIR)


def get_processing_dir() -> str:
    return _get_setting("processing_input_dir", DEFAULT_PROCESSING_DIR)


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _is_image(path: Path) -> bool:
    """Check if a file has a recognized image extension."""
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _list_images(directory: Path) -> list[Path]:
    """List image files in a directory, sorted by name for deterministic order."""
    if not directory.is_dir():
        return []
    return sorted(
        [p for p in directory.iterdir() if p.is_file() and _is_image(p)],
        key=lambda p: p.name.lower(),
    )


# ─── STAGE 1: PUSH TO LIGHTROOM ─────────────────────────────────────────────

def push_to_lightroom(
    sorted_dir: str,
    site_name: str,
    watch_dir: Optional[str] = None,
) -> dict:
    """Copy nadir photos from sorted output to Lightroom auto-import folder.

    Args:
        sorted_dir: Path to the sorted photo directory (should contain images
                     or a 'nadir' subfolder).
        site_name:  Site/job name used as a subfolder under the watch dir.
        watch_dir:  Override for the Lightroom watched folder root.
                    Defaults to the value in sortie_settings.json or
                    E:\\DroneWorkflow\\LightroomWatch.

    Returns:
        dict with keys: watch_dir, photo_count, site_name
    """
    sorted_path = Path(sorted_dir)

    # Prefer nadir subfolder if it exists, otherwise use the dir as-is
    nadir_path = sorted_path / "nadir"
    source = nadir_path if nadir_path.is_dir() else sorted_path

    images = _list_images(source)
    if not images:
        logger.warning("No images found in %s", source)
        return {
            "watch_dir": str(source),
            "photo_count": 0,
            "site_name": site_name,
        }

    dest_root = Path(watch_dir) if watch_dir else Path(get_watch_dir())
    dest = dest_root / site_name
    dest.mkdir(parents=True, exist_ok=True)

    copied = 0
    for img in images:
        dst_file = dest / img.name
        shutil.copy2(str(img), str(dst_file))
        copied += 1
        logger.debug("Copied %s -> %s", img.name, dst_file)

    logger.info(
        "Pushed %d photos for site '%s' to %s", copied, site_name, dest
    )

    return {
        "watch_dir": str(dest),
        "photo_count": copied,
        "site_name": site_name,
    }


# ─── STAGE 3: PULL FROM LIGHTROOM ───────────────────────────────────────────

def pull_from_lightroom(
    export_dir: str,
    site_name: str,
    output_dir: Optional[str] = None,
) -> dict:
    """Pick up Lightroom exports, rename with consistent pattern, move to
    the processing input folder.

    Files are renamed to {site_name}_{sequence:04d}.{ext} in alphabetical
    order by original filename, ensuring deterministic numbering.

    Args:
        export_dir: Path where Lightroom exported the keepers.
        site_name:  Site/job name used in the rename pattern and as a
                    subfolder under the processing input dir.
        output_dir: Override for the processing input root folder.
                    Defaults to the value in sortie_settings.json or
                    E:\\DroneWorkflow\\ProcessingInput.

    Returns:
        dict with keys: output_dir, total_in, keepers, rejects,
                        rejection_rate, renamed_files
    """
    export_path = Path(export_dir)
    keepers = _list_images(export_path)

    dest_root = Path(output_dir) if output_dir else Path(get_processing_dir())
    dest = dest_root / site_name
    dest.mkdir(parents=True, exist_ok=True)

    # Count how many we originally pushed (from watch dir)
    watch_root = Path(get_watch_dir())
    watch_site = watch_root / site_name
    total_in = len(_list_images(watch_site)) if watch_site.is_dir() else 0

    # If we can't determine total_in from watch dir, use keepers count
    # (user may have cleaned up the watch folder)
    if total_in == 0:
        total_in = len(keepers)

    renamed_files = []
    for seq, img in enumerate(keepers, start=1):
        ext = img.suffix.lower()
        new_name = f"{site_name}_{seq:04d}{ext}"
        dst_file = dest / new_name
        shutil.copy2(str(img), str(dst_file))
        renamed_files.append(new_name)
        logger.debug("Renamed %s -> %s", img.name, new_name)

    keeper_count = len(keepers)
    reject_count = max(0, total_in - keeper_count)
    rejection_rate = reject_count / total_in if total_in > 0 else 0.0

    logger.info(
        "Pulled %d keepers for site '%s' (%.1f%% rejection rate)",
        keeper_count,
        site_name,
        rejection_rate * 100,
    )

    return {
        "output_dir": str(dest),
        "total_in": total_in,
        "keepers": keeper_count,
        "rejects": reject_count,
        "rejection_rate": rejection_rate,
        "renamed_files": renamed_files,
    }


# ─── STATUS CHECK ────────────────────────────────────────────────────────────

def get_qa_status(
    site_name: str,
    watch_dir: Optional[str] = None,
    export_dir: Optional[str] = None,
) -> dict:
    """Check how many photos are waiting in each stage of the QA pipeline.

    Args:
        site_name:  Site/job name.
        watch_dir:  Override for the Lightroom watched folder root.
        export_dir: Override for the Lightroom export folder root.

    Returns:
        dict with keys: site_name, watch_count, export_count,
                        processing_count, stage (current pipeline stage)
    """
    watch_root = Path(watch_dir) if watch_dir else Path(get_watch_dir())
    export_root = Path(export_dir) if export_dir else Path(get_export_dir())
    proc_root = Path(get_processing_dir())

    watch_count = len(_list_images(watch_root / site_name))
    export_count = len(_list_images(export_root / site_name))
    processing_count = len(_list_images(proc_root / site_name))

    # Determine current stage
    if processing_count > 0:
        stage = "ready_for_processing"
    elif export_count > 0:
        stage = "exported_from_lightroom"
    elif watch_count > 0:
        stage = "waiting_in_lightroom"
    else:
        stage = "empty"

    return {
        "site_name": site_name,
        "watch_count": watch_count,
        "export_count": export_count,
        "processing_count": processing_count,
        "stage": stage,
    }
