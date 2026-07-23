# Session Handoff
**Date:** 2026-07-15
**Branch:** dev (head `3f38e8e`, merged to main + pushed)

## Accomplished (this session — PPK investigation + UX fixes)
- **Root-caused "PPK never worked"** (Adam's report): two independent causes, neither a code bug in the 6/11 fix chain.
  1. **Same-day CORS gap**: NOAA posts daily station RINEX only after the UTC day ends — verified live (ncel DOY 196 = 404, DOY 195/194 = 200). Any same-day PPK run fails with the generic "failed to download CORS data" error Adam saw.
  2. **Convergence wall**: NOAA daily files are 30-second decimated; Hampton Roads stations have NO high-rate/raw data on noaa-cors-pds, and the RINEX 2.11 base is GPS+GLO only. Short captures (both real missions to date: 2.7 and 3.5 min) → ~1% fix rate → Sortie correctly refuses float writes. GLONASS-AR-off retest: no change.
- **Full pipeline re-verified end-to-end** on a copy of `E:\DroneWorkflow\Input_Raw\DJI_202603191126_004_hemphaven` (91 photos): CORS download (ncel 20.9 km), brdc, rnx2rtkp 1040 solutions, 91/91 photos matched — mechanics all work.
- **Shipped `3f38e8e`**: same-day CORS failure now returns a specific "re-run tomorrow" error; `detect_rinex` reads TIME OF LAST OBS → capture duration; GUI banner + CLI warn under 8 min (MIN_RECOMMENDED_OBS_MINUTES). 486 tests pass.
- **Merged dev → main (ff, 13 commits) and pushed both branches** — CRM link, GIS/VARI, D-Log LUT, PPK fixes all now on main.
- Debug journal: `obsidian-dev/debug-journal/2026-07-15-sortie-ppk-cors-same-day.md` (resolved).

## Next Steps
- **First real PPK proof**: post-calibration M4E mapping flight with 10+ min continuous RINEX capture, processed the NEXT day. Property Survey preset (gps-accuracy 0.02 assumes PPK — override if raw GPS).
- Parking-lot survey flow confirmed: create CRM job first → Sortie dropdown → Property Survey preset (prefill, write-back, report push, ledger).
- Carry-over from 7/14: report draft + delivery_drive_url legs still unverified from a real GUI run; consider pinning NodeODM image tag; M4E paperwork/calibration items on Adam (~7/21).

## Known Issues
- PPK fix rate is physics-limited by 30-s CORS base data — flight duration is the only lever. Sub-5-min captures should be treated as raw-GPS jobs.
- NodeODM tasks auto-clean after 48h — recover assets fast after any failure.

## Key Decisions
- No same-day PPK is possible via noaa-cors-pds daily files; workflow = re-run PPK next day (RINEX stays with photos).
- EXIF writes stay gated to fixed (Q=1) solutions only (float bias worse than raw GPS) — unchanged, now better messaged.
- (Non-repo, same session): staff-meeting CMO seat now owns published-content analytics via Blotato (no new content-manager agent); new book project "Starting a Drone Business Using AI" briefed at `obsidian-dev/projects/drone-ai-book/`.

## Uncommitted Changes
- `.claude/` workflow files only; all code committed and pushed (dev = main = `3f38e8e`).
