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

### Task 1 audit follow-up (2026-07-27)

Audit of `9a922ee` found four defects the 540-test suite did not cover. All
reproduced against the pre-fix code before fixing, all now covered by
regression tests (540 -> 548).

1. **Shared gallery overwrite (high).** Renaming stitched output to
   `set-NNN.jpg` made the `PANO_GALLERY` copy collide: every job wrote
   `set-001.jpg` over the previous job's. Reproduced: two jobs -> one file.
   Affected all job types, since the NodeODM and mipmap paths call
   `stitch_panoramas` too. Fixed with `gallery_prefix()` — the gallery copy is
   now `<job-folder>_set-NNN.jpg`.
2. **Cross-folder cluster merge (high).** Clustering pooled every PANORAMA
   subfolder's photos, so two panoramas shot from one launch point (~1 m apart,
   inside the 5 m radius) merged into one unstitchable set. Reproduced: two
   8-photo folders -> one 16-photo set. Clustering is now per-folder, which
   still splits a folder holding two distinct positions.
3. **Duplicate sets from a mixed-GPS folder (medium).** A folder with some
   GPS-tagged and some untagged photos emitted two sets for one capture.
   Reproduced: 8 tagged + 9 untagged -> sets `[8, 9]`. Untagged photos now join
   their folder's largest set -> `[17]`.
4. **Unregistered with the CRM (medium).** `crm_sync.py` was never touched, so
   `panorama` had no `PRESET_TO_JOB_TYPE` key — the documented silent
   no-prefill failure class. Added, along with `structures`, which a new
   coverage invariant test showed had the same pre-existing gap.
   `REPORT_TEMPLATE_CODES` intentionally still omits panorama: verified live
   against `qjpujskwqaehxnqypxzu` that no panorama `report_templates` row
   exists, and creating one belongs to the staged, unapplied deliverables
   migration.

Also fixed double-encoded UTF-8 in `odm_presets.py` and `portfolio_service.py`,
and hoisted `import re` to module scope.

**Still unverified:** no real mission has run through the panorama path. The
Pannellum viewer check was HTTP 200 on the served file, not a WebGL render.
