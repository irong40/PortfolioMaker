"""
Sortie — Report Image Processing

Generates thumbnails and preview images for embedding in PDF reports.
Handles TIFF→JPEG conversion, resizing, and photo grid layout data.
"""

import os
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# ─── THUMBNAIL GENERATION ────────────────────────────────────────────────

def generate_thumbnail(image_path, output_dir, max_size=(800, 600), suffix="_thumb"):
    """Create a JPEG thumbnail from any image (including TIFF/DNG).

    Args:
        image_path: Path to source image
        output_dir: Directory to save thumbnail
        max_size: (width, height) max dimensions
        suffix: Filename suffix before extension

    Returns:
        Path to thumbnail JPEG, or None on failure.
    """
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None  # Allow large orthomosaics

        img = Image.open(image_path)

        # Handle 16-bit TIFF (orthomosaic, DSM)
        if img.mode in ("I;16", "I"):
            import numpy as np
            arr = np.array(img, dtype=np.float32)
            # Normalize to 0-255
            arr_min, arr_max = arr.min(), arr.max()
            if arr_max > arr_min:
                arr = ((arr - arr_min) / (arr_max - arr_min) * 255).astype("uint8")
            else:
                arr = (arr * 0).astype("uint8")
            img = Image.fromarray(arr)

        if img.mode in ("RGBA", "P", "LA", "F"):
            img = img.convert("RGB")

        img.thumbnail(max_size, Image.LANCZOS)

        stem = Path(image_path).stem
        out_path = Path(output_dir) / f"{stem}{suffix}.jpg"
        os.makedirs(output_dir, exist_ok=True)
        img.save(str(out_path), format="JPEG", quality=85)

        log.debug(f"Thumbnail: {out_path}")
        return str(out_path)

    except Exception as e:
        log.warning(f"Failed to generate thumbnail for {image_path}: {e}")
        return None


def generate_ortho_preview(ortho_path, output_dir, max_size=(1200, 900)):
    """Create a preview JPEG from an orthomosaic TIFF.

    Returns path to preview JPEG, or None.
    """
    if not ortho_path or not os.path.exists(ortho_path):
        return None
    return generate_thumbnail(ortho_path, output_dir, max_size=max_size, suffix="_preview")


def generate_dsm_preview(dsm_path, output_dir, max_size=(800, 600)):
    """Create a colorized elevation preview from a DSM TIFF.

    Returns path to preview JPEG, or None.
    """
    if not dsm_path or not os.path.exists(dsm_path):
        return None

    try:
        from PIL import Image
        import numpy as np
        Image.MAX_IMAGE_PIXELS = None

        img = Image.open(dsm_path)
        arr = np.array(img, dtype=np.float32)

        # Remove nodata (common: -9999, 0, very negative)
        valid = arr[arr > -9000]
        if len(valid) == 0:
            return None

        vmin, vmax = np.percentile(valid, [2, 98])
        arr = np.clip(arr, vmin, vmax)
        normalized = ((arr - vmin) / (vmax - vmin) * 255).astype("uint8")

        # Apply a simple elevation colormap (blue=low → green=mid → red=high)
        colored = np.zeros((*normalized.shape, 3), dtype="uint8")
        colored[..., 0] = normalized  # Red channel = high elevation
        colored[..., 1] = 255 - np.abs(normalized.astype(int) - 128).astype("uint8") * 2  # Green = mid
        colored[..., 2] = 255 - normalized  # Blue = low elevation

        out_img = Image.fromarray(colored)
        out_img.thumbnail(max_size, Image.LANCZOS)

        out_path = Path(output_dir) / "dsm_elevation_preview.jpg"
        os.makedirs(output_dir, exist_ok=True)
        out_img.save(str(out_path), format="JPEG", quality=85)

        log.debug(f"DSM preview: {out_path}")
        return str(out_path)

    except Exception as e:
        log.warning(f"Failed to generate DSM preview: {e}")
        return None


# ─── PHOTO SELECTION FOR REPORT ──────────────────────────────────────────

def select_report_photos(photos, max_photos=4):
    """Select the best photos for embedding in the report.

    Picks a mix of nadir (overview) and oblique (detail) shots,
    spread across the site by GPS position.

    Args:
        photos: list of PhotoMeta objects
        max_photos: max photos to embed

    Returns:
        list of PhotoMeta objects
    """
    nadir = [p for p in photos if p.classification == "nadir" and os.path.exists(p.path)]
    oblique = [p for p in photos if p.classification == "oblique" and os.path.exists(p.path)]

    selected = []

    # 1 nadir overview
    if nadir:
        # Pick middle of GPS spread for best overview
        with_gps = [p for p in nadir if p.latitude is not None]
        if with_gps:
            sorted_lat = sorted(with_gps, key=lambda p: p.latitude)
            selected.append(sorted_lat[len(sorted_lat) // 2])
        else:
            selected.append(nadir[0])

    # Fill rest with obliques from different angles
    if oblique:
        with_yaw = sorted(
            [p for p in oblique if p.yaw is not None],
            key=lambda p: p.yaw,
        )
        if with_yaw:
            step = max(1, len(with_yaw) // (max_photos - len(selected)))
            for i in range(0, len(with_yaw), step):
                if len(selected) >= max_photos:
                    break
                selected.append(with_yaw[i])
        else:
            remaining = max_photos - len(selected)
            selected.extend(oblique[:remaining])

    # Fallback: fill with any remaining
    if len(selected) < max_photos:
        all_remaining = [p for p in photos if p not in selected and os.path.exists(p.path)]
        selected.extend(all_remaining[:max_photos - len(selected)])

    return selected[:max_photos]


def prepare_report_images(photos, ortho_path, dsm_path, output_dir, max_photos=4):
    """Prepare all images needed for the report.

    Args:
        photos: list of PhotoMeta objects
        ortho_path: path to orthomosaic TIFF (or None)
        dsm_path: path to DSM TIFF (or None)
        output_dir: directory for generated thumbnails

    Returns:
        dict with keys:
            photo_thumbs: list of (thumb_path, caption) tuples
            ortho_preview: path or None
            dsm_preview: path or None
    """
    thumb_dir = os.path.join(output_dir, "_report_thumbs")

    # Generate photo thumbnails
    selected = select_report_photos(photos, max_photos)
    photo_thumbs = []
    for photo in selected:
        thumb = generate_thumbnail(photo.path, thumb_dir, max_size=(600, 450))
        if thumb:
            caption = f"{photo.classification.title()}"
            if photo.pitch is not None:
                caption += f" | {photo.pitch:.0f}\u00b0 pitch"
            if photo.altitude is not None:
                caption += f" | {photo.altitude:.0f}m alt"
            photo_thumbs.append((thumb, caption))

    # Generate ortho preview
    ortho_preview = generate_ortho_preview(ortho_path, thumb_dir)

    # Generate DSM elevation preview
    dsm_preview = generate_dsm_preview(dsm_path, thumb_dir)

    return {
        "photo_thumbs": photo_thumbs,
        "ortho_preview": ortho_preview,
        "dsm_preview": dsm_preview,
    }
