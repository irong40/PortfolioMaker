"""video_stills.py — Sentinel Aerial

Extract cardinal-anchor stills from a DJI orbit video using the SRT
telemetry sidecar + ffmpeg. Answers the boundary-anchor / elevation-shot
use case: fly one slow orbit video, pull the four frames where the
gimbal faced N/E/S/W (or front/right/back/left when a front bearing
is given), and drop them into the deliverable set.

Design notes:
- Self-contained SRT parsing (regex on DJI bracket tags), same style as
  reel_render.clip_color_mode. No import from drone-pipeline (S1 audit).
- ffmpeg does the frame pull (-ss before -i, single frame, q:v 2).
  Resolve was evaluated and rejected: no headless mode, external
  scripting needs Studio. ffmpeg is already a repo dependency.
- Extracted frames carry no EXIF, so the SRT's GPS/altitude/timestamp
  is injected via piexif (already in requirements). If piexif is
  missing the stills still land — metadata goes to the JSON manifest
  only. Everything fails soft; a bad SRT never breaks a run.

Yaw convention: DJI gb_yaw is degrees from north, -180..180.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

FFMPEG_TIMEOUT_S = 120
MAX_YAW_ERROR_DEG = 15.0   # best frame further off than this = missing side

# Bracket tags on DJI SRT frame blocks. Tolerant of spacing variants:
# [gb_yaw: -12.3 gb_pitch: -45.0 gb_roll: 0.0]
# [latitude: 36.795123] [longitude: -76.405456]
# [rel_alt: 45.300 abs_alt: 57.400]
_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->")
_TAG_RES = {
    "yaw": re.compile(r"\[gb_yaw\s*:\s*(-?\d+(?:\.\d+)?)"),
    "pitch": re.compile(r"gb_pitch\s*:\s*(-?\d+(?:\.\d+)?)"),
    "lat": re.compile(r"\[latitude\s*:\s*(-?\d+(?:\.\d+)?)"),
    "lon": re.compile(r"\[longit?ude\s*:\s*(-?\d+(?:\.\d+)?)"),
    "rel_alt": re.compile(r"\[rel_alt\s*:\s*(-?\d+(?:\.\d+)?)"),
    "abs_alt": re.compile(r"abs_alt\s*:\s*(-?\d+(?:\.\d+)?)"),
}
_DATETIME_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})")

CARDINALS = (("N", 0.0), ("E", 90.0), ("S", 180.0), ("W", 270.0))
RELATIVE_LABELS = ("front", "right", "back", "left")


def _angle_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two headings in degrees."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def parse_srt_telemetry(srt_path: str) -> list[dict]:
    """Parse a DJI SRT sidecar into telemetry samples.

    Returns a list of dicts: {t (float seconds from clip start), yaw,
    pitch, lat, lon, rel_alt, abs_alt (floats or None), timestamp (str
    or None)}. Samples without a yaw tag are dropped — yaw is the one
    field this module exists for. [] on unreadable/absent file.
    """
    try:
        text = Path(srt_path).read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        log.warning("SRT unreadable (%s): %s", srt_path, e)
        return []

    samples = []
    current_t = None
    current_dt = None
    for line in text.splitlines():
        m = _TIME_RE.search(line)
        if m:
            h, mi, s, ms = (int(g) for g in m.groups())
            current_t = h * 3600 + mi * 60 + s + ms / 1000.0
            current_dt = None
            continue
        dm = _DATETIME_RE.search(line)
        if dm:
            current_dt = dm.group(1)  # datetime is its own line in DJI blocks
        ym = _TAG_RES["yaw"].search(line)
        if ym is None or current_t is None:
            continue
        sample = {"t": current_t, "yaw": float(ym.group(1))}
        for key in ("pitch", "lat", "lon", "rel_alt", "abs_alt"):
            tm = _TAG_RES[key].search(line)
            sample[key] = float(tm.group(1)) if tm else None
        sample["timestamp"] = current_dt
        samples.append(sample)
        current_t = None  # one telemetry line per block
        current_dt = None
    return samples


def has_yaw_telemetry(srt_path: str | None) -> bool:
    """Cheap check (first 8KB) that an SRT carries gimbal yaw tags."""
    if not srt_path:
        return False
    try:
        with open(srt_path, encoding="utf-8", errors="ignore") as fh:
            head = fh.read(8192)
    except OSError:
        return False
    return _TAG_RES["yaw"].search(head) is not None


def yaw_coverage_deg(samples: list[dict]) -> float:
    """Total yaw arc covered, in degrees (0-360), via 10-degree bins."""
    bins = {int(((s["yaw"]) % 360.0) // 10) for s in samples}
    return len(bins) * 10.0


def select_cardinal_frames(samples: list[dict],
                           front_bearing: float | None = None,
                           max_error_deg: float = MAX_YAW_ERROR_DEG):
    """Pick the best sample for each cardinal target.

    front_bearing None -> absolute N/E/S/W labels.
    front_bearing set  -> front/right/back/left relative to it.

    Returns (picks, missing): picks is {label: {**sample, target, error}},
    missing is a list of labels with no sample within max_error_deg.
    """
    if front_bearing is None:
        targets = list(CARDINALS)
    else:
        targets = [(lbl, (front_bearing + off) % 360.0)
                   for lbl, (_, off) in zip(RELATIVE_LABELS, CARDINALS)]

    picks, missing = {}, []
    for label, heading in targets:
        best, best_err = None, None
        for s in samples:
            err = _angle_diff(s["yaw"] % 360.0, heading)
            if best_err is None or err < best_err:
                best, best_err = s, err
        if best is None or best_err > max_error_deg:
            missing.append(label)
        else:
            picks[label] = {**best, "target": heading,
                            "error": round(best_err, 1)}
    return picks, missing


def extract_frame(video_path: str, t: float, out_path: str) -> bool:
    """Pull one frame at t seconds into out_path via ffmpeg. False on failure."""
    cmd = ["ffmpeg", "-y", "-ss", f"{max(t, 0.0):.3f}", "-i", str(video_path),
           "-frames:v", "1", "-q:v", "2", str(out_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=FFMPEG_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("ffmpeg failed (%s @ %.2fs): %s", video_path, t, e)
        return False
    if proc.returncode != 0 or not Path(out_path).exists():
        log.warning("ffmpeg rc=%s for %s @ %.2fs: %s", proc.returncode,
                    video_path, t, (proc.stderr or "")[-300:])
        return False
    return True


def _deg_to_dms_rational(deg: float):
    d = int(abs(deg))
    m_full = (abs(deg) - d) * 60
    m = int(m_full)
    s = round((m_full - m) * 60 * 10000)
    return ((d, 1), (m, 1), (s, 10000))


def inject_exif(jpg_path: str, sample: dict, description: str) -> bool:
    """Write GPS/timestamp/description EXIF from an SRT sample. Fail-soft."""
    try:
        import piexif
    except ImportError:
        log.info("piexif not installed — stills keep manifest metadata only")
        return False
    try:
        exif = {"0th": {}, "Exif": {}, "GPS": {}}
        exif["0th"][piexif.ImageIFD.ImageDescription] = description.encode()
        exif["0th"][piexif.ImageIFD.Software] = b"Sortie video_stills"
        if sample.get("timestamp"):
            dt = sample["timestamp"].replace("-", ":")
            exif["Exif"][piexif.ExifIFD.DateTimeOriginal] = dt.encode()
        lat, lon = sample.get("lat"), sample.get("lon")
        if lat is not None and lon is not None:
            exif["GPS"][piexif.GPSIFD.GPSLatitudeRef] = (
                b"N" if lat >= 0 else b"S")
            exif["GPS"][piexif.GPSIFD.GPSLatitude] = _deg_to_dms_rational(lat)
            exif["GPS"][piexif.GPSIFD.GPSLongitudeRef] = (
                b"E" if lon >= 0 else b"W")
            exif["GPS"][piexif.GPSIFD.GPSLongitude] = _deg_to_dms_rational(lon)
            alt = sample.get("abs_alt")
            if alt is not None:
                exif["GPS"][piexif.GPSIFD.GPSAltitudeRef] = 0 if alt >= 0 else 1
                exif["GPS"][piexif.GPSIFD.GPSAltitude] = (
                    int(abs(alt) * 100), 100)
        piexif.insert(piexif.dump(exif), str(jpg_path))
        return True
    except Exception as e:  # piexif raises broadly; a still without EXIF is fine
        log.warning("EXIF injection failed for %s: %s", jpg_path, e)
        return False


def extract_cardinal_stills(video_path: str, srt_path: str, out_dir: str,
                            front_bearing: float | None = None,
                            site_name: str = "") -> dict:
    """Full pipeline: SRT -> cardinal picks -> ffmpeg stills -> manifest.

    Returns {"stills": {label: path}, "missing": [...], "coverage_deg": n,
    "manifest": path, "error": str|None}. Never raises.
    """
    result = {"stills": {}, "missing": [], "coverage_deg": 0.0,
              "manifest": None, "error": None}

    samples = parse_srt_telemetry(srt_path)
    if not samples:
        result["error"] = "no yaw telemetry in SRT"
        return result

    result["coverage_deg"] = yaw_coverage_deg(samples)
    picks, missing = select_cardinal_frames(samples, front_bearing)
    result["missing"] = missing
    if not picks:
        result["error"] = ("orbit covers %.0f deg of yaw — no cardinal "
                           "frame within %.0f deg of any target"
                           % (result["coverage_deg"], MAX_YAW_ERROR_DEG))
        return result

    out = Path(out_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        result["error"] = f"cannot create output dir: {e}"
        return result

    stem = Path(video_path).stem
    prefix = f"{site_name}_" if site_name else ""
    manifest_entries = []
    for label, pick in picks.items():
        name = f"{prefix}{stem}_{label}_yaw{int(pick['yaw']) % 360:03d}.JPG"
        dest = out / name
        if not extract_frame(video_path, pick["t"], str(dest)):
            result["missing"].append(label)
            continue
        desc = (f"Cardinal anchor {label} — yaw {pick['yaw']:.1f} deg "
                f"(target {pick['target']:.0f}, off {pick['error']:.1f}), "
                f"t={pick['t']:.2f}s of {Path(video_path).name}")
        inject_exif(str(dest), pick, desc)
        result["stills"][label] = str(dest)
        manifest_entries.append({
            "label": label, "file": name, "t": pick["t"],
            "yaw": pick["yaw"], "target": pick["target"],
            "error": pick["error"], "lat": pick.get("lat"),
            "lon": pick.get("lon"), "rel_alt": pick.get("rel_alt"),
            "abs_alt": pick.get("abs_alt"),
            "timestamp": pick.get("timestamp"),
        })

    manifest = {
        "video": str(video_path), "srt": str(srt_path),
        "generated": datetime.now().isoformat(timespec="seconds"),
        "front_bearing": front_bearing,
        "coverage_deg": result["coverage_deg"],
        "max_yaw_error_deg": MAX_YAW_ERROR_DEG,
        "stills": manifest_entries, "missing": result["missing"],
    }
    manifest_path = out / f"{stem}_cardinal_manifest.json"
    try:
        manifest_path.write_text(json.dumps(manifest, indent=2))
        result["manifest"] = str(manifest_path)
    except OSError as e:
        log.warning("Manifest write failed: %s", e)

    if not result["stills"]:
        result["error"] = "all frame extractions failed (is ffmpeg on PATH?)"
    return result
