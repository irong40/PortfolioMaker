# Session Handoff
**Date:** 2026-04-20
**Branch:** dev

## Accomplished
- Added `property_highlights.py` module — GPS-registered animated property boundary overlay for drone footage (DJI SRT + KML → PIL animation → ffmpeg composite)
- Added `PropertyHighlightsDialog` to `sortie.py` — Toplevel dialog in Tools menu with video picker, KML auto-match, heading override, scale-down, progress bar + cancel
- Added video detection to Sortie scan flow — after photo scan, finds MP4s + SRTs in source folder, shows "Videos found" panel with listbox
- Added "Send to Content Agent" button — writes timestamped JSON manifest to `video-queue/` for pickup by video-use workflow
- Fixed MP4 dedup bug — Windows case-insensitive FS matched both `*.mp4` and `*.MP4`, causing doubled lists
- Built and tested full video render pipeline for 806 Meads CT training footage:
  - 5-beat 59s reel (wide vista → neighborhood → waterway → street → second neighborhood)
  - DLog-M → warm cinematic grade (S-curve, highlight rolloff, saturation boost)
  - H&G music bed (`2026-04-20_04_cinematic_tender_moment.wav`)
  - PIL brand card end overlay (purple/gold Sentinel Aerial Inspections)
- Marked 806 Meads manifest as `.SKIP` — footage was personal neighborhood test, not for publishing

## Next Steps
- Shoot real client job footage → run through Sortie video pipeline for first publishable reel
- `git push origin dev` when ready to sync to remote (14 commits ahead)
- Consider adding `.SKIP` file handling to the queue reader so it ignores them automatically
- Add road line overlay support to `property_highlights.py` (second KML with LineString)
- Add `--before-after` wipe format to `property_highlights.py`
- Add `--social` 1080x1920 vertical crop output

## Known Issues
- `sortie_settings.json` has unstaged local settings drift — expected, not a code issue
- `video-queue/` folder is untracked — intentional (manifests are runtime artifacts, not code)
- Brand card uses `sentinelaerialinspections.com` — verify this is the correct domain

## Key Decisions
- Video queue manifest format: JSON with `{created, site, source_dir, videos[], targets[], notes}`
- Manifests marked `.SKIP` (not deleted) to preserve audit trail
- Grade pipeline: per-segment extraction with grade → lossless concat → composite overlay → music mix (no double-encode)
- DLog-M grade: `curves all S-curve + warm tint (r up, b down) + eq saturation=1.42`
- Music source: H&G daily pipeline `cinematic` or `cafe-jazz` tracks from `daily_output/`

## Uncommitted Changes
- `sortie_settings.json` — local runtime settings, intentionally not committed
- `.claude/` — session artifacts, not committed
- `video-queue/` — manifest files, not committed
