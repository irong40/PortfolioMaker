"""
property_highlights.py — Sentinel Aerial
GPS-registered animated property boundary overlay for drone footage.

Parses DJI SRT sidecar (per-frame lat/lon/alt/focal_len) + KML property boundary,
projects the polygon onto each video frame using nadir camera math, and renders
an animated yellow draw-on overlay composited via ffmpeg.

Used by Sortie via PropertyHighlightsDialog in sortie.py.
"""

import json
import math
import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ─── Camera constants (DJI M4T wide camera) ──────────────────────────────────
BASE_FOCAL_EQUIV   = 24.0    # mm equivalent at widest setting
BASE_HFOV_DEG      = 82.6    # horizontal FOV at BASE_FOCAL_EQUIV
METERS_PER_LAT_DEG = 111320.0

# ─── Overlay style ─────────────────────────────────────────────────────────
STROKE_COLOR = (255, 204, 0, 255)   # #FFCC00 opaque
FILL_COLOR   = (255, 204, 0, 65)    # semi-transparent fill


# ─── SRT parser ──────────────────────────────────────────────────────────────

def parse_srt(srt_path: str) -> list[dict]:
    """Parse DJI SRT sidecar into per-frame dicts."""
    txt = Path(srt_path).read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        r"\d+\n(\d{2}:\d{2}:\d{2},\d{3}) --> \d{2}:\d{2}:\d{2},\d{3}\n"
        r"<font[^>]*>(.*?)</font>",
        re.DOTALL,
    )

    def get(data, key, default=0.0):
        m = re.search(rf"\[{key}: ([0-9.-]+)", data)
        return float(m.group(1)) if m else default

    def ts(s):
        h, m, sms = s.split(":")
        sec, ms = sms.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000

    frames = []
    for m in pattern.finditer(txt):
        data = m.group(2)
        frames.append({
            "time_s":    ts(m.group(1)),
            "lat":       get(data, "latitude"),
            "lon":       get(data, "longitude"),
            "rel_alt":   get(data, "rel_alt"),
            "focal_len": get(data, "focal_len", BASE_FOCAL_EQUIV),
        })
    return [f for f in frames if not (f["lat"] == 0 and f["lon"] == 0)]


# ─── KML parser ──────────────────────────────────────────────────────────────

def parse_kml(kml_path: str) -> dict:
    """Extract polygon rings and name from KML."""
    tree = ET.parse(kml_path)
    NS = "http://www.opengis.net/kml/2.2"
    polygons, name, description = [], "", ""

    for pm in tree.iter(f"{{{NS}}}Placemark"):
        el = pm.find(f"{{{NS}}}name")
        if el is not None and not name:
            name = (el.text or "").strip()
        el = pm.find(f"{{{NS}}}description")
        if el is not None and not description:
            description = (el.text or "").strip()
        for coords_el in pm.iter(f"{{{NS}}}coordinates"):
            ring = []
            for tok in coords_el.text.strip().split():
                parts = tok.split(",")
                if len(parts) >= 2:
                    try:
                        ring.append((float(parts[1]), float(parts[0])))  # (lat, lon)
                    except ValueError:
                        pass
            tags = {e.tag for e in pm.iter()}
            if len(ring) >= 3 and f"{{{NS}}}Polygon" in tags:
                polygons.append(ring)

    return {"name": name, "description": description, "polygons": polygons}


def kml_center(polygon: list) -> tuple:
    lats = [p[0] for p in polygon]
    lons = [p[1] for p in polygon]
    return sum(lats) / len(lats), sum(lons) / len(lons)


# ─── Camera / projection ─────────────────────────────────────────────────────

def hfov_from_focal(focal_equiv: float) -> float:
    tan_half = math.tan(math.radians(BASE_HFOV_DEG / 2)) * (BASE_FOCAL_EQUIV / focal_equiv)
    return 2 * math.degrees(math.atan(tan_half))


def project_polygon(polygon_latlon, cam_lat, cam_lon, cam_alt,
                    heading_deg, focal_equiv, img_w, img_h):
    """Project (lat, lon) polygon to (px, py) list. Nadir camera assumed."""
    if cam_alt <= 0:
        return None
    hfov_rad = math.radians(hfov_from_focal(focal_equiv))
    scale = img_w / (2 * cam_alt * math.tan(hfov_rad / 2))
    m_per_lon = METERS_PER_LAT_DEG * math.cos(math.radians(cam_lat))
    theta = math.radians(heading_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    pixels = []
    for lat, lon in polygon_latlon:
        dy = (lat - cam_lat) * METERS_PER_LAT_DEG
        dx = (lon - cam_lon) * m_per_lon
        r = dx * cos_t - dy * sin_t
        f = dx * sin_t + dy * cos_t
        pixels.append((img_w / 2 + r * scale, img_h / 2 - f * scale))
    return pixels


def polygon_in_frame(pixels, img_w, img_h, margin=0.5):
    return any(
        -img_w * margin < px < img_w * (1 + margin)
        and -img_h * margin < py < img_h * (1 + margin)
        for px, py in pixels
    )


# ─── Heading inference ────────────────────────────────────────────────────────

def compute_headings(frames: list, window: int = 15) -> list:
    n = len(frames)
    raw = []
    for i in range(n):
        i0, i1 = max(0, i - window // 2), min(n - 1, i + window // 2)
        dlat_m = (frames[i1]["lat"] - frames[i0]["lat"]) * METERS_PER_LAT_DEG
        dlon_m = ((frames[i1]["lon"] - frames[i0]["lon"])
                  * METERS_PER_LAT_DEG * math.cos(math.radians(frames[i0]["lat"])))
        dist = math.sqrt(dlat_m ** 2 + dlon_m ** 2)
        raw.append(math.degrees(math.atan2(dlon_m, dlat_m)) % 360 if dist >= 0.3 else None)

    headings = list(raw)
    last = 0.0
    for i in range(n):
        headings[i] = last if headings[i] is None else (last := headings[i])
    last = headings[-1]
    for i in range(n - 1, -1, -1):
        if raw[i] is None and headings[i] == 0.0:
            headings[i] = last
        elif raw[i] is not None:
            last = raw[i]
    return headings


# ─── Clip selection ───────────────────────────────────────────────────────────

def find_best_window(frames, prop_lat, prop_lon, min_alt=10.0, clip_duration=20.0):
    m_per_lon = METERS_PER_LAT_DEG * math.cos(math.radians(prop_lat))
    scores = []
    for f in frames:
        if f["rel_alt"] < min_alt:
            scores.append(0.0)
            continue
        dy = (f["lat"] - prop_lat) * METERS_PER_LAT_DEG
        dx = (f["lon"] - prop_lon) * m_per_lon
        dist = math.sqrt(dy ** 2 + dx ** 2)
        scores.append(f["rel_alt"] / max(dist + 0.1, 1.0))

    if not frames:
        return 0, 0
    span = frames[-1]["time_s"] - frames[0]["time_s"]
    fps_approx = len(frames) / max(span, 1e-6)
    w = max(1, min(int(clip_duration * fps_approx), len(frames)))

    best_i, best_s = 0, -1.0
    for i in range(len(frames) - w + 1):
        s = sum(scores[i:i + w]) / w
        if s > best_s:
            best_s, best_i = s, i
    return best_i, min(best_i + w - 1, len(frames) - 1)


# ─── Animation ───────────────────────────────────────────────────────────────

def _ease_out_cubic(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def render_overlay_frame(polys_px, img_w, img_h, draw_prog, fill_prog):
    """Render one RGBA overlay frame with progressive polygon + fill."""
    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    stroke_w = max(3, img_w // 640)

    for poly in polys_px:
        if len(poly) < 3:
            continue
        pts = poly[:-1] if poly[-1] == poly[0] else poly
        segs = [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
        lengths = [math.sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) for a, b in segs]
        total = sum(lengths) or 1.0

        # Fill
        if fill_prog > 0:
            alpha = int(FILL_COLOR[3] * _ease_out_cubic(fill_prog))
            fi = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
            ImageDraw.Draw(fi).polygon(
                [(int(p[0]), int(p[1])) for p in pts],
                fill=(FILL_COLOR[0], FILL_COLOR[1], FILL_COLOR[2], alpha),
            )
            img = Image.alpha_composite(img, fi)

        # Progressive stroke
        draw_len = _ease_out_cubic(draw_prog) * total
        cumul, drawn, done = 0.0, [pts[0]], False
        for (a, b), seg_len in zip(segs, lengths):
            if done:
                break
            if cumul + seg_len <= draw_len:
                drawn.append(b)
                cumul += seg_len
            else:
                frac = (draw_len - cumul) / max(seg_len, 1e-9)
                drawn.append((a[0] + frac * (b[0] - a[0]), a[1] + frac * (b[1] - a[1])))
                done = True

        if len(drawn) >= 2:
            draw = ImageDraw.Draw(img)
            draw.line([(int(p[0]), int(p[1])) for p in drawn],
                      fill=STROKE_COLOR, width=stroke_w, joint="curve")

    return img


def render_label(img, text, img_w, img_h, alpha=255):
    draw = ImageDraw.Draw(img)
    font_size = max(18, img_w // 80)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = font_size // 2
    x, y = (img_w - tw) // 2, img_h - th - pad * 4
    bg = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    ImageDraw.Draw(bg).rounded_rectangle(
        [x - pad, y - pad // 2, x + tw + pad, y + th + pad // 2],
        radius=pad // 2, fill=(0, 0, 0, int(160 * alpha / 255)),
    )
    img = Image.alpha_composite(img, bg)
    ImageDraw.Draw(img).text((x, y), text, fill=(255, 255, 255, alpha), font=font)
    return img


# ─── Main render function ────────────────────────────────────────────────────

def render_highlights(
    video_path: str,
    kml_path: str,
    output_path: str,
    *,
    clip_start: float | None = None,
    clip_end: float | None = None,
    clip_duration: float = 20.0,
    heading_override: float | None = None,
    draw_duration: float = 2.5,
    hold_duration: float = 15.0,
    min_alt: float = 10.0,
    show_label: bool = True,
    scale_down: int = 1,
    crf: int = 18,
    progress_cb=None,   # callable(int, int) → (current_frame, total_frames)
    cancel_flag=None,   # threading.Event; set to request cancellation
) -> str:
    """
    Render animated property highlights overlay onto a drone video.

    Returns output_path on success. Raises RuntimeError on failure.
    progress_cb(current, total) is called every 30 frames if provided.
    cancel_flag is a threading.Event; checked every frame.
    """
    srt_path = os.path.splitext(video_path)[0] + ".SRT"
    if not os.path.exists(srt_path):
        srt_path = os.path.splitext(video_path)[0] + ".srt"
    if not os.path.exists(srt_path):
        raise RuntimeError(f"No SRT sidecar found alongside {video_path}")

    # Parse
    frames = parse_srt(srt_path)
    if not frames:
        raise RuntimeError("No GPS data found in SRT file")

    kml = parse_kml(kml_path)
    if not kml["polygons"]:
        raise RuntimeError("No polygon found in KML")

    prop_lat, prop_lon = kml_center(kml["polygons"][0])

    # Video info
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", video_path],
        capture_output=True, text=True, check=True,
    )
    vi = json.loads(probe.stdout)
    vs = next(s for s in vi["streams"] if s["codec_type"] == "video")
    orig_w, orig_h = vs["width"], vs["height"]
    fps = eval(vs["r_frame_rate"])
    img_w, img_h = orig_w // scale_down, orig_h // scale_down

    # Clip window
    if clip_start is not None and clip_end is not None:
        cs, ce = clip_start, clip_end
    else:
        fi0, fi1 = find_best_window(frames, prop_lat, prop_lon,
                                     min_alt=min_alt, clip_duration=clip_duration)
        cs = frames[fi0]["time_s"]
        ce = frames[fi1]["time_s"]

    clip_dur = ce - cs
    n_frames = max(1, int(round(clip_dur * fps)))

    # Headings
    headings = compute_headings(frames)

    def srt_at(t_abs):
        bi = min(range(len(frames)), key=lambda i: abs(frames[i]["time_s"] - t_abs))
        return frames[bi], headings[bi]

    # FFmpeg filter
    if scale_down > 1:
        vf = f"[0:v]scale={img_w}:{img_h}[s];[s][1:v]overlay=0:0:format=auto[out]"
    else:
        vf = "[0:v][1:v]overlay=0:0:format=auto[out]"

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(cs), "-t", str(clip_dur),
        "-i", video_path,
        "-f", "rawvideo", "-pix_fmt", "rgba",
        "-video_size", f"{img_w}x{img_h}",
        "-framerate", f"{fps:.6f}",
        "-i", "pipe:0",
        "-filter_complex", vf,
        "-map", "[out]",
        "-c:v", "libx264", "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-preset", "fast",
        output_path,
    ]

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    stderr_tmp = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=stderr_tmp)

    anim_start = None
    try:
        for fi in range(n_frames):
            if cancel_flag and cancel_flag.is_set():
                proc.stdin.close()
                proc.terminate()
                raise RuntimeError("Cancelled by user")

            t_abs = cs + fi / fps
            srt_f, heading = srt_at(t_abs)
            h = heading_override if heading_override is not None else heading

            polys_px = []
            for ring in kml["polygons"]:
                px = project_polygon(ring, srt_f["lat"], srt_f["lon"],
                                     srt_f["rel_alt"], h, srt_f["focal_len"], img_w, img_h)
                if px:
                    polys_px.append(px)

            in_frame = any(polygon_in_frame(p, img_w, img_h) for p in polys_px)
            if in_frame and anim_start is None:
                anim_start = fi / fps

            draw_prog = fill_prog = 0.0
            if anim_start is not None and in_frame:
                anim_t = fi / fps - anim_start
                draw_prog = min(1.0, anim_t / draw_duration)
                fill_prog = max(0.0, min(1.0, (anim_t - draw_duration * 0.8) / (draw_duration * 0.3)))

            frame_img = render_overlay_frame(polys_px, img_w, img_h, draw_prog, fill_prog)
            if show_label and draw_prog >= 1.0:
                frame_img = render_label(frame_img, kml["name"], img_w, img_h,
                                         int(255 * fill_prog))

            proc.stdin.write(frame_img.tobytes())

            if progress_cb and fi % 30 == 0:
                progress_cb(fi, n_frames)

    except BrokenPipeError:
        pass

    proc.stdin.close()
    proc.wait()

    if proc.returncode != 0:
        stderr_tmp.seek(0)
        err = stderr_tmp.read().decode(errors="replace")
        raise RuntimeError(f"ffmpeg failed:\n{err[-2000:]}")
    stderr_tmp.close()

    if progress_cb:
        progress_cb(n_frames, n_frames)

    return output_path


# ─── KML auto-match ──────────────────────────────────────────────────────────

MISSIONS_DIR = Path("E:/Sentinel/Missions")


def find_matching_kml(video_path: str) -> str | None:
    """
    Look for a KML in E:/Sentinel/Missions/ whose GPS center is close to
    the video's average GPS position (from the SRT sidecar).
    Returns best-match KML path or None.
    """
    srt_path = os.path.splitext(video_path)[0] + ".SRT"
    if not os.path.exists(srt_path):
        srt_path = os.path.splitext(video_path)[0] + ".srt"
    if not os.path.exists(srt_path):
        return None

    frames = parse_srt(srt_path)
    if not frames:
        return None

    lats = [f["lat"] for f in frames if f["lat"] != 0]
    lons = [f["lon"] for f in frames if f["lon"] != 0]
    if not lats:
        return None
    vid_lat = sum(lats) / len(lats)
    vid_lon = sum(lons) / len(lons)

    def haversine(lat1, lon1, lat2, lon2):
        R = 6371000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    best_path, best_dist = None, float("inf")
    if not MISSIONS_DIR.exists():
        return None

    for kml_file in MISSIONS_DIR.glob("*.kml"):
        try:
            kml = parse_kml(str(kml_file))
            if not kml["polygons"]:
                continue
            clat, clon = kml_center(kml["polygons"][0])
            d = haversine(vid_lat, vid_lon, clat, clon)
            if d < best_dist:
                best_dist, best_path = d, str(kml_file)
        except Exception:
            continue

    # Only return if within 500m — otherwise too far to be the same property
    return best_path if best_dist < 500 else None
