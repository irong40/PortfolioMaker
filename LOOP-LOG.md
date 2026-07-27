# Sortie Backlog Loop Log

## Task 1 - Panorama job type

- **Status:** DONE
- **Date:** 2026-07-27
- **Commit:** `feat(panorama): add first-class panorama deliverables`
- **Files changed:**
  - `odm_presets.py`
  - `photo_classifier.py`
  - `portfolio_service.py`
  - `report_templates.py`
  - `report_generator.py`
  - `sortie.py`
  - `assets/pannellum/pannellum.js`
  - `assets/pannellum/pannellum.css`
  - `assets/pannellum/LICENSE.txt`
  - `test_odm_presets.py`
  - `test_panorama.py`
  - `test_portfolio_service.py`
  - `test_report_templates.py`
  - `test_report_generator.py`
  - `docs/plans/2026-07-27-panorama-job-type-design.md`
  - `docs/plans/2026-07-27-panorama-job-type-implementation.md`
- **Test count:** 511 baseline -> 540 passing (+29), with the same 3 existing rasterio warnings.
- **Focused verification:** 161 panorama, preset, service, and report tests passed.
- **Smoke verification:** Offline Pannellum viewer returned HTTP 200 using local assets. A real local panorama job produced `set-001.jpg`, `set-001.html`, `view_panoramas.bat`, `manifest.json`, and the panorama PDF with no NodeODM task.
- **Pannellum 2.5.7 asset SHA-256:**
  - `pannellum.js`: `51B8DF674333612ADBD807B6F47940D4D7AA07317D949D3F1314F84DE965FC3C`
  - `pannellum.css`: `DA0906E704524CCA414CE6160C7BE218048DCDD0C13FAFA84184F6EA0B084785`
  - `LICENSE.txt`: `10690F970C56EA1A43EDE2B5BEEAE11510FEFF41A617ED6EBE186A74BA2228AC`
- **Deferred:** Automatic publishing to DroneInvoice and Cloudflare R2. Task 1 produces upload-ready artifacts for both targets but performs no external upload.
