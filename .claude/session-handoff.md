# Session Handoff
**Date:** 2026-07-12
**Branch:** dev (head `575413c`, pushed — dev == origin/dev)

## Accomplished
- **GIS integration feature** (`38b9073`), all four parts Adam scoped:
  1. VARI vegetation analysis pipeline step — `vegetation_analysis.py` bridges to drone-pipeline's headless QGIS script; preset-gated (`vegetation_analysis` flag, vegetation only); paths: env `QGIS_PYTHON`/`VEG_SCRIPT` > `sortie_settings.json` > defaults
  2. GIS/veg outputs in deliverables + "VARI Vegetation Index Analysis" report section
  3. Reel map card (`render.map_card`, all packages) — 3.0s flight-path card before outro; one polyline per clip SRT (never joined), 0.5s decimation, KML boundary fill; segmentation absorbs the duration
  4. `gis_export.py` — photo points (GeoJSON+CSV), per-clip tracks (GeoJSON), mission KML in both `process_job` and `portfolio_only`
- **/qcheckf refactor** (`c51ce22`): shared `load_tracks()` dedup
- **GIS delivery policy locked by Adam** (`d376d1f`): client package gets GIS exports ONLY for property_survey / construction_progress / vegetation (`gis_delivery` preset flag); all other job types → internal `_gis/`. drive_delivery skips `_`-prefixed folders at any depth — also fixed pre-existing leak of `_report_thumbs/` + `_mipmap_work/` into client Drive packages
- **Codex cross-model audit + hardening fixes** (`575413c`): GIS/VARI pipeline blocks and reel map-card call now survive any runtime error (were ImportError-only — a permissions/disk error could abort a paid job after ODM finished); veg out-dir mkdir guarded; tautological test assertion fixed
- 428 tests passing; commit history AI-trailer-free per repo GH-2 rule

## Next Steps
- First live vegetation-mission run: verify the QGIS bridge on a real ortho end-to-end (bridge is subprocess-mock tested; the external script was live-tested 7/09 in drone-pipeline)
- Practice flight validates the map card on real property-shoot tracks (fireworks corpus is hover-heavy → GPS jitter dominates at ~30 m extent)
- GUI touchpoints offered, not yet requested: QGIS status indicator next to NodeODM check, Advanced settings fields for QGIS paths, GIS line in completion summary
- Carried: Phase 4 Remotion templates, photos-only Ken Burns path, Phase 5 Sortie GUI reel queue + delivery wiring

## Known Issues
- **Tech debt (pre-existing, deferred from audit)**: drive_delivery flattens folders nested deeper than one level (upload targets `rel.parts[0]` only) — harmless while all deliverables are one level deep; fix before shipping nested tile sets
- Map card on hover-heavy footage reads as GPS-noise scribble (acceptable; real shoots span the parcel)
- VARI timeout is 3600s; very large orthos under QGIS python may need more
- `sortie_settings.json` modified + `rtklib/` untracked — pre-existing local state, NOT from this session, left uncommitted deliberately

## Key Decisions
- GIS delivery policy per job type via `gis_delivery` preset flag; internal-vs-delivered via `_`-prefix folder convention; client report lists only actually-delivered files
- Vegetation analysis preset-gated and graceful-skip everywhere — a missing QGIS install or any runtime GIS/VARI error never breaks a mission (hardened post-audit)
- Reel map card: request-not-guarantee; per-clip polylines; PIL card style (Remotion replaces in Phase 4)
- Audit M2 (Drive folder flattening) deferred as pre-existing debt rather than fixed

## Uncommitted Changes
- `sortie_settings.json` (pre-existing, not mine), untracked `rtklib/`, and `.claude/` state files — everything from this session is committed and pushed (`38b9073`, `c51ce22`, `d376d1f`, `575413c`)
