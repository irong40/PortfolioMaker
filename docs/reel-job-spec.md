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
              "overlay_address": true, "agent_card": true, "map_card": true}
}
```

Field notes:

- **`inputs.clips`** reuses Sortie's video-scan shape (`_scan_videos` in `sortie.py`); `panos`/`photos` are plain path strings. At least one input list must be non-empty. Photos-only jobs render as **Ken Burns reels** (Listing Lite fallback): photos + panos pooled, chronological, even-spread selection when there are more stills than segments, alternating zoom-in/zoom-out/pan moves (deterministic by position). When clips exist the reel is clips-only; stills are ignored.
- **`front_bearing`** is informational metadata (recorded for future front-elevation/orientation use); the renderer does not read it.
- **`render.overlay_address`** (default `true`, all packages — locked decision 2): centered address lower-third on body segments (clips + photos), boxed for legibility, positioned to survive the 9:16 center crop. Cards keep their own address rendering. Skipped with a log warning when the job has no address or the overlay font is missing.
- **`render.agent_card`** `false` forces the SAI-only outro even when agent data is present on the job.
- **`music.mood`** derived from package; **`music.track`** set = explicit override, skips pool selection.
- **`outputs.dir`** `null` → renderer defaults to `E:/Portfolio/{site}/{YYYY-MM-DD}/reel/`.
- **`render.agent_card`** defaults to `true` only when agent info exists; the renderer falls back to the SAI outro card when it's `false` or agent data is absent.
- **`render.lut`** `null` → repo default LUT (`assets/luts/dji_dlog_m_to_rec709.cube`, DJI's official D-Log M → Rec.709). Set a path to override with a custom `.cube`. Applied **per clip**, only when that clip's SRT sidecar reports `[color_md: dlog_*]` — normal-profile clips and title cards are never graded, so mixed-profile reels stay correct. An explicit path that doesn't exist disables grading (with a flat-reel warning in the log) rather than silently falling back.
- **`render.map_card`** (default `true`, all packages): a 3.0s flight-path/location card before the outro, drawn from the clips' SRT telemetry (one polyline per clip — clips are separate flights) plus the `kml_path` boundary when present. The card's duration is absorbed into the segmentation, so the timeline still hits `duration_s`. When the job carries neither SRT nor KML the renderer skips the card silently — the flag is a request, not a guarantee.

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
