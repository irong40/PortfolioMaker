# Milestones

## v1.0 — Photo Classification & Sorting (Complete)

**Shipped:** 2026-03-16
**Phases:** 1-7 (pre-GSD, 7 git commits)

**What shipped:**
- Tkinter desktop GUI for drone photo sorting
- Gimbal pitch classification (nadir/oblique/unknown)
- GPS bounding box filtering
- Flat export for WebODM-ready subsets
- Manifest.json generation
- Drone platform detection (DJI)
- Fallback EXIF extraction (works without drone-pipeline)
- 26 automated tests for core logic

**Post-ship audit (v1.0.1):**
- Fixed file operation error handling (CQ1)
- Fixed hardcoded DRONE_PIPELINE_DIR (S1)
- Fixed partial bbox validation (CQ3)
- Narrowed fallback exception handling (CQ2)
- Added .tif/.tiff support
- Added unknown_dir tracking in manifest
- Cleaned unused imports
- Added requirements-dev.txt
- Cross-model audit completed (Claude Code + Codex gpt-5.4): verdict REWORK → fixes applied
