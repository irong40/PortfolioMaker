"""
reel_renderer.py — Sentinel Aerial
CLI for the video reel renderer. Standalone module Sortie calls post-mission.

Job creation, validation, queue mechanics, and the render core (reel_render.py:
clip analysis, edit planning, xfade assembly, NVENC encode, derived cuts) are
live. Photos-only Ken Burns reels are not implemented yet — `run` releases
those jobs back to the queue.

Usage:
    python reel_renderer.py create --package listing_pro --site "806 Meads Ct" \
        --address "806 Meads Ct, Chesapeake, VA 23322" --source-dir F:/DCIM/DJI_001 \
        [--photos-dir E:/Portfolio/806/oblique] [--agent-name "Jane Realtor" \
        --agent-phone 757-555-0100] [--front-bearing 210] [--kml path.kml]
    python reel_renderer.py validate reel-queue/20260705_120000_806-meads-ct.json
    python reel_renderer.py next
    python reel_renderer.py run [job.json]     # defaults to next queued job
"""

import argparse
import sys
from pathlib import Path

from reel_job import (
    PACKAGE_PRESETS,
    QUEUE_DIR,
    build_reel_job,
    claim_job,
    complete_job,
    enqueue_reel_job,
    fail_job,
    load_reel_job,
    next_job,
    pick_music_track,
    release_job,
    validate_reel_job,
)

MUSIC_POOL_DIR = Path(__file__).parent / "music-pool"

VIDEO_EXTENSIONS = {".mp4", ".mov"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg"}


from reel_render import render_reel


# ─── Folder scanning ─────────────────────────────────────────────────────────

def scan_media(directory: str, extensions: set[str]) -> list[Path]:
    """Media files in a directory, sorted, case-insensitive dedup (Windows FS)."""
    src = Path(directory)
    if not src.is_dir():
        return []
    seen = set()
    found = []
    for f in sorted(src.iterdir()):
        if f.suffix.lower() not in extensions or f.name.lower() in seen:
            continue
        seen.add(f.name.lower())
        found.append(f)
    return found


def scan_clips(directory: str) -> list[dict]:
    """MP4/MOV clips with SRT sidecar detection, Sortie video-scan shape."""
    clips = []
    for mp4 in scan_media(directory, VIDEO_EXTENSIONS):
        srt = mp4.with_suffix(".SRT")
        if not srt.exists():
            srt = mp4.with_suffix(".srt")
        clips.append({
            "path": str(mp4),
            "name": mp4.name,
            "has_srt": srt.exists(),
            "srt_path": str(srt) if srt.exists() else None,
        })
    return clips


# ─── Subcommands ─────────────────────────────────────────────────────────────

def cmd_create(args) -> int:
    agent = None
    if args.agent_name:
        agent = {"name": args.agent_name, "phone": args.agent_phone,
                 "email": args.agent_email, "brokerage": args.agent_brokerage}

    job = build_reel_job(
        package=args.package,
        site=args.site,
        address=args.address,
        source_dir=args.source_dir or "",
        clips=scan_clips(args.source_dir) if args.source_dir else [],
        panos=[str(p) for p in scan_media(args.panos_dir, PHOTO_EXTENSIONS)]
              if args.panos_dir else [],
        photos=[str(p) for p in scan_media(args.photos_dir, PHOTO_EXTENSIONS)]
               if args.photos_dir else [],
        agent=agent,
        front_bearing=args.front_bearing,
        kml_path=args.kml,
        output_dir=args.output_dir,
    )
    try:
        path = enqueue_reel_job(job)
    except ValueError as e:
        print(f"NOT QUEUED — {e}")
        return 1
    n_inputs = sum(len(v) for v in job["inputs"].values())
    print(f"Queued {job['job_id']} ({args.package}, {job['render']['duration_s']}s, "
          f"{job['music']['mood']} music, {n_inputs} input files)")
    print(f"  -> {path}")
    return 0


def cmd_validate(args) -> int:
    try:
        job = load_reel_job(args.job)
    except ValueError as e:
        print(f"INVALID: {e}")
        return 1
    print(f"OK: {job['job_id']} — {job['package']}, {job['render']['duration_s']}s, "
          f"{len(job['inputs']['clips'])} clips / {len(job['inputs']['panos'])} panos / "
          f"{len(job['inputs']['photos'])} photos")
    return 0


def cmd_next(args) -> int:
    path = next_job()
    if path is None:
        print("Queue empty.")
        return 0
    print(path)
    return 0


def cmd_run(args) -> int:
    path = Path(args.job) if args.job else next_job()
    if path is None:
        print("Queue empty — nothing to run.")
        return 0
    job = load_reel_job(path)
    problems = validate_reel_job(job)
    if problems:
        print("INVALID job, refusing to run: " + "; ".join(problems))
        return 1

    claimed = claim_job(path)
    music = pick_music_track(job, MUSIC_POOL_DIR)
    if music is None and job["music"]["track"] is None:
        print(f"NOTE: music pool empty for mood {job['music']['mood']!r} "
              f"({MUSIC_POOL_DIR}) — reel will use placeholder audio")
    try:
        outputs = render_reel(job, music)
    except NotImplementedError as e:
        release_job(claimed)
        print(f"Render core not available yet: {e}")
        print("Job returned to queue untouched.")
        return 2
    except Exception as e:
        fail_job(claimed, error=f"{type(e).__name__}: {e}")
        print(f"FAILED {job['job_id']}: {e}")
        return 1

    complete_job(claimed, outputs=outputs,
                 music_track=str(music) if music else None)
    print(f"DONE {job['job_id']}")
    for name, out in outputs.items():
        print(f"  {name}: {out}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Sentinel Aerial reel renderer — job queue + render CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="build and queue a reel job")
    p_create.add_argument("--package", required=True, choices=sorted(PACKAGE_PRESETS))
    p_create.add_argument("--site", required=True)
    p_create.add_argument("--address", default="")
    p_create.add_argument("--source-dir", help="folder of clips (SRT auto-detected)")
    p_create.add_argument("--panos-dir", help="folder of stitched panoramas")
    p_create.add_argument("--photos-dir", help="folder of stills for Ken Burns segments")
    p_create.add_argument("--agent-name")
    p_create.add_argument("--agent-phone")
    p_create.add_argument("--agent-email")
    p_create.add_argument("--agent-brokerage")
    p_create.add_argument("--front-bearing", type=float)
    p_create.add_argument("--kml", help="property boundary KML for overlay")
    p_create.add_argument("--output-dir")
    p_create.set_defaults(func=cmd_create)

    p_validate = sub.add_parser("validate", help="validate a job file")
    p_validate.add_argument("job")
    p_validate.set_defaults(func=cmd_validate)

    p_next = sub.add_parser("next", help="show the next queued job")
    p_next.set_defaults(func=cmd_next)

    p_run = sub.add_parser("run", help="claim and render a job (next queued by default)")
    p_run.add_argument("job", nargs="?")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
