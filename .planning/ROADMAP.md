# Roadmap: Portfolio Maker

## Milestones

- Completed **v1.0** - Photo Classification and Sorting (pre-GSD, 7 commits, shipped 2026-03-16)
- In progress **v2.0** - Intent-Driven Workflow: Job Types, NodeODM Submission, Reports (Phases 1-5)

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

### v2.0 In Progress

- [ ] **Phase 1: ODM Presets** - Define the 6 job type configurations that drive processing and output
- [ ] **Phase 2: Portfolio Service** - Orchestration layer connecting job types to NodeODM and output folders
- [ ] **Phase 3: GUI Redesign** - Rebuild the Tkinter interface around the intent-first workflow
- [ ] **Phase 4: Report Generator** - Branded ReportLab PDF reports for all 6 job types
- [ ] **Phase 5: Integration Testing** - End-to-end validation of full workflow from photo folder to deliverable package
- [ ] **Phase 6: MipMap + Gaussian Splats** - Add 7th job type routing to MipMap Desktop for Gaussian Splat generation

## Phase Details

### Phase 1: ODM Presets
**Goal**: Job type configurations exist as a tested data layer, ready to drive all downstream behavior
**Depends on**: Nothing (first phase)
**Requirements**: JOB-01, JOB-02, JOB-03, JOB-04, JOB-05
**Success Criteria** (what must be TRUE):
  1. All 6 job types (Construction Progress, Property Survey, Roof Inspection, Structures, Vegetation, Real Estate) are selectable from a defined preset registry
  2. Each job type specifies which photo subset it uses (nadir only vs all photos)
  3. Each job type specifies its ODM processing options including split-merge settings safe for 12GB VRAM
  4. Each job type specifies which report template it triggers
  5. Preset registry is tested in isolation — behavior is verifiable without GUI or NodeODM running
**Plans**: TBD

### Phase 2: Portfolio Service
**Goal**: A drone operator can submit photos to NodeODM, monitor progress, and receive outputs in an organized portfolio folder
**Depends on**: Phase 1
**Requirements**: PROC-01, PROC-02, PROC-03, PROC-04, PROC-05, OUT-01, OUT-02, OUT-03, OUT-04
**Success Criteria** (what must be TRUE):
  1. User can submit a filtered photo set to NodeODM and a task is created and tracked
  2. App polls NodeODM and reports task progress without blocking the GUI
  3. Completed outputs (orthomosaic, DSM, models) download automatically to E:\Portfolio\{SiteName}\{YYYY-MM-DD}\
  4. User can run "Portfolio Only" mode (photo sort and manifest, no NodeODM) when NodeODM is unavailable
  5. App detects whether NodeODM is reachable before the user tries to submit
  6. Site name entered by user appears in folder path and is persisted as site_info.json for future visits
**Plans**: TBD

### Phase 3: GUI Redesign
**Goal**: The Tkinter interface presents an intent-first workflow where job type and site name are chosen before scan, and status is always visible
**Depends on**: Phase 2
**Requirements**: GUI-01, GUI-02, GUI-03, GUI-04, GUI-05, GUI-06
**Success Criteria** (what must be TRUE):
  1. Photo folder, job type dropdown, and site name field are visible and required before a scan can run
  2. After scan, stat badges show total photos, nadir count, oblique count, and detected platform
  3. Process and Portfolio Only buttons appear only after a successful scan (not before)
  4. Advanced settings (pitch threshold, bbox, NodeODM URL) are present but collapsed by default
  5. NodeODM connectivity indicator (green/red) is visible in the header at all times
**Plans**: TBD

### Phase 4: Report Generator
**Goal**: Each completed job produces a branded PDF report appropriate to its job type, ready to hand to a prospect
**Depends on**: Phase 2
**Requirements**: RPT-01, RPT-02, RPT-03, RPT-04, RPT-05, RPT-06, RPT-07, RPT-08
**Success Criteria** (what must be TRUE):
  1. Every job type produces a PDF with Sentinel branding (logo, colors, contact info)
  2. Every report includes a flight summary section showing photo count, drone platform, GPS footprint, and flight date
  3. Construction Progress report contains a progress tracking section with site context
  4. Property Survey report contains area, elevation, and boundary sections
  5. Roof Inspection report contains a condition assessment section
  6. Structures report contains a structural assessment section
  7. Vegetation job delegates to the existing vegetation_report.py without reimplementing logic
  8. Real Estate report contains a property showcase section
**Plans**: TBD

### Phase 5: Integration Testing
**Goal**: The full workflow from photo folder selection through job completion produces a verifiable, client-ready deliverable package
**Depends on**: Phase 1, Phase 2, Phase 3, Phase 4
**Requirements**: (validation of all 28 requirements — no new requirements introduced)
**Success Criteria** (what must be TRUE):
  1. Selecting a job type, scanning a photo folder, and submitting to NodeODM produces an output folder at E:\Portfolio\{SiteName}\{YYYY-MM-DD}\ containing orthomosaic, DSM, and manifest.json
  2. Running Portfolio Only mode produces sorted photos and manifest.json without NodeODM
  3. Report generated at job completion matches the selected job type and contains Sentinel branding
  4. Switching between all 6 job types produces correctly differing ODM options and report templates
  5. App starts cleanly and shows correct NodeODM status when NodeODM is both running and stopped
**Plans**: TBD

### Phase 6: MipMap + Gaussian Splats
**Goal**: A 7th job type "Gaussian Splat" routes to MipMap Desktop instead of NodeODM, with optimized settings and a splat-specific report
**Depends on**: Phase 1, Phase 2, Phase 3, Phase 4
**Requirements**: MIP-01, MIP-02, MIP-03, MIP-04, MIP-05
**Success Criteria** (what must be TRUE):
  1. Selecting "Gaussian Splat" job type submits to MipMap Desktop (not NodeODM)
  2. MipMap task uses resolution_level 3, mesh_decimate_ratio 0.5, and disables non-essential outputs to conserve VRAM
  3. App monitors MipMap task log for progress updates and completion
  4. Completed splat outputs (gs_ply, gs_sog_tiles) download to portfolio folder
  5. Gaussian Splat report includes splat-specific sections and Sentinel branding
  6. GUI shows MipMap status indicator in header alongside NodeODM indicator
**Plans:** 3 plans

Plans:
- [ ] 06-01-PLAN.md — MipMap service module, gaussian_splat preset, and report template
- [ ] 06-02-PLAN.md — Wire MipMap into portfolio service and GUI
- [ ] 06-03-PLAN.md — Full integration verification and GUI checkpoint

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6
(Phase 3 and Phase 4 can be parallelized — both depend on Phase 2 only)
(Phase 6 depends on Phases 1-4 complete)

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. ODM Presets | 0/TBD | Not started | - |
| 2. Portfolio Service | 0/TBD | Not started | - |
| 3. GUI Redesign | 0/TBD | Not started | - |
| 4. Report Generator | 0/TBD | Not started | - |
| 5. Integration Testing | 0/TBD | Not started | - |
| 6. MipMap + Gaussian Splats | 0/3 | Planned | - |
