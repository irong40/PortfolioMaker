"""Stitch drone panorama source photos into a single panorama image."""

import cv2
import sys
from pathlib import Path

# Disable OpenCL to avoid GPU memory issues with large panos
cv2.ocl.setUseOpenCL(False)


def stitch_panorama(input_folder: str, output_path: str | None = None) -> str:
    folder = Path(input_folder)
    image_files = sorted({f for f in folder.iterdir() if f.suffix.lower() == ".jpg"})

    if len(image_files) < 2:
        print(f"Error: Found only {len(image_files)} images in {folder}")
        sys.exit(1)

    print(f"Loading {len(image_files)} images from {folder}...")
    images = []
    for f in image_files:
        img = cv2.imread(str(f))
        if img is None:
            print(f"  Warning: Could not read {f.name}, skipping")
            continue
        # Downscale for stitching speed, full-res can OOM on large panos
        h, w = img.shape[:2]
        if w > 2000:
            scale = 2000 / w
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
        images.append(img)
        print(f"  Loaded {f.name}")

    print(f"\nStitching {len(images)} images...")
    stitcher = cv2.Stitcher.create(cv2.Stitcher_PANORAMA)
    status, pano = stitcher.stitch(images)

    status_messages = {
        cv2.Stitcher_OK: "Success",
        cv2.Stitcher_ERR_NEED_MORE_IMGS: "Need more images - not enough overlap detected",
        cv2.Stitcher_ERR_HOMOGRAPHY_EST_FAIL: "Homography estimation failed - images may not overlap",
        cv2.Stitcher_ERR_CAMERA_PARAMS_ADJUST_FAIL: "Camera parameter adjustment failed",
    }

    if status != cv2.Stitcher_OK:
        print(f"Error: Stitching failed - {status_messages.get(status, f'Unknown error {status}')}")
        sys.exit(1)

    if output_path is None:
        output_path = str(folder.parent / f"{folder.name}_panorama.jpg")

    cv2.imwrite(output_path, pano, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print(f"\nPanorama saved to: {output_path}")
    print(f"Resolution: {pano.shape[1]}x{pano.shape[0]}")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pano_stitch.py <folder> [output.jpg]")
        print("Example: python pano_stitch.py F:/DCIM/PANORAMA/002_0008")
        sys.exit(1)

    output = sys.argv[2] if len(sys.argv) > 2 else None
    stitch_panorama(sys.argv[1], output)
