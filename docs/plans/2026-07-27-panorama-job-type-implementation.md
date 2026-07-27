# Panorama Job Type Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a first-class local Panorama job that produces DroneInvoice-ready JPGs, Sentinel website-ready Pannellum viewers, and a panorama report.

**Architecture:** Extend Sortie's existing panorama model and subprocess stitcher in `photo_classifier.py`. Route the new `engine: "local"` preset through `portfolio_service.py` without NodeODM, then expose it through the existing Tkinter job list and report system. All new behavior is driven by failing tests first.

**Tech Stack:** Python 3.12, Tkinter, pytest, Pillow, OpenCV worker subprocess, `sentinel_core.spatial.haversine`, ReportLab, Pannellum 2.5.7.

---

## Commit rule

The backlog requires one commit for Task 1. The approved design is already committed as `feat(panorama): add first-class panorama deliverables`. Do not create intermediate commits. After the full suite passes, stage all Task 1 files and amend that commit with `git commit --amend --no-edit`.

### Task 1: Add the local panorama preset

**Files:**
- Modify: `test_odm_presets.py`
- Modify: `odm_presets.py:17-269`

**Step 1: Write the failing preset tests**

Add `panorama` to the expected preset registry and assert the local-only contract:

```python
class TestPanoramaPreset:
    def test_panorama_in_job_types(self):
        assert ("panorama", "Panorama / 360") in JOB_TYPES

    def test_panorama_is_local_with_eight_photo_minimum(self):
        preset = PRESETS["panorama"]
        assert preset["engine"] == "local"
        assert preset["min_photos"] == 8
        assert preset["odm_options"] == []
        assert preset["downloads"] == []
        assert preset["report_type"] == "panorama"
```

Update generic ODM tests to skip `engine == "local"` wherever they require split options, orthophoto downloads, or non-empty downloads.

**Step 2: Verify RED**

Run:

```powershell
python -m pytest test_odm_presets.py -q
```

Expected: failures because the `panorama` preset does not exist.

**Step 3: Implement the minimal preset**

Add this job type and preset without changing `gaussian_splat`:

```python
("panorama", "Panorama / 360"),

"panorama": {
    "label": "Panorama / 360",
    "description": "Local 360 panorama stitching and web viewer export",
    "photo_filter": None,
    "min_photos": 8,
    "engine": "local",
    "odm_options": [],
    "downloads": [],
    "report_type": "panorama",
},
```

**Step 4: Verify GREEN**

Run `python -m pytest test_odm_presets.py -q`.

Expected: all preset tests pass and the D12 no-split test remains green.

### Task 2: Add deterministic GPS clustering and richer set metadata

**Files:**
- Modify: `test_panorama.py`
- Modify: `photo_classifier.py:29-194`

**Step 1: Write failing clustering tests**

Import `cluster_panorama_photos` and cover:

```python
def test_clusters_one_set_within_five_metres(): ...
def test_clusters_two_positions_more_than_five_metres_apart(): ...
def test_keeps_sub_eight_cluster_as_stragglers(): ...
def test_cluster_order_is_deterministic(): ...
```

Use tuples shaped as `(path, latitude, longitude)` and generate coordinates with small longitude offsets near Hampton Roads. Assert that the helper returns all clusters, then assert `scan_panoramas` promotes only clusters with at least eight photos.

Update the old one-photo, two-photo, three-photo, and five-photo scan fixtures to create eight images where they expect a valid set.

**Step 2: Verify RED**

Run the four new tests directly. Expected: import failure because `cluster_panorama_photos` does not exist.

**Step 3: Implement metadata and clustering**

Import the shared helper:

```python
from sentinel_core.spatial import haversine
```

Extend `PanoramaSet` with defaults that preserve existing callers:

```python
latitude: float = None
longitude: float = None
prestitched_path: str = ""
viewer_path: str = ""
status: str = "pending"
source_type: str = "source_photos"
```

Implement a deterministic greedy clusterer that compares each located photo to the current cluster representative with `haversine`. Sort input by path before grouping. Return valid clusters and stragglers separately from the scan helper so the manifest can report both.

When GPS extraction returns no coordinates for a DJI set folder, preserve folder grouping. Enforce eight photos for both GPS and folder-based sets.

**Step 4: Verify GREEN**

Run `python -m pytest test_panorama.py -q`.

Expected: all existing panorama tests plus clustering tests pass.

### Task 3: Detect and use DJI pre-stitched panoramas

**Files:**
- Modify: `test_panorama.py`
- Modify: `photo_classifier.py:158-194,399-495`

**Step 1: Write failing detection tests**

Create real small JPEG fixtures with Pillow and test:

```python
def test_detects_wide_dji_prestitched_panorama(): ...
def test_rejects_normal_four_by_three_pano_source_frame(): ...
def test_matches_candidate_to_nearest_gps_set(): ...
def test_copy_failure_falls_back_to_worker(): ...
```

The positive fixture should have a panorama filename and width-to-height ratio of at least 1.8. The negative fixture uses the same name pattern but a normal 4:3 shape.

**Step 2: Verify RED**

Run the new detection tests. Expected: missing detection helper or missing `prestitched_path` behavior.

**Step 3: Implement minimal detection and fast path**

Add pure helpers that:

- Search the selected folder and discovered panorama roots without descending into output folders.
- Accept `.jpg` and `.jpeg` candidates whose stem contains `pano` or `panorama`.
- Confirm width-to-height ratio with Pillow.
- Match by nearest GPS coordinate within five meters when coordinates exist.
- Fall back to an unambiguous DJI set identifier match.
- Leave ambiguous candidates unmatched.

Before calling `_stitch_one_set`, copy a matching pre-stitched image to the deterministic output path. Record `dji_prestitched`. On copy failure, log the error and continue into the existing worker. Record worker success as `opencv_stitched` and failure as `failed`.

**Step 4: Verify GREEN**

Run `python -m pytest test_panorama.py -q`.

Expected: all panorama tests pass.

### Task 4: Generate local Pannellum viewers

**Files:**
- Create: `assets/pannellum/pannellum.js`
- Create: `assets/pannellum/pannellum.css`
- Create: `assets/pannellum/LICENSE.txt`
- Modify: `test_panorama.py`
- Modify: `photo_classifier.py:448-495`

**Step 1: Vendor the official assets**

Download the Pannellum 2.5.7 release from the official project and copy only the compiled JavaScript, CSS, and license notice into `assets/pannellum/`. Verify the files contain no CDN substitutions and record their SHA-256 hashes in the implementation log.

**Step 2: Write failing viewer tests**

Add tests for `generate_panorama_viewer` and `write_panorama_launcher`:

```python
def test_viewer_references_local_image_and_assets(): ...
def test_viewer_contains_no_http_or_cdn_url(): ...
def test_viewer_json_escapes_site_title(): ...
def test_launcher_serves_folder_on_loopback(): ...
```

Also test that `stitch_panoramas` sets `viewer_path` only when the JPG exists.

**Step 3: Verify RED**

Run the new tests. Expected: missing functions.

**Step 4: Implement viewer and launcher generation**

Copy the vendored assets once into `<output>/assets/`. Generate one full-viewport HTML file per JPG with relative URLs:

```javascript
pannellum.viewer("panorama", {
  type: "equirectangular",
  panorama: "set-001.jpg",
  autoLoad: true,
  showFullscreenCtrl: true,
  title: "<JSON-serialized title>"
});
```

Serialize dynamic strings with `json.dumps`, not interpolation into JavaScript literals. Generate `view_panoramas.bat` that changes to its own directory, opens the first viewer on `http://127.0.0.1:8765/`, and runs `python -m http.server 8765 --bind 127.0.0.1`.

Use deterministic files `set-001.jpg`, `set-001.html`, and so on. Continue copying each JPG to `PANO_GALLERY` with its deterministic name.

**Step 5: Verify GREEN**

Run `python -m pytest test_panorama.py -q`.

Expected: viewer, launcher, stitch, and detection tests pass.

### Task 5: Route the local engine without NodeODM

**Files:**
- Modify: `test_portfolio_service.py`
- Modify: `portfolio_service.py:184-540`

**Step 1: Write failing orchestration tests**

Add `process_job` to the test imports and create a classification with one valid `PanoramaSet`. Patch `_stitch_one_set` or `stitch_panoramas`, report generation, GIS export, and photo analysis as needed. Assert:

```python
assert result["preset"]["engine"] == "local"
assert result["task_uuid"] is None
mock_submit_to_nodeodm.assert_not_called()
assert result["report_data"]["panorama_sets"][0]["status"] == "opencv_stitched"
```

Add a no-valid-set test expecting `"No panorama set with at least 8 photos"`.

**Step 2: Verify RED**

Run the new service tests. Expected: local engine falls into the NodeODM branch or rejects the working set.

**Step 3: Implement local routing**

Load the preset before generic photo-count rejection. For the panorama job, validate `classification.panorama_sets` instead of `classification.total` and bypass normal `scan_for_job` empty-set validation.

Add an explicit local branch before the NodeODM branch:

```python
elif engine == "local":
    notify("panorama", f"Processing {len(classification.panorama_sets)} panorama set(s)")
    stitch_panoramas(classification.panorama_sets, output_dir, site_name=site_name)
```

Do not run the later generic panorama stitch block a second time. Build the deliverables index from each JPG and HTML. Add structured `panorama_sets` and `panorama_stragglers` to report data and the returned result. Set `task_uuid` only for engines that actually create remote tasks.

Skip AI photo analysis, point-cloud analysis, GIS export, and vegetation analysis for the local panorama engine. Generate the report directly from panorama results.

**Step 4: Verify GREEN**

Run:

```powershell
python -m pytest test_portfolio_service.py test_panorama.py -q
```

Expected: all service and panorama tests pass with no NodeODM calls.

### Task 6: Add the panorama report

**Files:**
- Modify: `test_report_templates.py`
- Modify: `test_report_generator.py`
- Modify: `report_templates.py:650-790`
- Modify: `report_generator.py:201-720`

**Step 1: Write failing report tests**

Add `panorama` to the expected template registry and assert the new template contains `panorama_sets`, `flight_summary`, `deliverables`, and `methodology` sections.

Add a renderer test with two structured sets and assert the generated PDF exists. Add a focused unit test for the panorama table helper if the existing report tests inspect element output.

**Step 2: Verify RED**

Run:

```powershell
python -m pytest test_report_templates.py test_report_generator.py -q
```

Expected: missing panorama template and renderer failures.

**Step 3: Implement the template and renderer**

Add a `PANORAMA` `ReportTemplate` with at least five sections and a unique AI prompt and schema that satisfy existing registry invariants. Keep AI optional.

Add `_render_panorama_sets` to produce a table with columns for set, coordinates, source photos, processing source, panorama status, and viewer status. Dispatch `section.key == "panorama_sets"` before the generic AI section logic.

Ensure `_render_deliverables` lists both JPG and HTML paths from the report data.

**Step 4: Verify GREEN**

Run `python -m pytest test_report_templates.py test_report_generator.py -q`.

Expected: all report tests pass.

### Task 7: Expose and gate Panorama correctly in Tkinter

**Files:**
- Modify: `sortie.py:500-525,1560-1760`
- Modify: `test_panorama.py`

**Step 1: Write the failing GUI contract test**

The job list is data-driven, so assert `JOB_TYPES` exposes Panorama and add a pure helper or predicate for engine availability if needed:

```python
assert ("panorama", "Panorama / 360") in sortie.JOB_TYPES
assert sortie.engine_requires_nodeodm("local") is False
assert sortie.engine_requires_nodeodm("opensplat") is True
```

**Step 2: Verify RED**

Run the focused test. Expected: missing predicate or incorrect preflight behavior.

**Step 3: Implement minimal GUI routing changes**

Keep the existing radio-button construction. Update the process preflight so `local` bypasses NodeODM and splat health errors. Preserve OpenSplat's NodeODM requirement. Ensure progress stages `panorama`, `warning`, `report`, and `complete` display through the existing queue and no work runs on the GUI thread.

Update the scan results line to show valid panorama sets, pre-stitched matches, and skipped stragglers.

**Step 4: Verify GREEN**

Run `python -m pytest test_panorama.py -q`.

Expected: GUI contract and all panorama tests pass.

### Task 8: Final verification, loop log, and single commit

**Files:**
- Create: `LOOP-LOG.md`
- Modify: all Task 1 files above
- Reference only: `C:\Users\redle.SOULAAN\obsidian-dev\projects\sortie\Sortie.md`
- Append: `C:\Users\redle.SOULAAN\obsidian-dev\last-session.md`

**Step 1: Run focused tests**

```powershell
python -m pytest test_panorama.py test_odm_presets.py test_portfolio_service.py test_report_templates.py test_report_generator.py -q
```

Expected: zero failures and zero new skips.

**Step 2: Run the complete suite**

```powershell
python -m pytest -q
```

Expected: at least the 511-test baseline plus all new tests, with zero failures.

**Step 3: Inspect output and repository state**

```powershell
git diff --check
git status --short
git diff --stat HEAD^
```

Confirm no protected folders changed and `gaussian_splat` contains no split or optimize-disk-space options.

**Step 4: Record the loop result**

Create `LOOP-LOG.md` with Task 1, date `2026-07-27`, files changed, baseline and final test counts, Pannellum asset hashes, and any deferred automatic publishing work.

Update the Sortie vault project page and append a concise session entry because project status changed. Do not modify architecture decisions unless implementation introduces a decision outside the approved design.

**Step 5: Amend the single Task 1 commit**

```powershell
git add -- <exact Task 1 files>
git diff --cached --check
git commit --amend --no-edit
git status --short --branch
```

Expected: one final commit named `feat(panorama): add first-class panorama deliverables`, clean working tree, and no push.
