# Reel Job Spec — Sortie → Reel Renderer Handoff

**Schema:** `sai.reel-job/1` · **Owner:** `reel_job.py` · **Consumer:** `reel_renderer.py`
**Vault plan:** `obsidian-dev/projects/sortie/video-reel-renderer.md`

## Flow

1. Post-mission, Sortie (or the `create` CLI) builds a job with `build_reel_job()` — package presets (duration, music mood, overlay flags) are baked in at creation time so the renderer never carries business rules.
2. Job is written to `reel-queue/{job_id}.json` (gitignored runtime artifacts, same convention as `video-queue/`).
3. `reel_renderer.py run` claims the oldest job, renders, and writes `{job_id}.result.json` beside it. Sortie polls for the result and attaches outputs to delivery.

## Queue state machine (by file extension)

| Extension | State |
|---|---|
| `.json` | queued — visible to `next_job()` |
| `.rendering` | claimed by a renderer process |
| `.done` | finished OK — see `{job_id}.result.json` |
| `.failed` | render error — see `{job_id}.result.json` |

Crash recovery: a stale `.rendering` file can be re-queued with `release_job()`.

## Job file (`sai.reel-job/1`)

```json
{
  "schema": "sai.reel-job/1",
  "job_id": "20260705_120000_806-meads-ct",
  "created": "20260705_120000",
  "package": "listing_pro",
  "site": "806 Meads Ct",
  "address": "806 Meads Ct, Chesapeake, VA 23322",
  "agent": {"name": "Jane Realtor", "phone": "757-555-0100",
            "email": null, "brokerage": null},
  "front_bearing": 210.0,
  "kml_path": null,
  "source_dir": "F:/DCIM/DJI_001",
  "inputs": {
    "clips":  [{"path": "...MP4", "name": "...", "has_srt": true, "srt_path": "...SRT"}],
    "panos":  ["...pano.jpg"],
    "photos": ["...oblique.jpg"]
  },
  "music":   {"mood": "upbeat", "track": null},
  "outputs": {"dir": null, "deliverables": ["master_4k", "web_1080p", "vertical_916"]},
  "render":  {"duration_s": 60, "lut": null,
              "overlay_address": true, "agent_card": true}
}
```

Field notes:

- **`inputs.clips`** reuses Sortie's video-scan shape (`_scan_videos` in `sortie.py`); `panos`/`photos` are plain path strings. At least one input list must be non-empty — photos-only is valid (Ken Burns reel, Listing Lite fallback).
- **`music.mood`** derived from package; **`music.track`** set = explicit override, skips pool selection.
- **`outputs.dir`** `null` → renderer defaults to `E:/Portfolio/{site}/{YYYY-MM-DD}/reel/`.
- **`render.agent_card`** is `true` only when agent info exists; renderer falls back to the SAI outro card otherwise.
- **`render.lut`** `null` → renderer default LUT (matched to Lightroom presets, Phase 3).

## Package presets (LOCKED 2026-07-05)

| Package | `duration_s` | `music_mood` |
|---|---|---|
| `listing_lite` | 45 | calm |
| `listing_pro` | 60 | upbeat |
| `luxury` | 90 | upbeat |
| `commercial_marketing` | 90 | upbeat |
| `construction` | 60 | corporate |
| `inspection` | 60 | corporate |

Address overlay and agent contact card: **all packages** (locked decisions 2 & 3). Construction/Inspection durations default to 60s — not part of the locked decision set, adjust in `PACKAGE_PRESETS` if needed.

## Result manifest (`sai.reel-result/1`)

```json
{
  "schema": "sai.reel-result/1",
  "job_id": "20260705_120000_806-meads-ct",
  "status": "done",
  "finished": "20260705_121412",
  "outputs": {"master_4k": "...mp4", "web_1080p": "...mp4", "vertical_916": "...mp4"},
  "music_track": "music-pool/upbeat/upbeat_take2.wav",
  "error": null
}
```

## Music pool

`music-pool/{calm,upbeat,corporate}/` next to the renderer — pre-generated, approved ACE-Step takes only, never per-video generation. Selection is seeded by `job_id` (`pick_music_track`), so re-renders always get the same track. Empty pool → renderer uses placeholder audio and says so.

## CLI

```
python reel_renderer.py create --package listing_pro --site "806 Meads Ct" \
    --address "..." --source-dir F:/DCIM/DJI_001 --agent-name "Jane Realtor"
python reel_renderer.py validate reel-queue/<job>.json
python reel_renderer.py next
python reel_renderer.py run          # claims next queued job
```

The render core (`reel_render.py`: clip scoring, edit planning, xfade assembly, NVENC encode, derived cuts) is live — validated end-to-end 2026-07-05. Photos-only (Ken Burns) jobs are the one case `run` still releases back to the queue with exit code 2. All encodes pin `yuv420p` + standard profiles; unpinned NVENC output negotiates to unplayable 4:4:4 (see debug journal 2026-07-05).
