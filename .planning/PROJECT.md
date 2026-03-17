# Portfolio Maker

## What This Is

Desktop application for Sentinel Aerial Inspections that produces client-ready drone deliverable packages — orthomosaics, 3D models, point clouds, and branded PDF reports. Manual-mode equivalent of the automated drone pipeline, used for building a sales portfolio and demo flights without needing mission IDs, Supabase, or n8n.

## Core Value

A drone operator can point at a folder of photos, pick what they want to produce, and get a complete branded deliverable package ready to hand to a prospect.

## Requirements

### Validated

<!-- Shipped in v1.0 and confirmed valuable. -->

- ✓ **SORT-01**: User can classify drone photos into nadir/oblique by gimbal pitch — v1.0
- ✓ **SORT-02**: User can filter photos by GPS bounding box — v1.0
- ✓ **SORT-03**: User can export filtered photo subset to a flat folder — v1.0
- ✓ **SORT-04**: App generates manifest.json with full metadata — v1.0
- ✓ **SORT-05**: App detects drone platform from EXIF — v1.0
- ✓ **SORT-06**: Fallback metadata extraction works without drone-pipeline — v1.0

### Active

<!-- Current scope: v2.0 milestone. -->

- [ ] User can select a job type that determines processing and output
- [ ] User can submit photos to NodeODM for photogrammetry processing
- [ ] App generates branded PDF reports per job type
- [ ] Output lands in organized portfolio folder by site and date

### Out of Scope

- Supabase integration — portfolio mode runs without backend
- n8n webhook orchestration — manual trigger only
- MipMap Desktop integration — NodeODM/WebODM only for v2.0
- Video processing — handled by drone-pipeline Path V
- Client billing/invoicing — not a business tool
- Multi-user/cloud — single desktop user

## Context

- **Business:** Sentinel Aerial Inspections (Faith & Harmony LLC), FAA Part 107 certified
- **Primary use case:** Weekly construction progress flights at a mall site to build sales portfolio
- **Existing pipeline:** drone-pipeline repo handles automated client deliveries (ingest → n8n → NodeODM → reports)
- **Portfolio Maker is the manual lane** — same quality outputs, no automation required
- **Hardware:** i9-14900F, RTX 5070 (12GB VRAM), 32GB DDR5 — VRAM is the bottleneck for photogrammetry
- **Split-merge required:** All ODM presets use split:4 to stay within 12GB VRAM on large datasets
- **6 job types:** Construction Progress, Property Survey, Roof Inspection, Structures, Vegetation/Land, Real Estate
- **Reports follow vegetation_report.py pattern:** ReportLab PDF + Folium HTML + matplotlib overlays

## Constraints

- **Stack**: Python 3.12, Tkinter (desktop GUI), no web framework
- **Processing**: NodeODM via Docker (localhost:3000), reuses photogrammetry_submit.py from drone-pipeline
- **Reports**: ReportLab + Folium (already in drone-pipeline venv, no new packages)
- **VRAM**: 12GB — all presets must include split-merge to handle 400+ image datasets
- **Portability**: DRONE_PIPELINE_DIR configurable via env var (fixed in audit)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Three-layer architecture (GUI → Service → Core) | Cross-model audit flagged god-class and duplicated orchestration | — Pending |
| Job type presets drive ODM options | User picks intent, not settings; reduces complexity | — Pending |
| ReportLab PDF + Folium HTML reports | Matches existing vegetation_report.py pattern in drone-pipeline | — Pending |
| Portfolio folder at E:\Portfolio\{site}\{date}\ | Organized by site for weekly revisits, grab-and-upload ready | — Pending |
| Skip Supabase for portfolio mode | Portfolio is local-only; pipeline handles client DB records | ✓ Good |

---
*Last updated: 2026-03-16 after v2.0 milestone initialization*
