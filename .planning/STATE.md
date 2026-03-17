# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-16)

**Core value:** A drone operator can point at a folder of photos, pick what they want to produce, and get a complete branded deliverable package.
**Current focus:** v2.0 — All 5 phases complete, pending manual E2E verification

## Current Position

Phase: 5 of 5 (Integration Testing)
Plan: Complete
Status: Code complete — awaiting manual E2E test
Last activity: 2026-03-16 — All phases implemented, 67 tests passing

Progress: [█████████░] 90%

## Performance Metrics

**Velocity:**
- Total plans completed: 5
- Total execution time: Single session
- Tests: 67 passing (26 photo_classifier + 16 odm_presets + 12 portfolio_service + 13 report_generator)

**By Phase:**

| Phase | Status | Tests | Commit |
|-------|--------|-------|--------|
| 1. ODM Presets | ✓ Complete | 16 | cc344f1 |
| 2. Portfolio Service | ✓ Complete | 12 | d536f29 |
| 3. GUI Redesign | ✓ Complete | manual | fb3c8ce |
| 4. Report Generator | ✓ Complete | 13 | 69479f9 |
| 5. Integration | ○ Pending manual E2E | — | — |

## Accumulated Context

### Decisions

- Three-layer architecture (GUI → Service → Core) approved after cross-model REWORK audit
- Job type presets drive ODM options — user picks intent, not settings
- split:4 required in all ODM presets — MipMap stalled at 443 images on 12GB VRAM
- ReportLab + Folium for reports — matches existing vegetation_report.py pattern
- Portfolio folder: E:\Portfolio\{SiteName}\{YYYY-MM-DD}\ — organized for weekly revisits
- DRONE_PIPELINE_DIR configurable via env var (was hardcoded, fixed in v1.0.1 audit)
- BodyText style renamed to SentinelBody to avoid ReportLab stylesheet collision

### Pending Todos

- Manual E2E test: launch GUI, scan real photos, verify Process + Portfolio Only flows
- Verify NodeODM indicator updates correctly when Docker is running vs stopped

### Blockers/Concerns

None.

## Session Continuity

Last session: 2026-03-16
Stopped at: All code complete, manual testing next
Resume file: None
