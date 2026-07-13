"""
reel_render.py — Sentinel Aerial
Render core for the video reel renderer: clip analysis, edit planning,
and ffmpeg assembly per the locked reel recipe (see docs/reel-job-spec.md).

Pipeline: probe + motion/exposure sampling (LRF proxy when present) →
best-window selection per clip → edit plan (intro card, scored clip
segments in chronological order, outro card, 0.5s crossfades) → single
ffmpeg pass to 4K master (hevc_nvenc) → derived 1080p web + 9:16 vertical
cuts (h264_nvenc).

Audio priority: explicit job music track > music pool pick > native clip
audio > silence. Branded Remotion templates replace the PIL title cards in
Phase 4; photos-only Ken Burns reels are not implemented yet.
"""

import json
import math
import re
import subprocess
import tempfile
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont

# ─── Recipe constants ────────────────────────────────────────────────────────
XFADE_S = 0.5          # locked recipe: 0.5s crossfades only
INTRO_S = 3.0
OUTRO_S = 4.0
MAP_S = 3.0            # flight-path/location map card (before outro)
MIN_SEG_S = 2.5
MAX_SEG_S = 8.0
TARGET_SEG_S = 5.5

MASTER_W, MASTER_H = 3840, 2160
FPS = 30

SAMPLE_FPS = 3.0       # analysis sampling rate
ANALYZE_MAX_DIM = 320  # downscale for motion/brightness sampling

# DJI's official D-Log M -> Rec.709 conversion LUT (one curve across the
# Mini 4 Pro / Matrice 4 series). render.lut in the job overrides this.
DEFAULT_LUT = Path(__file__).resolve().parent / "assets" / "luts" / "dji_dlog_m_to_rec709.cube"

# ─── Card style (PIL v1 — Remotion replaces these in Phase 4) ────────────────
CARD_BG = (11, 15, 20)             # near-black slate
CARD_ACCENT = (255, 204, 0)        # SAI yellow, matches property_highlights
CARD_TEXT = (255, 255, 255)
CARD_DIM = (170, 178, 189)
BRAND_LINE = "SENTINEL AERIAL INSPECTIONS"
BRAND_URL = "sentinelaerialinspections.com"


# ─── Probing & analysis ──────────────────────────────────────────────────────

def probe_media(path: str) -> dict:
    """ffprobe a media file -> {duration, width, height, fps, has_audio}."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height,r_frame_rate:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    info = {"duration": float(data["format"]["duration"]),
            "width": 0, "height": 0, "fps": float(FPS), "has_audio": False}
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not info["width"]:
            info["width"] = stream.get("width", 0)
            info["height"] = stream.get("height", 0)
            num, _, den = stream.get("r_frame_rate", "30/1").partition("/")
            if float(den or 1):
                info["fps"] = float(num) / float(den or 1)
        elif stream.get("codec_type") == "audio":
            info["has_audio"] = True
    return info


def find_proxy(path: str) -> str:
    """DJI LRF low-res proxy beside the clip, if present — much faster to decode."""
    lrf = Path(path).with_suffix(".LRF")
    return str(lrf) if lrf.exists() else str(path)


def clip_color_mode(srt_path: str | None) -> str | None:
    """Color profile from a DJI SRT sidecar ('dlog_m', 'default', ...), or None.

    DJI stamps [color_md: X] on every frame block; the first occurrence is
    enough. Fail-soft: unreadable/absent SRT or no tag -> None (treated as
    normal profile — no LUT applied).
    """
    if not srt_path:
        return None
    try:
        with open(srt_path, encoding="utf-8", errors="ignore") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    match = re.search(r"\[color_md\s*:\s*([\w-]+)\]", head)
    return match.group(1).lower() if match else None


def resolve_lut(job: dict) -> str | None:
    """LUT to grade D-Log clips with: explicit render.lut > repo default > None."""
    explicit = (job.get("render") or {}).get("lut")
    if explicit:
        return str(explicit) if Path(explicit).exists() else None
    return str(DEFAULT_LUT) if DEFAULT_LUT.exists() else None


def _lut_filter(lut_path: str) -> str:
    """lut3d filter atom with the path escaped for a filtergraph string
    (forward slashes; drive colon escaped; quoted against spaces)."""
    escaped = lut_path.replace("\\", "/").replace(":", "\\:")
    return f"lut3d='{escaped}'"


def sample_clip(path: str, sample_fps: float = SAMPLE_FPS) -> list[dict]:
    """Sample motion + brightness over a clip -> [{t, motion, brightness}].

    motion = mean abs diff vs previous sample (0-1), brightness = mean luma (0-1).
    """
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return []
    native_fps = cap.get(cv2.CAP_PROP_FPS) or FPS
    step = max(1, round(native_fps / sample_fps))
    samples, prev, frame_idx = [], None, 0
    while True:
        grabbed = cap.grab()
        if not grabbed:
            break
        if frame_idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            scale = ANALYZE_MAX_DIM / max(h, w)
            small = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            motion = float(cv2.absdiff(gray, prev).mean() / 255.0) if prev is not None else 0.0
            samples.append({
                "t": frame_idx / native_fps,
                "motion": motion,
                "brightness": float(gray.mean() / 255.0),
            })
            prev = gray
        frame_idx += 1
    cap.release()
    return samples


def window_score(samples: list[dict]) -> float:
    """Score a window of samples: motion-driven, penalizing dead exposure."""
    if not samples:
        return 0.0
    motion = sum(s["motion"] for s in samples) / len(samples)
    brightness = sum(s["brightness"] for s in samples) / len(samples)
    exposure_w = min(1.0, brightness / 0.04)     # near-black with no action -> low
    if brightness > 0.92:                        # blown out
        exposure_w *= 0.5
    return motion * exposure_w


def best_window(samples: list[dict], clip_duration: float, win_s: float) -> tuple[float, float]:
    """Best (start, score) window of win_s seconds. Centered fallback if unsampled."""
    if clip_duration <= win_s:
        return 0.0, window_score(samples)
    if not samples:
        return max(0.0, (clip_duration - win_s) / 2), 0.0
    best_start, best = 0.0, -1.0
    stride = max(0.5, win_s / 4)
    start = 0.0
    while start + win_s <= clip_duration + 1e-6:
        in_win = [s for s in samples if start <= s["t"] < start + win_s]
        score = window_score(in_win)
        if score > best:
            best_start, best = start, score
        start += stride
    return best_start, best


# ─── Edit planning ───────────────────────────────────────────────────────────

def choose_segmentation(target_s: float, n_clips: int,
                        intro_s: float = INTRO_S, outro_s: float = OUTRO_S,
                        xfade: float = XFADE_S,
                        extra_cards_s: float = 0.0,
                        extra_cards: int = 0) -> tuple[int, float]:
    """Pick (body segment count, segment length) to hit target duration.

    Timeline = intro + outro + extras + n*seg - xfade*(n+1+extra_cards);
    prefers segments near TARGET_SEG_S within [MIN_SEG_S, MAX_SEG_S]. With
    few clips the reel comes out shorter than target rather than dragging
    segments past max. extra_cards/extra_cards_s account for optional cards
    (e.g. the map card) beyond intro+outro.
    """
    if n_clips < 1:
        raise ValueError("no usable clips")
    best = None
    for n in range(1, n_clips + 1):
        transitions = n + 1 + extra_cards
        body_time = (target_s - intro_s - outro_s - extra_cards_s
                     + xfade * transitions)
        seg = min(max(body_time / n, MIN_SEG_S), MAX_SEG_S)
        achieved = (intro_s + outro_s + extra_cards_s + n * seg
                    - xfade * transitions)
        cost = (round(abs(achieved - target_s), 3), round(abs(seg - TARGET_SEG_S), 3))
        if best is None or cost < best[0]:
            best = (cost, n, seg)
    return best[1], best[2]


def plan_reel(clips: list[dict], target_s: float,
              map_card: bool = False) -> list[dict]:
    """Build the edit plan from analyzed clips.

    clips: [{path, duration, samples}] — chronological (DJI filenames sort so).
    Returns plan items: {type: card|clip, dur, ...}; clip items carry path/start.
    map_card inserts a flight-path map card before the outro (MAP_S seconds),
    absorbed into the segmentation so the timeline still hits target_s.
    """
    usable = [c for c in clips if c["duration"] >= MIN_SEG_S]
    if not usable:
        raise ValueError(f"no clips >= {MIN_SEG_S}s to build a reel from")
    n, seg = choose_segmentation(
        target_s, len(usable),
        extra_cards_s=MAP_S if map_card else 0.0,
        extra_cards=1 if map_card else 0)

    scored = []
    for c in usable:
        win = min(seg, c["duration"])
        start, score = best_window(c["samples"], c["duration"], win)
        scored.append({"path": c["path"], "start": start, "dur": win, "score": score})
    picked = sorted(scored, key=lambda s: s["score"], reverse=True)[:n]
    picked.sort(key=lambda s: s["path"])  # back to chronological order

    plan = [{"type": "card", "card": "intro", "dur": INTRO_S}]
    plan += [{"type": "clip", "path": p["path"], "start": p["start"], "dur": p["dur"]}
             for p in picked]
    if map_card:
        plan.append({"type": "card", "card": "map", "dur": MAP_S})
    plan.append({"type": "card", "card": "outro", "dur": OUTRO_S})
    return plan


def plan_duration(plan: list[dict], xfade: float = XFADE_S) -> float:
    """Final timeline duration of a plan after crossfade overlap."""
    return sum(p["dur"] for p in plan) - xfade * (len(plan) - 1)


def xfade_offsets(durations: list[float], xfade: float = XFADE_S) -> list[float]:
    """Offsets for chained ffmpeg xfade filters (one per transition)."""
    offsets, timeline = [], 0.0
    for dur in durations[:-1]:
        timeline += dur - xfade
        offsets.append(round(timeline, 3))
    return offsets


# ─── Title cards (PIL v1) ────────────────────────────────────────────────────

def _font(size: int, bold: bool = True):
    name = "arialbd.ttf" if bold else "arial.ttf"
    try:
        return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
    except Exception:
        return ImageFont.load_default()


def _center_text(draw, text, font, y, width, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(((width - (bbox[2] - bbox[0])) // 2, y), text, font=font, fill=fill)
    return bbox[3] - bbox[1]


def make_card(kind: str, job: dict, out_path: str,
              size: tuple[int, int] = (MASTER_W, MASTER_H)) -> str:
    """Render an intro/outro title card PNG. Returns out_path."""
    w, h = size
    img = Image.new("RGB", size, CARD_BG)
    draw = ImageDraw.Draw(img)
    rule_w = w // 6
    if kind == "intro":
        y = int(h * 0.36)
        y += _center_text(draw, BRAND_LINE, _font(h // 28), y, w, CARD_ACCENT) + h // 24
        draw.rectangle([(w - rule_w) // 2, y, (w + rule_w) // 2, y + max(2, h // 400)],
                       fill=CARD_ACCENT)
        y += h // 20
        y += _center_text(draw, job.get("site", ""), _font(h // 12), y, w, CARD_TEXT) + h // 26
        if job.get("address"):
            _center_text(draw, job["address"], _font(h // 32, bold=False), y, w, CARD_DIM)
    else:
        agent = job.get("agent") or {}
        y = int(h * 0.34)
        if agent.get("name"):
            y += _center_text(draw, agent["name"], _font(h // 14), y, w, CARD_TEXT) + h // 30
            contact = " · ".join(x for x in (agent.get("phone"), agent.get("email")) if x)
            if contact:
                y += _center_text(draw, contact, _font(h // 26, bold=False), y, w, CARD_DIM) + h // 30
            if agent.get("brokerage"):
                y += _center_text(draw, agent["brokerage"], _font(h // 30, bold=False),
                                  y, w, CARD_DIM) + h // 30
            y += h // 24
        draw.rectangle([(w - rule_w) // 2, y, (w + rule_w) // 2, y + max(2, h // 400)],
                       fill=CARD_ACCENT)
        y += h // 20
        y += _center_text(draw, BRAND_LINE, _font(h // 24), y, w, CARD_ACCENT) + h // 30
        _center_text(draw, BRAND_URL, _font(h // 32, bold=False), y, w, CARD_DIM)
    img.save(out_path)
    return out_path


# ─── Map card (GIS overlay) ──────────────────────────────────────────────────

def _clip_flight_tracks(job: dict, min_dt_s: float = 0.5) -> list[list[tuple[float, float]]]:
    """One (lat, lon) track per clip SRT sidecar — clips are separate
    flights, so their tracks must never be joined into one line. Frames
    are thinned to ~1/min_dt_s Hz: per-frame quantized GPS draws as boxy
    zigzag at 30 Hz."""
    from gis_export import load_tracks
    srts = [clip.get("srt_path")
            for clip in job.get("inputs", {}).get("clips", [])]
    srts = [s for s in srts if s and Path(s).exists()]
    return [[(f["lat"], f["lon"]) for f in frames]
            for _name, frames in load_tracks(srts, min_dt_s)]


def _job_boundary(job: dict) -> list[tuple[float, float]]:
    """Property boundary (lat, lon) polygon from the job's KML, if any."""
    kml = job.get("kml_path")
    if not (kml and Path(kml).exists()):
        return []
    try:
        from sentinel_core.spatial import parse_kml
        return list(parse_kml(kml))
    except Exception:
        return []


def _latlon_to_px(points, bounds, rect):
    """Map (lat, lon) points into a pixel rect, aspect-true (equirectangular)."""
    min_lat, max_lat, min_lon, max_lon = bounds
    rx, ry, rw, rh = rect
    mid_lat = math.radians((min_lat + max_lat) / 2)
    span_x = max((max_lon - min_lon) * math.cos(mid_lat), 1e-9)
    span_y = max(max_lat - min_lat, 1e-9)
    scale = min(rw / span_x, rh / span_y)
    used_w, used_h = span_x * scale, span_y * scale
    ox, oy = rx + (rw - used_w) / 2, ry + (rh - used_h) / 2
    return [(ox + (lon - min_lon) * math.cos(mid_lat) * scale,
             oy + used_h - (lat - min_lat) * scale)
            for lat, lon in points]


def make_map_card(job: dict, out_path: str,
                  size: tuple[int, int] = (MASTER_W, MASTER_H)) -> str | None:
    """Render a flight-path/location map card PNG in the title-card style.

    Draws the KML property boundary (when the job carries one) and the flight
    track from the clips' SRT telemetry. Returns None when the job has neither
    — callers then skip the card.
    """
    tracks = _clip_flight_tracks(job)
    boundary = _job_boundary(job)
    if not tracks and len(boundary) < 3:
        return None

    w, h = size
    img = Image.new("RGB", size, CARD_BG)
    draw = ImageDraw.Draw(img)

    # Header / footer in the shared card style
    y = int(h * 0.06)
    y += _center_text(draw, job.get("site", ""), _font(h // 18), y, w, CARD_TEXT) + h // 60
    _center_text(draw, "FLIGHT PATH", _font(h // 34), y, w, CARD_ACCENT)
    if job.get("address"):
        _center_text(draw, job["address"], _font(h // 36, bold=False),
                     int(h * 0.92), w, CARD_DIM)

    # Drawing area between header and footer, 10% padded bounds
    everything = [p for t in tracks for p in t] + boundary
    lats = [p[0] for p in everything]
    lons = [p[1] for p in everything]
    pad_lat = max((max(lats) - min(lats)) * 0.1, 1e-5)
    pad_lon = max((max(lons) - min(lons)) * 0.1, 1e-5)
    bounds = (min(lats) - pad_lat, max(lats) + pad_lat,
              min(lons) - pad_lon, max(lons) + pad_lon)
    rect = (w * 0.15, h * 0.20, w * 0.70, h * 0.66)

    if len(boundary) >= 3:
        overlay = Image.new("RGBA", size, (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        pts = _latlon_to_px(boundary, bounds, rect)
        odraw.polygon(pts, fill=(*CARD_ACCENT, 40))
        # compositing rebinds img/draw — anything drawn on the old handles
        # before this point is baked in, anything after must use these
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)
        draw.line(pts + pts[:1], fill=CARD_DIM, width=max(2, h // 540))

    if tracks:
        r = max(6, h // 180)
        for track in tracks:
            pts = _latlon_to_px(track, bounds, rect)
            draw.line(pts, fill=CARD_ACCENT, width=max(3, h // 360),
                      joint="curve")
        first = _latlon_to_px(tracks[0][:1], bounds, rect)[0]
        last = _latlon_to_px(tracks[-1][-1:], bounds, rect)[0]
        draw.ellipse([first[0] - r, first[1] - r, first[0] + r, first[1] + r],
                     fill=CARD_TEXT)
        draw.ellipse([last[0] - r, last[1] - r, last[0] + r, last[1] + r],
                     outline=CARD_TEXT, width=max(2, r // 3))

    img.save(out_path)
    return out_path


# ─── ffmpeg assembly ─────────────────────────────────────────────────────────

def build_assembly_cmd(plan: list[dict], clip_audio: dict, card_pngs: dict,
                       out_path: str, music_track: str | None,
                       width: int = MASTER_W, height: int = MASTER_H,
                       clip_luts: dict | None = None) -> list[str]:
    """Build the single-pass ffmpeg command for the master reel.

    clip_audio: {path: has_audio} from probing. card_pngs: {"intro": png, "outro": png}.
    clip_luts: {clip_path: cube_lut_path} — applied per clip before scaling,
    so D-Log clips get graded while normal-profile clips and cards pass through.
    """
    clip_luts = clip_luts or {}
    total = plan_duration(plan)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"]
    silent_needed = []

    for item in plan:
        if item["type"] == "card":
            cmd += ["-loop", "1", "-t", f"{item['dur']:.3f}",
                    "-i", card_pngs[item["card"]]]
        else:
            cmd += ["-ss", f"{item['start']:.3f}", "-t", f"{item['dur']:.3f}",
                    "-i", item["path"]]
    n_seg = len(plan)

    music_idx = None
    if music_track:
        cmd += ["-i", str(music_track)]
        music_idx = n_seg
    else:
        # native audio: silent sources stand in for cards / soundless clips
        for i, item in enumerate(plan):
            if item["type"] == "card" or not clip_audio.get(item.get("path"), False):
                silent_needed.append(i)
        for _ in silent_needed:
            cmd += ["-f", "lavfi", "-t", "10", "-i", "anullsrc=r=48000:cl=stereo"]

    filters = []
    for i, item in enumerate(plan):
        lut = clip_luts.get(item.get("path")) if item["type"] == "clip" else None
        grade = _lut_filter(lut) + "," if lut else ""
        filters.append(
            f"[{i}:v]{grade}"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={FPS},setsar=1,format=yuv420p,settb=AVTB[v{i}]")

    durations = [p["dur"] for p in plan]
    offsets = xfade_offsets(durations)
    last = "v0"
    for i in range(1, n_seg):
        out = f"vx{i}" if i < n_seg - 1 else "vout"
        filters.append(f"[{last}][v{i}]xfade=transition=fade:duration={XFADE_S}:"
                       f"offset={offsets[i - 1]}[{out}]")
        last = out
    if n_seg == 1:
        filters.append("[v0]null[vout]")

    if music_track is not None:
        filters.append(f"[{music_idx}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
                       f"afade=t=out:st={max(0.0, total - 2):.3f}:d=2[aout]")
    else:
        silent_iter = iter(range(n_seg, n_seg + len(silent_needed)))
        alabels = []
        for i, item in enumerate(plan):
            if i in silent_needed:
                src = f"[{next(silent_iter)}:a]"
            else:
                src = f"[{i}:a]"
            filters.append(f"{src}atrim=0:{durations[i]:.3f},asetpts=PTS-STARTPTS,"
                           f"aresample=48000,aformat=channel_layouts=stereo[a{i}]")
            alabels.append(f"a{i}")
        last_a = alabels[0]
        for i in range(1, n_seg):
            out = f"ax{i}" if i < n_seg - 1 else "aout"
            filters.append(f"[{last_a}][{alabels[i]}]acrossfade=d={XFADE_S}[{out}]")
            last_a = out
        if n_seg == 1:
            filters.append(f"[{alabels[0]}]anull[aout]")

    cmd += ["-filter_complex", ";".join(filters),
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "22",
            # pin 4:2:0 main profile — without this the graph negotiates 4:4:4,
            # which most players/hardware decoders cannot play
            "-pix_fmt", "yuv420p", "-profile:v", "main",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{total:.3f}", "-movflags", "+faststart", str(out_path)]
    return cmd


def derive_cut(master: str, out_path: str, kind: str) -> list[str]:
    """ffmpeg command for a derived cut from the master: web_1080p or vertical_916."""
    if kind == "web_1080p":
        vf = "scale=1920:1080"
    elif kind == "vertical_916":
        # even crop width keeps 4:2:0 chroma alignment
        vf = "crop='trunc(ih*9/16/2)*2':ih,scale=1080:1920"
    else:
        raise ValueError(f"unknown cut {kind!r}")
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-i", str(master),
            "-vf", vf + ",format=yuv420p",
            "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23",
            "-profile:v", "high",
            "-c:a", "copy", "-movflags", "+faststart", str(out_path)]


# ─── Orchestration ───────────────────────────────────────────────────────────

def default_output_dir(job: dict) -> Path:
    from datetime import datetime
    safe_site = "".join(c for c in job["site"] if c not in '<>:"/\\|?*').strip()
    return Path("E:/Portfolio") / safe_site / datetime.now().strftime("%Y-%m-%d") / "reel"


def render_reel(job: dict, music_track: Path | None,
                work_dir: str | None = None, log=print) -> dict:
    """Render the full deliverable set for a job. Returns outputs dict."""
    clips_in = job["inputs"]["clips"]
    if not clips_in:
        raise NotImplementedError(
            "photos-only (Ken Burns) rendering not implemented yet — job has no clips")

    out_dir = Path(job["outputs"]["dir"] or default_output_dir(job))
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="reel_"))
    work.mkdir(parents=True, exist_ok=True)

    lut = resolve_lut(job)
    log(f"Analyzing {len(clips_in)} clips...")
    analyzed, clip_audio, clip_luts = [], {}, {}
    dlog_seen = False
    for clip in clips_in:
        info = probe_media(clip["path"])
        clip_audio[clip["path"]] = info["has_audio"]
        samples = sample_clip(find_proxy(clip["path"]))
        analyzed.append({"path": clip["path"], "duration": info["duration"],
                         "samples": samples})
        color_md = clip_color_mode(clip.get("srt_path"))
        is_dlog = bool(color_md and color_md.startswith("dlog"))
        dlog_seen = dlog_seen or is_dlog
        if is_dlog and lut:
            clip_luts[clip["path"]] = lut
        log(f"  {clip['name']}: {info['duration']:.1f}s, {len(samples)} samples"
            + (f", {color_md} -> LUT" if is_dlog and lut else ""))
    if clip_luts:
        log(f"Color: {len(clip_luts)}/{len(clips_in)} clips D-Log -> {Path(lut).name}")
    elif dlog_seen:
        log("WARNING: D-Log clips detected but no LUT available — reel will be flat. "
            f"Expected LUT at {DEFAULT_LUT}")

    map_png = None
    if job["render"].get("map_card"):
        # Optional decoration — never let a card error fail the reel
        try:
            map_png = make_map_card(job, str(work / "map.png"))
        except Exception as exc:
            log(f"Map card failed ({exc}) — skipping")
        else:
            if map_png:
                log("Map card: flight path rendered from SRT/KML")
            else:
                log("Map card requested but no SRT/KML data — skipping")

    target = float(job["render"]["duration_s"])
    plan = plan_reel(analyzed, target, map_card=bool(map_png))
    n_clips = sum(1 for p in plan if p["type"] == "clip")
    n_cards = len(plan) - n_clips
    log(f"Edit plan: {n_clips} segments + {n_cards} cards = "
        f"{plan_duration(plan):.1f}s timeline (target {target:.0f}s)")

    cards = {"intro": make_card("intro", job, str(work / "intro.png")),
             "outro": make_card("outro", job, str(work / "outro.png"))}
    if map_png:
        cards["map"] = map_png

    job_id = job["job_id"]
    outputs = {}
    master = out_dir / f"{job_id}_master_4k.mp4"
    log(f"Rendering 4K master (hevc_nvenc){' with music' if music_track else ' with native audio'}...")
    subprocess.run(build_assembly_cmd(plan, clip_audio, cards, str(master),
                                      str(music_track) if music_track else None,
                                      clip_luts=clip_luts),
                   check=True)
    outputs["master_4k"] = str(master)

    for kind in ("web_1080p", "vertical_916"):
        if kind not in job["outputs"]["deliverables"]:
            continue
        out = out_dir / f"{job_id}_{kind}.mp4"
        log(f"Deriving {kind}...")
        subprocess.run(derive_cut(str(master), str(out), kind), check=True)
        outputs[kind] = str(out)

    (out_dir / f"{job_id}_edit-plan.json").write_text(
        json.dumps({"plan": plan, "timeline_s": plan_duration(plan)}, indent=2),
        encoding="utf-8")
    return outputs
