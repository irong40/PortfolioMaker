# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-16)

**Core value:** A drone operator can point at a folder of photos, pick what they want to produce, and get a complete branded deliverable package.
**Current focus:** v2.0 — Phase 1: ODM Presets (ready to plan)

## Current Position

Phase: 1 of 5 (ODM Presets)
Plan: — of — in current phase
Status: Ready to plan
Last activity: 2026-03-16 — Roadmap created, v2.0 phases defined

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: —
- Total execution time: —

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

*Updated after each plan completion*

## Accumulated Context

### Decisions

- Three-layer architecture (GUI → Service → Core) approved after cross-model REWORK audit
- Job type presets drive ODM options — user picks intent, not settings
- split:4 required in all ODM presets — MipMap stalled at 443 images on 12GB VRAM
- ReportLab + Folium for reports — matches existing vegetation_report.py pattern
- Portfolio folder: E:\Portfolio\{SiteName}\{YYYY-MM-DD}\ — organized for weekly revisits
- DRONE_PIPELINE_DIR configurable via env var (was hardcoded, fixed in v1.0.1 audit)

### Pending Todos

None yet.

### Blockers/Concerns

None yet.

## Session Continuity

Last session: 2026-03-16
Stopped at: Roadmap written, requirements traced, ready to run /gsd:plan-phase 1
Resume file: None
