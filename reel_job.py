"""
reel_job.py — Sentinel Aerial
Job-file contract between Sortie and the video reel renderer.

Sortie writes a reel job JSON into reel-queue/ after a mission is sorted;
reel_renderer.py claims it, renders, and writes a result manifest beside it.
Package business rules (duration, music mood, overlay flags — template
decisions locked 2026-07-05) are baked into the job file at creation time,
so the renderer only ever executes what the job says.

Queue state machine, by file extension (mirrors video-queue/ conventions):
    {job_id}.json       queued, visible to next_job()
    {job_id}.rendering  claimed by a renderer process
    {job_id}.done       finished OK  (result in {job_id}.result.json)
    {job_id}.failed     render error (result in {job_id}.result.json)

Full spec: docs/reel-job-spec.md
"""

import json
import random
import re
from datetime import datetime
from pathlib import Path

SCHEMA_ID = "sai.reel-job/1"
RESULT_SCHEMA_ID = "sai.reel-result/1"
QUEUE_DIR = Path(__file__).parent / "reel-queue"

MUSIC_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a"}

# ─── Package presets (template decisions locked 2026-07-05) ──────────────────
# Durations: 45/60/90 by tier. Moods: calm=Lite, upbeat=Pro and up,
# corporate=Construction/Inspection. Address overlay + agent card: ALL packages.
PACKAGE_PRESETS = {
    "listing_lite":         {"duration_s": 45, "music_mood": "calm"},
    "listing_pro":          {"duration_s": 60, "music_mood": "upbeat"},
    "luxury":               {"duration_s": 90, "music_mood": "upbeat"},
    "commercial_marketing": {"duration_s": 90, "music_mood": "upbeat"},
    "construction":         {"duration_s": 60, "music_mood": "corporate"},
    "inspection":           {"duration_s": 60, "music_mood": "corporate"},
}

DELIVERABLES = ("master_4k", "web_1080p", "vertical_916")


# ─── Job construction ────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    """Lowercase, alphanumeric-and-dash slug for job IDs and folder names."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "unnamed"


def build_reel_job(
    package: str,
    site: str,
    address: str = "",
    source_dir: str = "",
    clips: list[dict] | None = None,
    panos: list[str] | None = None,
    photos: list[str] | None = None,
    agent: dict | None = None,
    front_bearing: float | None = None,
    kml_path: str | None = None,
    output_dir: str | None = None,
    created: str | None = None,
) -> dict:
    """Build a reel job dict with package presets applied.

    clips entries use Sortie's video-scan shape: {path, name, has_srt, srt_path}.
    panos/photos are plain path strings. `created` accepts a pre-formatted
    YYYYMMDD_HHMMSS timestamp (tests); defaults to now.
    """
    preset = PACKAGE_PRESETS.get(package)
    if preset is None:
        raise ValueError(
            f"unknown package {package!r} — expected one of {sorted(PACKAGE_PRESETS)}")

    ts = created or datetime.now().strftime("%Y%m%d_%H%M%S")
    return {
        "schema": SCHEMA_ID,
        "job_id": f"{ts}_{slugify(site)}",
        "created": ts,
        "package": package,
        "site": site,
        "address": address,
        "agent": agent,
        "front_bearing": front_bearing,
        "kml_path": kml_path,
        "source_dir": source_dir,
        "inputs": {
            "clips": clips or [],
            "panos": panos or [],
            "photos": photos or [],
        },
        "music": {"mood": preset["music_mood"], "track": None},
        "outputs": {
            "dir": output_dir,  # renderer defaults to E:/Portfolio/{site}/{date}/reel
            "deliverables": list(DELIVERABLES),
        },
        "render": {
            "duration_s": preset["duration_s"],
            "lut": None,
            "overlay_address": True,           # locked: all packages
            "agent_card": bool(agent and agent.get("name")),
        },
    }


def validate_reel_job(job: dict) -> list[str]:
    """Return a list of problems; empty list means the job is valid."""
    problems = []
    if job.get("schema") != SCHEMA_ID:
        problems.append(f"schema must be {SCHEMA_ID!r}, got {job.get('schema')!r}")
    if job.get("package") not in PACKAGE_PRESETS:
        problems.append(f"unknown package {job.get('package')!r}")
    if not (job.get("site") or "").strip():
        problems.append("site must be non-empty")
    if not job.get("job_id"):
        problems.append("job_id must be non-empty")

    inputs = job.get("inputs") or {}
    clips = inputs.get("clips") or []
    if not (clips or inputs.get("panos") or inputs.get("photos")):
        problems.append("at least one input required (clips, panos, or photos)")
    for i, clip in enumerate(clips):
        if not clip.get("path"):
            problems.append(f"clips[{i}] missing path")

    render = job.get("render") or {}
    if not isinstance(render.get("duration_s"), (int, float)) or render["duration_s"] <= 0:
        problems.append("render.duration_s must be a positive number")
    return problems


# ─── Queue operations ────────────────────────────────────────────────────────

def enqueue_reel_job(job: dict, queue_dir: Path = QUEUE_DIR) -> Path:
    """Validate and write the job to the queue. Returns the job file path."""
    problems = validate_reel_job(job)
    if problems:
        raise ValueError("invalid reel job: " + "; ".join(problems))
    queue_dir = Path(queue_dir)
    queue_dir.mkdir(parents=True, exist_ok=True)
    path = queue_dir / f"{job['job_id']}.json"
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    return path


def load_reel_job(path: Path) -> dict:
    """Load and validate a job file; raises ValueError on schema problems."""
    job = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = validate_reel_job(job)
    if problems:
        raise ValueError(f"invalid reel job {path}: " + "; ".join(problems))
    return job


def next_job(queue_dir: Path = QUEUE_DIR) -> Path | None:
    """Oldest queued (.json) job file, or None. Job IDs sort chronologically."""
    queue_dir = Path(queue_dir)
    if not queue_dir.is_dir():
        return None
    queued = sorted(p for p in queue_dir.glob("*.json")
                    if not p.name.endswith(".result.json"))
    return queued[0] if queued else None


def claim_job(path: Path) -> Path:
    """Mark a queued job as being rendered (.json -> .rendering)."""
    return _transition(path, ".rendering")


def release_job(path: Path) -> Path:
    """Return a claimed job to the queue (.rendering -> .json)."""
    return _transition(path, ".json")


def complete_job(path: Path, outputs: dict, music_track: str | None = None) -> Path:
    """Write a success result manifest and mark the job .done."""
    _write_result(path, status="done", outputs=outputs,
                  music_track=music_track, error=None)
    return _transition(path, ".done")


def fail_job(path: Path, error: str) -> Path:
    """Write a failure result manifest and mark the job .failed."""
    _write_result(path, status="failed", outputs={}, music_track=None, error=error)
    return _transition(path, ".failed")


def _transition(path: Path, new_suffix: str) -> Path:
    path = Path(path)
    target = path.with_suffix(new_suffix)
    path.rename(target)
    return target


def _write_result(job_path: Path, status: str, outputs: dict,
                  music_track: str | None, error: str | None):
    job_path = Path(job_path)
    result = {
        "schema": RESULT_SCHEMA_ID,
        "job_id": job_path.stem,
        "status": status,
        "finished": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "outputs": outputs,
        "music_track": music_track,
        "error": error,
    }
    result_path = job_path.with_name(f"{job_path.stem}.result.json")
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")


# ─── Music selection ─────────────────────────────────────────────────────────

def pick_music_track(job: dict, pool_dir: Path) -> Path | None:
    """Pick a track from the approved pool, seeded by job_id.

    Same job always gets the same track, so re-renders are consistent.
    An explicit job["music"]["track"] path overrides pool selection.
    Returns None when the mood folder is empty or missing (pool not
    generated yet — renderer falls back to silent/placeholder).
    """
    override = (job.get("music") or {}).get("track")
    if override:
        return Path(override)
    mood = (job.get("music") or {}).get("mood", "calm")
    mood_dir = Path(pool_dir) / mood
    if not mood_dir.is_dir():
        return None
    tracks = sorted(p for p in mood_dir.iterdir()
                    if p.suffix.lower() in MUSIC_EXTENSIONS)
    if not tracks:
        return None
    return random.Random(job["job_id"]).choice(tracks)
