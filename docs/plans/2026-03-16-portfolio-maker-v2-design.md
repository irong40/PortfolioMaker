# Portfolio Maker v2.0 — Design Document

**Date**: 2026-03-16
**Author**: Claude Code + Adam Pierce
**Status**: Approved

## Problem

Portfolio Maker v1 is a photo sorter that tells users "go to WebODM next." It doesn't produce deliverables. The user needs a manual-mode tool that produces the same client-ready packages as the automated drone pipeline — orthomosaics, 3D models, point clouds, and branded reports — without requiring a mission ID, Supabase, or n8n.

The primary use case is building a sales portfolio: weekly construction progress flights at a mall site, producing full deliverable packages to show prospective clients.

## Architecture

Three-layer split replacing the current monolithic GUI:

```
portfolio_maker.py       (GUI — thin display + user input)
        │
portfolio_service.py     (orchestration — scan, filter, submit, download, report)
        │
    ┌───┴───┬──────────────┬─────────────────┐
photo_classifier.py  odm_presets.py  report_generator.py
(existing core)      (job type →     (branded PDF/HTML
                      ODM options)    per job type)
```

### New Files

- `odm_presets.py` — Job type to ODM options/photo filter/report template mapping
- `portfolio_service.py` — Stateless orchestration (scan, process, check_nodeodm)
- `report_generator.py` — Branded PDF + Folium HTML reports per job type

### GUI Flow

1. Pick folder + job type + site name
2. Scan (auto-classifies photos, shows stats)
3. Process (submits to NodeODM with correct presets) or Portfolio Only (local sort)
4. Downloads outputs + generates branded report
5. Everything lands in `E:\Portfolio\{SiteName}\{date}\`

### Job Type Presets

| Job Type | Photos | Key ODM Options | Report Focus |
|---|---|---|---|
| Construction Progress | Nadir | ortho + DSM, split:4 | Timeline, overlays, volume delta |
| Property Survey | Nadir | ortho + DSM + DTM + PC, split:4 | Area, elevation, boundaries |
| Roof Inspection | All | full mesh + texture, high quality | Annotated findings, measurements |
| Structures | All | full mesh + PC, ultra quality | Condition assessment, measurements |
| Vegetation/Land | Nadir | ortho (then Path E) | Delegates to vegetation_report.py |
| Real Estate | All | ortho + mesh, medium quality | Hero images, property showcase |

### Output Structure

```
E:\Portfolio\{SiteName}\
├── site_info.json
└── {YYYY-MM-DD}\
    ├── orthomosaic.tif
    ├── dsm.tif
    ├── 3d_model\
    ├── point_cloud.laz
    ├── report.pdf
    ├── interactive_map.html
    └── manifest.json
```

### What Stays

- `photo_classifier.py` — untouched
- `test_photo_classifier.py` — untouched
- CLI functionality
- Sentinel branding

### Dependencies

No new pip packages. ReportLab and Folium from drone-pipeline venv. `photogrammetry_submit.py` imported from drone-pipeline.
