# Panorama Job Type Design

**Date:** 2026-07-27
**Status:** Approved
**Scope:** Sortie backlog Task 1

## Goal

Turn Sortie's existing DJI panorama detection and OpenCV stitching into a first-class local job type. The result must produce panorama files that can be uploaded as normal photos to DroneInvoice and interactive viewers that can be hosted through the Sentinel Aerial website's existing static R2 portfolio pattern.

## Constraints

- Keep the Tkinter GUI and existing threading and queue patterns.
- Do not submit panorama jobs to NodeODM or OpenSplat.
- Use `sentinel_core.spatial.haversine` for geographic distance.
- Require at least eight source photos for a panorama set.
- Reuse the existing subprocess-based OpenCV stitch worker.
- Vendor Pannellum 2.5.7 locally and load no runtime CDN resources.
- Add no Python dependency.
- Keep all changes inside the Sortie repository.

## Architecture

The feature extends the existing panorama functions in `photo_classifier.py` instead of introducing another service module. This preserves the current public imports used by `test_panorama.py` and keeps the change focused.

- `odm_presets.py` adds the `panorama` job type with `engine: "local"`, `min_photos: 8`, no ODM options, and the panorama report type.
- `photo_classifier.py` adds GPS clustering, pre-stitched DJI detection, fast-path copying, deterministic naming, viewer generation, and local preview launcher generation.
- `portfolio_service.py` recognizes the local engine, avoids all NodeODM calls, processes each panorama set, writes its manifest data, and supplies panorama data to the report generator.
- `sortie.py` displays Panorama from `JOB_TYPES`, permits it when NodeODM and splat engines are unavailable, and uses the existing worker-thread processing path.
- `report_templates.py` adds a panorama report template covering set count, representative positions, source type, stitch status, and viewer status.
- `assets/pannellum/` contains the vendored Pannellum JavaScript, CSS, and license notice.

Automatic uploads to DroneInvoice or Cloudflare R2 are not part of this task. The output is deliberately upload-ready for both destinations.

## Panorama discovery and clustering

Sortie continues to inspect the selected folder, its `PANORAMA` child, and a sibling `PANORAMA` folder used by DJI SD card layouts.

Source images with valid GPS coordinates are grouped using `sentinel_core.spatial.haversine` and a five-meter radius. A cluster becomes a deliverable set when it contains at least eight images. Smaller clusters are retained as skipped straggler information for logging and the manifest but are not stitched.

If source images do not contain GPS coordinates, Sortie preserves the existing DJI subfolder grouping. A subfolder still needs at least eight images to become a set.

The clustering result uses deterministic ordering based on representative position and source path so generated names remain stable between runs.

## DJI pre-stitched fast path

Sortie searches the selected folder and panorama roots for likely DJI panorama outputs. A candidate must be a JPEG with a panorama-style filename and panorama-shaped dimensions. When GPS is available, the candidate is associated with the nearest set within five meters. Otherwise, Sortie uses an unambiguous DJI folder or set identifier match.

When a matching candidate exists, Sortie copies it into the deliverable folder and records `dji_prestitched`. If the copy fails, processing falls back to the OpenCV stitch worker. When no candidate exists, Sortie uses OpenCV directly and records `opencv_stitched` or `failed`.

One failed set never stops the remaining sets.

## Output package

The panorama job writes directly into its standard job folder:

```text
E:\Portfolio\<site>\<date>\panorama\
  set-001.jpg
  set-001.html
  set-002.jpg
  set-002.html
  assets\
    pannellum.js
    pannellum.css
    LICENSE.txt
  view_panoramas.bat
  manifest.json
  Sentinel_<site>_panorama_<date>.pdf
```

The JPG files are the direct DroneInvoice upload artifacts. The HTML files use relative paths to the JPG and vendored assets, so the complete folder can be uploaded to R2 and embedded by the Sentinel website.

Pannellum requires an HTTP origin when used locally. `view_panoramas.bat` starts Python's built-in local HTTP server and opens the panorama folder without requiring internet access or another dependency.

The existing copy to `E:\Portfolio\_Panoramas` remains available as the quick DroneInvoice gallery path.

## Viewer generation

Each successful set receives one HTML viewer configured as an equirectangular Pannellum scene. The generator uses relative asset paths, escapes all dynamic text through JSON serialization, and emits no external URLs. The title identifies the site and set. The viewer fills the browser viewport and enables normal mouse, touch, zoom, and fullscreen controls.

Viewer generation happens only after a panorama JPG exists. A viewer failure is recorded independently and does not remove the usable JPG.

## GUI behavior

Panorama appears in the existing job type list without a GUI redesign. Scan results show the number of valid panorama sets, skipped stragglers, and whether a DJI pre-stitched candidate was found.

The Process action runs through the existing background thread and queue. The local engine bypasses NodeODM and OpenSplat health gates. Progress messages identify each set and whether Sortie copied or stitched it.

## Report and manifest

The panorama report contains:

- Valid set count
- Representative latitude and longitude when available
- Source photo count per set
- DJI pre-stitched or OpenCV source
- Stitch or copy status
- Viewer generation status and relative filename
- Skipped straggler count

The manifest carries the same structured set data so downstream portfolio tooling can consume it later without parsing the PDF.

## Error handling

- A panorama job with no valid eight-photo set returns a clear local validation error.
- Missing GPS falls back to DJI folder grouping.
- Ambiguous pre-stitched candidates are not selected automatically.
- A failed pre-stitched copy falls back to OpenCV.
- A stitch failure is isolated to its set.
- A viewer generation failure leaves the JPG intact.
- Missing vendored assets produces a clear viewer error while preserving panorama images and report data.

## Verification

Test-first coverage will include:

- One GPS cluster
- Two distinct GPS clusters
- Sub-eight-photo stragglers
- Missing-GPS folder fallback
- DJI pre-stitched detection and absence
- Copy fast path and OpenCV fallback
- Deterministic filenames
- Viewer HTML generation with local assets and no CDN URLs
- Offline preview launcher generation
- Local-engine routing with no NodeODM submission
- Panorama report fields and statuses
- GUI job type exposure through `JOB_TYPES`
- Backward compatibility for existing presets

The final gate is `python -m pytest -q` from the repository root with no failures or new skips.
