# Requirements: Portfolio Maker

**Defined:** 2026-03-16
**Core Value:** A drone operator can point at a folder of photos, pick what they want to produce, and get a complete branded deliverable package.

## v2.0 Requirements

### Job Types

- [ ] **JOB-01**: User can select from 6 job types (Construction Progress, Property Survey, Roof Inspection, Structures, Vegetation, Real Estate)
- [ ] **JOB-02**: Selected job type determines which photos are used (nadir only vs all)
- [ ] **JOB-03**: Selected job type determines ODM processing options
- [ ] **JOB-04**: Selected job type determines which report template is generated
- [ ] **JOB-05**: Job type presets include split-merge settings for 12GB VRAM safety

### Processing

- [ ] **PROC-01**: User can submit filtered photos to NodeODM for processing
- [ ] **PROC-02**: App polls NodeODM for task progress and displays status
- [ ] **PROC-03**: App downloads completed outputs (orthomosaic, DSM, models) to portfolio folder
- [ ] **PROC-04**: User can sort photos locally without NodeODM ("Portfolio Only" mode)
- [ ] **PROC-05**: App checks NodeODM availability and shows status indicator

### Reports

- [ ] **RPT-01**: App generates branded PDF report with Sentinel branding for each job type
- [ ] **RPT-02**: Reports include flight summary (photo count, platform, GPS footprint, date)
- [ ] **RPT-03**: Construction Progress reports include progress tracking sections
- [ ] **RPT-04**: Property Survey reports include area/elevation/boundary sections
- [ ] **RPT-05**: Roof Inspection reports include condition assessment sections
- [ ] **RPT-06**: Structures reports include structural assessment sections
- [ ] **RPT-07**: Vegetation reports delegate to existing vegetation_report.py
- [ ] **RPT-08**: Real Estate reports include property showcase sections

### Output

- [ ] **OUT-01**: Output folder organized as E:\Portfolio\{SiteName}\{YYYY-MM-DD}\
- [ ] **OUT-02**: Site info persisted as site_info.json in site root
- [ ] **OUT-03**: Manifest.json written alongside outputs with full metadata
- [ ] **OUT-04**: User can specify site name for folder naming and report branding

### GUI

- [ ] **GUI-01**: GUI shows Photo Folder, Job Type, and Site Name before scan
- [ ] **GUI-02**: Scan reveals stats badges (total, nadir, oblique, platform)
- [ ] **GUI-03**: Process and Portfolio Only buttons appear only after scan
- [ ] **GUI-04**: Advanced settings (threshold, bbox, engine URL) collapsed by default
- [ ] **GUI-05**: Progress bar and status updates during NodeODM processing
- [ ] **GUI-06**: NodeODM connectivity indicator in header (green/red)

## Future Requirements

### Post-Processing

- **POST-01**: Export oblique portfolio shots to Lightroom edit folder
- **POST-02**: Auto-enhance orthomosaic output (contrast, white balance, sharpening)

### Change Detection

- **CHG-01**: Compare orthomosaics across visits for construction progress overlay
- **CHG-02**: Volume change calculation between DSMs

### MipMap Integration

- **MIP-01**: Submit to MipMap Desktop as alternative engine
- **MIP-02**: Gaussian Splat and OSGB output support

## Out of Scope

| Feature | Reason |
|---------|--------|
| Supabase integration | Portfolio mode runs without backend |
| n8n webhook orchestration | Manual trigger only |
| Video processing | Handled by drone-pipeline Path V |
| Client billing/invoicing | Not a business tool |
| Multi-user/cloud | Single desktop user |
| Lightroom edit step | Not needed for v2.0 portfolio; manual edit when needed |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| JOB-01 | Phase 1 | Pending |
| JOB-02 | Phase 1 | Pending |
| JOB-03 | Phase 1 | Pending |
| JOB-04 | Phase 1 | Pending |
| JOB-05 | Phase 1 | Pending |
| PROC-01 | Phase 2 | Pending |
| PROC-02 | Phase 2 | Pending |
| PROC-03 | Phase 2 | Pending |
| PROC-04 | Phase 2 | Pending |
| PROC-05 | Phase 2 | Pending |
| OUT-01 | Phase 2 | Pending |
| OUT-02 | Phase 2 | Pending |
| OUT-03 | Phase 2 | Pending |
| OUT-04 | Phase 2 | Pending |
| GUI-01 | Phase 3 | Pending |
| GUI-02 | Phase 3 | Pending |
| GUI-03 | Phase 3 | Pending |
| GUI-04 | Phase 3 | Pending |
| GUI-05 | Phase 3 | Pending |
| GUI-06 | Phase 3 | Pending |
| RPT-01 | Phase 4 | Pending |
| RPT-02 | Phase 4 | Pending |
| RPT-03 | Phase 4 | Pending |
| RPT-04 | Phase 4 | Pending |
| RPT-05 | Phase 4 | Pending |
| RPT-06 | Phase 4 | Pending |
| RPT-07 | Phase 4 | Pending |
| RPT-08 | Phase 4 | Pending |

**Coverage:**
- v2.0 requirements: 28 total
- Mapped to phases: 28
- Unmapped: 0

---
*Requirements defined: 2026-03-16*
*Last updated: 2026-03-16 — traceability mapped after roadmap creation*
