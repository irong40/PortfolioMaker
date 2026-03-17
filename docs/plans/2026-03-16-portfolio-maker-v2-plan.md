# Portfolio Maker v2.0 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesign Portfolio Maker from a photo sorter into an intent-driven tool that produces full client-ready deliverable packages (orthomosaics, 3D models, reports) via NodeODM.

**Architecture:** Three-layer split — thin Tkinter GUI calls a stateless service layer, which coordinates photo_classifier (existing), odm_presets (new), and report_generator (new). NodeODM API calls reuse photogrammetry_submit.py from drone-pipeline.

**Tech Stack:** Python 3.12, Tkinter, ReportLab (PDF), Folium (HTML maps), NodeODM REST API, existing drone-pipeline imports.

**Working directory:** `D:\Projects\PortfolioMaker\`

**Drone-pipeline imports:** Available at `DRONE_PIPELINE_DIR` env var or `C:\Users\redle.SOULAAN\Documents\drone-pipeline` — already on sys.path via photo_classifier.py.

---

## Phase 1: ODM Presets (data layer)

### Task 1: Create odm_presets.py with job type definitions

**Files:**
- Create: `odm_presets.py`
- Create: `test_odm_presets.py`

**Step 1: Write the failing test**

```python
"""Tests for odm_presets job type definitions."""

import pytest
from odm_presets import PRESETS, get_preset, JOB_TYPES


class TestPresets:
    def test_all_six_job_types_exist(self):
        expected = {
            "construction_progress", "property_survey", "roof_inspection",
            "structures", "vegetation", "real_estate",
        }
        assert set(PRESETS.keys()) == expected

    def test_preset_has_required_keys(self):
        for name, preset in PRESETS.items():
            assert "label" in preset, f"{name} missing label"
            assert "description" in preset, f"{name} missing description"
            assert "photo_filter" in preset, f"{name} missing photo_filter"
            assert "odm_options" in preset, f"{name} missing odm_options"
            assert "downloads" in preset, f"{name} missing downloads"
            assert "report_type" in preset, f"{name} missing report_type"

    def test_photo_filter_valid_values(self):
        for name, preset in PRESETS.items():
            assert preset["photo_filter"] in ("nadir", None), (
                f"{name} photo_filter must be 'nadir' or None, got {preset['photo_filter']}"
            )

    def test_odm_options_include_split_merge(self):
        for name, preset in PRESETS.items():
            if preset["odm_options"] is None:
                continue
            option_names = {o["name"] for o in preset["odm_options"]}
            assert "split" in option_names, f"{name} missing split option"
            assert "split-overlap" in option_names, f"{name} missing split-overlap"

    def test_get_preset_returns_copy(self):
        p1 = get_preset("property_survey")
        p2 = get_preset("property_survey")
        assert p1 is not p2
        assert p1 == p2

    def test_get_preset_invalid_raises(self):
        with pytest.raises(KeyError):
            get_preset("nonexistent")

    def test_job_types_list_matches_presets(self):
        assert len(JOB_TYPES) == len(PRESETS)
        for key, label in JOB_TYPES:
            assert key in PRESETS


class TestPortfolioOnlyPreset:
    def test_portfolio_has_no_odm_options(self):
        # vegetation delegates to Path E, but still has ODM for ortho
        # Only check that all presets with odm_options have lists
        for name, preset in PRESETS.items():
            if preset["odm_options"] is not None:
                assert isinstance(preset["odm_options"], list)
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest test_odm_presets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'odm_presets'`

**Step 3: Write the implementation**

```python
"""
Sentinel Portfolio Maker — ODM Processing Presets

Maps job types to NodeODM processing options, photo filters,
download targets, and report templates.
"""

import copy

# Ordered list for GUI display: (key, label)
JOB_TYPES = [
    ("construction_progress", "Construction Progress"),
    ("property_survey", "Property Survey"),
    ("roof_inspection", "Roof Inspection"),
    ("structures", "Structures"),
    ("vegetation", "Vegetation / Land"),
    ("real_estate", "Real Estate / Marketing"),
]

# Shared split-merge options (all presets use these to stay within 12GB VRAM)
_SPLIT_MERGE = [
    {"name": "split", "value": 4},
    {"name": "split-overlap", "value": 150},
    {"name": "sm-cluster", "value": "none"},
]

PRESETS = {
    "construction_progress": {
        "label": "Construction Progress",
        "description": "Orthomosaic + DSM for site progress tracking",
        "photo_filter": "nadir",
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "dtm", "value": True},
            {"name": "orthophoto-resolution", "value": 5},
            {"name": "fast-orthophoto", "value": False},
            {"name": "auto-boundary", "value": True},
            {"name": "pc-quality", "value": "medium"},
            {"name": "feature-quality", "value": "high"},
        ] + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif"],
        "report_type": "construction_progress",
    },
    "property_survey": {
        "label": "Property Survey",
        "description": "Orthomosaic + DSM + DTM + point cloud for survey",
        "photo_filter": "nadir",
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "dtm", "value": True},
            {"name": "orthophoto-resolution", "value": 5},
            {"name": "fast-orthophoto", "value": False},
            {"name": "auto-boundary", "value": True},
            {"name": "pc-quality", "value": "high"},
            {"name": "feature-quality", "value": "high"},
        ] + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif", "dtm.tif", "georeferenced_model.laz"],
        "report_type": "property_survey",
    },
    "roof_inspection": {
        "label": "Roof Inspection",
        "description": "Textured 3D mesh for roof condition assessment",
        "photo_filter": None,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "mesh-octree-depth", "value": 12},
            {"name": "mesh-size", "value": 300000},
            {"name": "pc-quality", "value": "high"},
            {"name": "feature-quality", "value": "ultra"},
            {"name": "auto-boundary", "value": True},
        ] + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif", "textured_model.zip"],
        "report_type": "roof_inspection",
    },
    "structures": {
        "label": "Structures",
        "description": "3D model + point cloud for structural inspection",
        "photo_filter": None,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "mesh-octree-depth", "value": 12},
            {"name": "mesh-size", "value": 300000},
            {"name": "pc-quality", "value": "high"},
            {"name": "feature-quality", "value": "ultra"},
            {"name": "auto-boundary", "value": True},
        ] + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif", "textured_model.zip", "georeferenced_model.laz"],
        "report_type": "structures",
    },
    "vegetation": {
        "label": "Vegetation / Land",
        "description": "Orthomosaic for vegetation analysis (Path E)",
        "photo_filter": "nadir",
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "orthophoto-resolution", "value": 5},
            {"name": "fast-orthophoto", "value": False},
            {"name": "auto-boundary", "value": True},
            {"name": "pc-quality", "value": "medium"},
            {"name": "feature-quality", "value": "high"},
        ] + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif"],
        "report_type": "vegetation",
    },
    "real_estate": {
        "label": "Real Estate / Marketing",
        "description": "Orthomosaic + 3D model for property showcase",
        "photo_filter": None,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "mesh-octree-depth", "value": 11},
            {"name": "mesh-size", "value": 200000},
            {"name": "pc-quality", "value": "medium"},
            {"name": "feature-quality", "value": "high"},
            {"name": "auto-boundary", "value": True},
        ] + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif", "textured_model.zip"],
        "report_type": "real_estate",
    },
}


def get_preset(job_type):
    """Return a deep copy of the preset for the given job type.

    Raises KeyError if job_type is not valid.
    """
    return copy.deepcopy(PRESETS[job_type])
```

**Step 4: Run tests**

Run: `python -m pytest test_odm_presets.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add odm_presets.py test_odm_presets.py
git commit -m "feat: add ODM presets for 6 job types"
```

---

## Phase 2: Portfolio Service (orchestration layer)

### Task 2: Create portfolio_service.py — scan + NodeODM check

**Files:**
- Create: `portfolio_service.py`
- Create: `test_portfolio_service.py`

**Step 1: Write failing tests**

```python
"""Tests for portfolio_service orchestration layer."""

import json
import os
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from portfolio_service import (
    check_nodeodm,
    scan_for_job,
    build_output_dir,
    write_site_info,
    PORTFOLIO_ROOT,
)
from odm_presets import get_preset


class TestCheckNodeodm:
    @patch("portfolio_service.requests.get")
    def test_reachable(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200)
        mock_get.return_value.json.return_value = {"version": "2.0", "taskQueueCount": 0}
        result = check_nodeodm("http://localhost:3000")
        assert result is not None
        assert result["version"] == "2.0"

    @patch("portfolio_service.requests.get", side_effect=Exception("refused"))
    def test_unreachable(self, mock_get):
        result = check_nodeodm("http://localhost:3000")
        assert result is None


class TestScanForJob:
    def test_nadir_filter_returns_nadir_only(self, tmp_path):
        # Create fake classified result
        from photo_classifier import PhotoMeta, ClassificationResult
        photos = [
            PhotoMeta(filename="n1.jpg", path=str(tmp_path / "n1.jpg"),
                      pitch=-90.0, latitude=36.82, longitude=-76.42, classification="nadir"),
            PhotoMeta(filename="o1.jpg", path=str(tmp_path / "o1.jpg"),
                      pitch=-45.0, latitude=36.82, longitude=-76.42, classification="oblique"),
        ]
        result = ClassificationResult(
            source_dir=str(tmp_path), total=2, nadir_count=1, oblique_count=1, photos=photos,
        )
        preset = get_preset("construction_progress")  # photo_filter = "nadir"
        filtered = scan_for_job(result, preset)
        assert filtered.total == 1
        assert filtered.nadir_count == 1

    def test_none_filter_returns_all(self, tmp_path):
        from photo_classifier import PhotoMeta, ClassificationResult
        photos = [
            PhotoMeta(filename="n1.jpg", path=str(tmp_path / "n1.jpg"),
                      pitch=-90.0, classification="nadir"),
            PhotoMeta(filename="o1.jpg", path=str(tmp_path / "o1.jpg"),
                      pitch=-45.0, classification="oblique"),
        ]
        result = ClassificationResult(
            source_dir=str(tmp_path), total=2, nadir_count=1, oblique_count=1, photos=photos,
        )
        preset = get_preset("roof_inspection")  # photo_filter = None
        filtered = scan_for_job(result, preset)
        assert filtered.total == 2


class TestBuildOutputDir:
    def test_creates_path(self):
        path = build_output_dir("MallTest", "2026-03-16")
        assert "MallTest" in path
        assert "2026-03-16" in path

    def test_uses_portfolio_root(self):
        path = build_output_dir("TestSite", "2026-01-01")
        assert path.startswith(PORTFOLIO_ROOT)


class TestWriteSiteInfo:
    def test_writes_json(self, tmp_path):
        write_site_info(str(tmp_path), "MallTest", "construction_progress")
        info_path = tmp_path / "site_info.json"
        assert info_path.exists()
        data = json.loads(info_path.read_text())
        assert data["site_name"] == "MallTest"
        assert data["job_type"] == "construction_progress"
```

**Step 2: Run to verify failure**

Run: `python -m pytest test_portfolio_service.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write portfolio_service.py**

```python
"""
Sentinel Portfolio Maker — Portfolio Service

Orchestration layer: scan, filter, submit to NodeODM, download outputs.
No GUI dependency — called by portfolio_maker.py or CLI.
"""

import os
import sys
import json
import logging
import requests
from pathlib import Path
from datetime import datetime, timezone

from photo_classifier import classify_photos, filter_photos, sort_photos, export_photos
from odm_presets import get_preset

PORTFOLIO_ROOT = os.environ.get("PORTFOLIO_ROOT", r"E:\Portfolio")
NODEODM_URL = os.environ.get("NODEODM_URL", "http://localhost:3000")


def check_nodeodm(base_url=None):
    """Check if NodeODM is reachable. Returns server info dict or None."""
    url = base_url or NODEODM_URL
    try:
        resp = requests.get(f"{url}/info", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def scan_for_job(classification_result, preset):
    """Apply the preset's photo filter to a ClassificationResult.

    Args:
        classification_result: From classify_photos()
        preset: From get_preset(job_type)

    Returns:
        Filtered ClassificationResult (or original if no filter).
    """
    photo_filter = preset.get("photo_filter")
    if photo_filter:
        return filter_photos(classification_result, classification=photo_filter)
    return classification_result


def build_output_dir(site_name, date_str=None):
    """Build the output directory path for a portfolio job.

    Returns: str path like E:\\Portfolio\\MallTest\\2026-03-16\\
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    return str(Path(PORTFOLIO_ROOT) / site_name / date_str)


def write_site_info(site_dir, site_name, job_type):
    """Write or update site_info.json in the site root folder."""
    site_root = Path(site_dir)
    # site_info lives one level up from the date folder
    if site_root.name != site_name:
        site_root = site_root.parent
    site_root.mkdir(parents=True, exist_ok=True)

    info_path = site_root / "site_info.json"

    # Merge with existing if present
    info = {}
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    info.update({
        "site_name": site_name,
        "job_type": job_type,
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })

    info_path.write_text(json.dumps(info, indent=2))
    return str(info_path)


def submit_to_nodeodm(photo_paths, odm_options, task_name="portfolio",
                       base_url=None, poll_interval=30, max_hours=6):
    """Submit photos to NodeODM and poll until complete.

    Uses photogrammetry_submit.py functions from drone-pipeline.

    Returns:
        (task_uuid, task_info) on success, (None, error_msg) on failure.
    """
    log = logging.getLogger(__name__)
    url = base_url or NODEODM_URL

    try:
        from photogrammetry_submit import submit_task, poll_task
    except ImportError:
        return None, "drone-pipeline not available — cannot submit to NodeODM"

    task_uuid = submit_task(url, photo_paths, options=odm_options, name=task_name)
    if not task_uuid:
        return None, "Task submission failed"

    result = poll_task(url, task_uuid, poll_interval=poll_interval, max_hours=max_hours)
    if not result:
        return None, "Task timed out"

    status_code = result.get("status", {}).get("code", -1)
    if status_code != 40:
        error = result.get("status", {}).get("errorMessage", "unknown")
        return None, f"Task failed: {error}"

    return task_uuid, result


def download_outputs(task_uuid, output_dir, download_list, base_url=None):
    """Download specific outputs from a completed NodeODM task.

    Args:
        task_uuid: Completed task UUID
        output_dir: Local directory to save files
        download_list: List of asset names (e.g., ["orthophoto.tif", "dsm.tif"])
        base_url: NodeODM URL

    Returns:
        Dict of {asset_name: local_path} for successful downloads.
    """
    log = logging.getLogger(__name__)
    url = base_url or NODEODM_URL
    os.makedirs(output_dir, exist_ok=True)
    downloaded = {}

    for asset_name in download_list:
        asset_url = f"{url}/task/{task_uuid}/download/{asset_name}"
        local_path = os.path.join(output_dir, asset_name)

        try:
            resp = requests.get(asset_url, stream=True, timeout=30)
            if resp.status_code == 200:
                with open(local_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                size_mb = os.path.getsize(local_path) / (1024 * 1024)
                log.info(f"Downloaded: {asset_name} ({size_mb:.1f} MB)")
                downloaded[asset_name] = local_path
            elif resp.status_code == 404:
                log.info(f"Not available: {asset_name}")
            else:
                log.warning(f"Download failed: {asset_name} HTTP {resp.status_code}")
        except requests.RequestException as e:
            log.warning(f"Download failed: {asset_name}: {e}")

    return downloaded


def process_job(source_dir, job_type, site_name, threshold=-70.0,
                bbox=None, base_url=None, progress_callback=None):
    """Full portfolio job: scan → filter → submit → download → report.

    This is the main entry point called by the GUI or CLI.

    Args:
        source_dir: Folder containing drone photos
        job_type: Key from PRESETS (e.g., "construction_progress")
        site_name: Human name for the site (e.g., "MallTest")
        threshold: Nadir pitch threshold
        bbox: Optional (min_lat, max_lat, min_lon, max_lon)
        base_url: NodeODM URL override
        progress_callback: Optional callable(stage, detail) for GUI updates

    Returns:
        dict with keys: output_dir, downloaded, task_uuid, classification, etc.
    """
    log = logging.getLogger(__name__)
    preset = get_preset(job_type)

    def notify(stage, detail=""):
        if progress_callback:
            progress_callback(stage, detail)
        log.info(f"[{stage}] {detail}")

    # 1. Classify
    notify("scan", f"Scanning {source_dir}")
    classification = classify_photos(source_dir, threshold=threshold)
    if classification.total == 0:
        return {"error": "No photos found"}

    # 2. Filter by preset + optional bbox
    working_set = scan_for_job(classification, preset)
    if bbox:
        working_set = filter_photos(working_set, bbox=bbox)
    if working_set.total == 0:
        return {"error": "No photos match filter criteria"}

    notify("filtered", f"{working_set.total} photos selected ({preset['label']})")

    # 3. Build output dir
    date_str = datetime.now().strftime("%Y-%m-%d")
    output_dir = build_output_dir(site_name, date_str)
    os.makedirs(output_dir, exist_ok=True)

    # 4. Write site info
    write_site_info(output_dir, site_name, job_type)

    # 5. If no ODM options (shouldn't happen with current presets, but safety)
    if preset["odm_options"] is None:
        notify("sort", "Portfolio only — sorting locally")
        sort_photos(working_set, copy=True)
        return {
            "output_dir": output_dir,
            "classification": classification,
            "working_set": working_set,
            "downloaded": {},
            "task_uuid": None,
        }

    # 6. Submit to NodeODM
    photo_paths = [p.path for p in working_set.photos]
    task_name = f"portfolio-{site_name[:20]}-{date_str}"
    notify("submit", f"Submitting {len(photo_paths)} photos to NodeODM")

    task_uuid, result = submit_to_nodeodm(
        photo_paths, preset["odm_options"], task_name=task_name, base_url=base_url,
    )

    if task_uuid is None:
        return {"error": f"NodeODM: {result}"}

    notify("processing", "NodeODM processing complete")

    # 7. Download outputs
    notify("download", "Downloading outputs")
    downloaded = download_outputs(task_uuid, output_dir, preset["downloads"], base_url=base_url)

    # 8. Write manifest
    from photo_classifier import write_manifest
    write_manifest(working_set, Path(output_dir) / "manifest.json")

    notify("complete", f"Output: {output_dir}")

    return {
        "output_dir": output_dir,
        "classification": classification,
        "working_set": working_set,
        "downloaded": downloaded,
        "task_uuid": task_uuid,
        "preset": preset,
    }
```

**Step 4: Run tests**

Run: `python -m pytest test_portfolio_service.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add portfolio_service.py test_portfolio_service.py
git commit -m "feat: add portfolio service orchestration layer"
```

---

## Phase 3: GUI Redesign

### Task 3: Rewrite portfolio_maker.py with intent-driven flow

**Files:**
- Modify: `portfolio_maker.py` (full rewrite of `_build_ui` and action handlers)
- Reference: `odm_presets.py` (for JOB_TYPES list)
- Reference: `portfolio_service.py` (for process_job, check_nodeodm)

This is the largest task. The GUI keeps the same branding/colors but replaces the layout:

**Step 1: Rewrite _build_ui with new layout**

Replace the entire `_build_ui` method and all action handlers. The new flow:

1. Header with NodeODM status indicator
2. Photo Folder picker
3. Job Type radio buttons (from JOB_TYPES)
4. Site Name entry
5. Scan button
6. Results section (hidden until scan) — stats badges, photo count summary, output path
7. Advanced section (collapsed) — threshold, bbox, engine URL
8. Action buttons: Process + Portfolio Only
9. Progress bar + details log

Key implementation details:
- `_on_scan()` calls `classify_photos()` in a thread, shows results, reveals action buttons
- `_on_process()` calls `process_job()` in a thread with progress callback updating the status bar
- `_on_portfolio_only()` calls `sort_photos()` in a thread — local sort, no NodeODM
- NodeODM check runs on startup in background thread, updates green/red indicator
- Results section uses `pack_forget()` / `pack()` to show/hide after scan
- Advanced section uses a toggle button to expand/collapse

**Step 2: Test manually**

Run: `python portfolio_maker.py`
- Verify: window opens, all 6 job types visible
- Verify: NodeODM indicator shows red (if Docker not running) or green
- Verify: Browse → select folder → Scan works
- Verify: Stats badges populate after scan
- Verify: Process button enabled only after scan
- Verify: Advanced section expands/collapses

**Step 3: Commit**

```bash
git add portfolio_maker.py
git commit -m "feat: redesign GUI with intent-driven job type workflow"
```

---

## Phase 4: Report Generator (stub)

### Task 4: Create report_generator.py with construction_progress template

**Files:**
- Create: `report_generator.py`
- Create: `test_report_generator.py`

Start with one report type (construction_progress) as the pattern. The other 5 follow the same structure and can be added incrementally.

**Step 1: Write failing test**

```python
"""Tests for report_generator."""

import os
import pytest
from pathlib import Path
from report_generator import generate_report, REPORT_TYPES


class TestReportTypes:
    def test_all_types_registered(self):
        expected = {
            "construction_progress", "property_survey", "roof_inspection",
            "structures", "vegetation", "real_estate",
        }
        assert set(REPORT_TYPES.keys()) == expected


class TestGenerateReport:
    def test_construction_progress_creates_pdf(self, tmp_path):
        data = {
            "site_name": "MallTest",
            "date": "2026-03-16",
            "job_type": "construction_progress",
            "total_photos": 443,
            "nadir_count": 380,
            "oblique_count": 63,
            "platform": "DJI Mini 3 Pro",
            "gps_bounds": (36.824, 36.828, -76.418, -76.412),
            "ortho_path": None,
            "dsm_path": None,
            "downloads": {},
        }
        result = generate_report("construction_progress", data, str(tmp_path))
        assert result is not None
        assert os.path.exists(result["pdf_path"])
        assert result["pdf_path"].endswith(".pdf")

    def test_unknown_type_returns_none(self, tmp_path):
        result = generate_report("nonexistent", {}, str(tmp_path))
        assert result is None
```

**Step 2: Run to verify failure**

Run: `python -m pytest test_report_generator.py -v`
Expected: FAIL

**Step 3: Write report_generator.py**

Follow the `vegetation_report.py` pattern from drone-pipeline:
- ReportLab Platypus for PDF layout
- Sentinel branding (green/purple, footer with FAA Part 107, Veteran-Owned)
- Cover page with site name, date, job type
- Summary table (photo count, platform, GPS footprint)
- Embedded ortho thumbnail if available
- Methodology section
- Per-type sections (construction gets "Progress Tracking" and "Volume Changes")

The `REPORT_TYPES` dict maps each type to a generator function. Start with `construction_progress` fully implemented; the other 5 can be stubs that generate a basic summary report.

**Step 4: Run tests**

Run: `python -m pytest test_report_generator.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add report_generator.py test_report_generator.py
git commit -m "feat: add report generator with construction progress template"
```

---

## Phase 5: Wire report generation into service

### Task 5: Connect report_generator to process_job

**Files:**
- Modify: `portfolio_service.py` — add report generation after download step

**Step 1: Add report call between download and complete**

In `process_job()`, after the download step and before the "complete" notify, add:

```python
    # 8. Generate report
    notify("report", f"Generating {preset['report_type']} report")
    from report_generator import generate_report
    report_data = {
        "site_name": site_name,
        "date": date_str,
        "job_type": job_type,
        "total_photos": classification.total,
        "nadir_count": classification.nadir_count,
        "oblique_count": classification.oblique_count,
        "platform": classification.platform,
        "gps_bounds": classification.gps_bounds,
        "ortho_path": downloaded.get("orthophoto.tif"),
        "dsm_path": downloaded.get("dsm.tif"),
        "downloads": downloaded,
    }
    report_result = generate_report(preset["report_type"], report_data, output_dir)
```

**Step 2: Run existing tests**

Run: `python -m pytest test_portfolio_service.py test_odm_presets.py test_report_generator.py test_photo_classifier.py -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add portfolio_service.py
git commit -m "feat: wire report generation into portfolio service"
```

---

## Phase 6: Remaining report templates

### Task 6: Add 5 remaining report type generators

**Files:**
- Modify: `report_generator.py` — add property_survey, roof_inspection, structures, vegetation, real_estate generators

Each follows the same pattern as construction_progress but with type-specific sections:

- **property_survey**: Area calculation, elevation range, boundary coordinates
- **roof_inspection**: Condition assessment checklist, measurement annotations
- **structures**: Structural condition matrix, measurement points
- **vegetation**: Delegates to `vegetation_report.py` from drone-pipeline if available, falls back to basic ortho report
- **real_estate**: Property highlights, aerial hero image, area/dimensions

**Step 1: Add tests for each type generating a PDF**

**Step 2: Implement each generator function**

**Step 3: Run all tests**

Run: `python -m pytest -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add report_generator.py test_report_generator.py
git commit -m "feat: add all 6 report type templates"
```

---

## Phase 7: Final integration test

### Task 7: End-to-end manual test + cleanup

**Step 1: Start NodeODM Docker container**

```bash
docker start nodeodm  # or docker run -p 3000:3000 opendronemap/nodeodm
```

**Step 2: Run Portfolio Maker**

```bash
cd D:\Projects\PortfolioMaker
python portfolio_maker.py
```

Test flow:
1. Browse to a small test photo set (10-20 photos)
2. Select "Construction Progress"
3. Enter site name "Test_Integration"
4. Click Scan → verify stats
5. Click Process → verify NodeODM submission
6. Wait for completion → verify output folder created
7. Check: orthomosaic.tif, dsm.tif, report.pdf, manifest.json all present

**Step 3: Test Portfolio Only mode**

1. Select same photos
2. Select "Real Estate"
3. Click Portfolio Only → verify nadir/oblique sort without NodeODM

**Step 4: Clean up**

```bash
git add .gitignore  # add docs/ if needed
git commit -m "docs: add v2 design and implementation plan"
```

**Step 5: Final commit**

```bash
git add -A
git commit -m "feat: Portfolio Maker v2.0 — intent-driven workflow with job types and reports"
```

---

## Execution Order Summary

| Phase | Task | New Files | Tests |
|---|---|---|---|
| 1 | ODM Presets | `odm_presets.py` | `test_odm_presets.py` |
| 2 | Portfolio Service | `portfolio_service.py` | `test_portfolio_service.py` |
| 3 | GUI Redesign | modify `portfolio_maker.py` | manual |
| 4 | Report Generator | `report_generator.py` | `test_report_generator.py` |
| 5 | Wire reports | modify `portfolio_service.py` | existing |
| 6 | Remaining reports | modify `report_generator.py` | `test_report_generator.py` |
| 7 | Integration test | — | manual E2E |
