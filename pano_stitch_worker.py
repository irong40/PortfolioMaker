"""
Sortie — Panorama Stitch Worker (subprocess)

Stitches ONE panorama set and exits. cv2.Stitcher peaks at several GB
for a DJI sphere set, and CPython/OpenCV never return that heap to the
OS — running each set in a short-lived subprocess keeps the Sortie GUI
process small and hands the memory back to Windows when the set is done.

Invoked by photo_classifier.stitch_panoramas():
    python pano_stitch_worker.py <job.json>

job.json:
    {"photos": ["..."], "output_path": "...", "max_width": 2000}

Always prints a one-line JSON result to stdout:
    {"ok": true, "width": W, "height": H}
    {"ok": false, "error": "..."}
A missing/unparseable result means the worker crashed (e.g. OOM) and the
parent reports that as the stitch error.
"""

import json
import sys


def stitch(photos, output_path, max_width=2000):
    try:
        import cv2
        cv2.ocl.setUseOpenCL(False)
    except ImportError:
        return {"ok": False, "error": "OpenCV not installed"}

    images = []
    for photo_path in photos:
        img = cv2.imread(photo_path)
        if img is None:
            continue
        h, w = img.shape[:2]
        if w > max_width:
            scale = max_width / w
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
        images.append(img)

    if len(images) < 2:
        return {"ok": False, "error": f"Only {len(images)} readable images"}

    stitcher = cv2.Stitcher.create(cv2.Stitcher_PANORAMA)
    status, pano = stitcher.stitch(images)
    images.clear()

    if status != cv2.Stitcher_OK:
        errors = {
            cv2.Stitcher_ERR_NEED_MORE_IMGS: "not enough overlap",
            cv2.Stitcher_ERR_HOMOGRAPHY_EST_FAIL: "homography failed",
            cv2.Stitcher_ERR_CAMERA_PARAMS_ADJUST_FAIL: "camera params failed",
        }
        return {"ok": False, "error": errors.get(status, f"unknown error {status}")}

    if not cv2.imwrite(output_path, pano, [cv2.IMWRITE_JPEG_QUALITY, 95]):
        return {"ok": False, "error": f"failed to write {output_path}"}

    return {"ok": True, "width": int(pano.shape[1]), "height": int(pano.shape[0])}


def main():
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "error": "usage: pano_stitch_worker.py <job.json>"}))
        sys.exit(2)

    with open(sys.argv[1], "r") as f:
        job = json.load(f)

    result = stitch(
        job["photos"],
        job["output_path"],
        max_width=job.get("max_width", 2000),
    )
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
