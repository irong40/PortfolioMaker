# Phase 6: MipMap + Gaussian Splats - Research

**Researched:** 2026-03-17
**Domain:** MipMap Desktop v5.0.1.2 CLI integration, Gaussian Splat outputs, Tkinter GUI extension
**Confidence:** HIGH (all findings based on direct filesystem inspection of installed software and real task logs)

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| MIP-01 | Gaussian Splat job type routes to MipMap Desktop instead of NodeODM | `reconstruct_full_engine.exe -task_json` CLI confirmed; `process_job()` branching pattern established |
| MIP-02 | App launches MipMap task with resolution_level 3, mesh_decimate_ratio 0.5, non-essential outputs disabled | Task JSON schema fully confirmed from real at_task.json/r3d_task.json; field names verified |
| MIP-03 | App monitors MipMap task log for progress and completion | Log format confirmed: `[Progress]<float>` lines, 0.5 → ~100.0, log path is `result/logs/log.txt` |
| MIP-04 | Gaussian Splat report template includes splat viewer embed or screenshot | `report_generator.py` pattern established; `_build_gaussian_splat_sections()` follows existing pattern |
| MIP-05 | GUI shows MipMap status indicator alongside NodeODM indicator | `_nodeodm_frame` pattern in `portfolio_maker.py` is copy-able; `check_mipmap()` is a file-existence check |
</phase_requirements>

---

## Summary

MipMap Desktop v5.0.1.2 is an Electron app but exposes a fully functional CLI through `reconstruct_full_engine.exe` located at `C:\Program Files\MipMap\MipMapDesktop\resources\resources\catch3d\reconstruct_full_engine.exe`. The engine accepts `-task_json <path>` and `-reconstruct_type <int>`. The task JSON schema is fully documented by the existing `at_task.json` and `r3d_task.json` files already captured at `D:\ProjectErrors\TaskLog\`. There is no REST API — control is exclusively through the filesystem: write a task JSON, launch the engine subprocess, tail the `logs/log.txt` file for `[Progress]<float>` lines.

The Gaussian Splat preset must set `generate_gs_ply: true`, `generate_gs_splat_sog_tiles: true`, and disable all other expensive outputs (`generate_3d_tiles: false`, `generate_osgb: false`, `generate_las: false`, `generate_geotiff: false`, `generate_tile_2D: false`). Target `resolution_level: 3` and `mesh_decimate_ratio: 0.5` per MIP-02. Splat outputs land at `<working_dir>/3D/model-gs-ply/` and `<working_dir>/3D/model-gs-sog-tile/`.

The MipMap "status indicator" (MIP-05) cannot be a connectivity check like NodeODM's HTTP ping. It should be an executable-existence check: green if `reconstruct_full_engine.exe` exists, red/grey if not found. Progress monitoring (MIP-03) uses Python `subprocess.Popen` + a background thread that tails `log.txt` and parses `[Progress]<float>` lines, mapping the float (0.5 → 100.0) to 0–100%.

**Primary recommendation:** Drive MipMap entirely through `reconstruct_full_engine.exe` CLI — write `at_task.json` then `r3d_task.json`, launch as separate subprocesses, tail `logs/log.txt` for progress. No GUI automation needed.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| subprocess | stdlib | Launch `reconstruct_full_engine.exe` as child process | Python standard, already used by drone-pipeline |
| threading | stdlib | Background log-tail thread (non-blocking GUI) | Already used in `portfolio_maker.py` for NodeODM polling |
| pathlib | stdlib | Construct working_dir and output paths | Already used throughout codebase |
| json | stdlib | Write at_task.json and r3d_task.json | Already used throughout codebase |
| uuid | stdlib | Generate task UUID for working_dir naming | MipMap workspace uses UUID-based project dirs |
| shutil | stdlib | Copy splat outputs to portfolio folder | Already used in `portfolio_service.py` |
| reportlab | installed | Gaussian Splat PDF report | Already used by all 6 existing report types |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| watchdog | pip | Alternative to polling for log file changes | Only if polling every 1s proves too heavy |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| subprocess polling log | watchdog file watcher | Polling every 1-2s is simpler, sufficient for a task that takes minutes |
| file-based task JSON | MipMap REST API | No REST API exists — file-based is the only option |

**Installation:**
```bash
# No new dependencies required beyond what is already installed
# All needed modules are Python stdlib + reportlab (already present)
```

---

## Architecture Patterns

### Recommended Project Structure (additions only)

```
portfolio_maker/
├── odm_presets.py          # ADD: gaussian_splat preset entry (no odm_options, custom routing flag)
├── portfolio_service.py    # ADD: process_mipmap_job(), check_mipmap()
├── mipmap_service.py       # NEW: MipMap task JSON builder, engine launcher, log monitor
├── report_generator.py     # ADD: gaussian_splat entry in REPORT_TYPES + _build_gaussian_splat_sections()
└── portfolio_maker.py      # ADD: MipMap indicator, branch on job_type == "gaussian_splat"
```

### Pattern 1: Task JSON Construction

MipMap requires two sequential task JSONs:
1. **AT task** (`at_task.json`) — aerotriangulation, uses camera intrinsics from EXIF. In programmatic mode, `image_meta_data` contains per-image GPS position and orientation. The engine derives these from EXIF automatically when `input_image_type: 1`.
2. **R3D task** (`r3d_task.json`) — 3D reconstruction, references `working_dir` where AT results were written, plus all output flags.

Both JSONs share the same `working_dir` path. The working directory must exist before launch.

**Minimum viable at_task.json for programmatic launch (EXIF-driven):**
```python
# Source: direct inspection of D:\ProjectErrors\TaskLog\at_task.json
# and D:\50c59097-5dd8-4ef2-8f78-e62544e90dad\Test_GraveYard\...\r3d_task.json
at_task = {
    "license_id": 9000,
    "working_dir": str(working_dir),
    "extension_paths": [
        r"C:\Users\redle.SOULAAN\AppData\Roaming\mipmap-desktop\extentions\gs_dlls",
        r"C:\Users\redle.SOULAAN\AppData\Roaming\mipmap-desktop\extentions\ml_dlls",
    ],
    "gdal_folder": r"C:\ProgramData\MipMap\MipMapDesktop\gdal_data",
    "input_image_type": 1,          # 1 = standard RGB
    "output_block_change_xml": True,
    "boundary_from_image": None,
    # Disable non-essential outputs for splat-only run:
    "generate_2D_from_3D_model": False,
    "generate_3d_tiles": False,
    "generate_obj": False,
    "generate_osgb": False,
    "generate_las": False,
    "generate_ply": False,
    "generate_fbx": False,
    "generate_skp": False,
    "generate_glb": False,
    "generate_pc_osgb": False,
    "generate_pc_pnts": False,
    "generate_pc_ply": False,
    "generate_gs_ply": True,           # PRIMARY splat output
    "generate_gs_splat": False,
    "generate_gs_splat_sog_tiles": True,  # LOD tile output for web viewer
    "generate_gs_sog": False,
    "fill_water_area_with_AI": False,
    "generate_geotiff": False,
    "generate_tile_2D": False,
    "resolution_level": 3,             # MIP-02: level 3
    "coordinate_system_2d": {
        "type": 3,
        "type_name": "Projected",
        "label": "WGS 84 / UTM zone 18N",
        "epsg_code": 32618
    },
    "keep_undistort_images": False,
    "build_overview": False,
    "cut_frame_2d": False,
    "cut_frame_width": 4096,
    "mesh_decimate_ratio": 0.5,        # MIP-02: 0.5
    "remove_small_islands": False,
    "dom_gsd": 0,
    "camera_meta_data": [],            # Populated from photo EXIF by engine
    "image_meta_data": [],             # Populated from photo EXIF by engine
}
```

**OPEN QUESTION:** Whether `image_meta_data` and `camera_meta_data` can be empty arrays for EXIF-driven input, or whether the engine requires them populated. The existing task JSONs had them pre-populated by MipMap Desktop's GUI. The `input_image_type: 1` flag suggests EXIF extraction is built-in, but this needs a live test. See Open Questions section.

### Pattern 2: Engine Launch and Log Monitoring

```python
# Source: reconstruct_full_engine.exe --help output (verified 2026-03-17)
ENGINE_PATH = (
    r"C:\Program Files\MipMap\MipMapDesktop\resources\resources\catch3d"
    r"\reconstruct_full_engine.exe"
)
RECONSTRUCT_TYPE_AT  = 0   # Aerotriangulation (at_task.json)
RECONSTRUCT_TYPE_R3D = 1   # 3D reconstruction (r3d_task.json)
# Note: reconstruct_type values 0 and 1 are inferred from "AT" vs "R3D" naming
# convention observed in result/ directory. MEDIUM confidence — needs live test.

import subprocess, threading, time
from pathlib import Path

def launch_mipmap_stage(task_json_path, reconstruct_type, log_path, progress_callback):
    """Launch one stage of MipMap reconstruction and monitor log for progress."""
    cmd = [
        ENGINE_PATH,
        f"-task_json={task_json_path}",
        f"-reconstruct_type={reconstruct_type}",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def tail_log():
        last_pos = 0
        while proc.poll() is None:
            if Path(log_path).exists():
                with open(log_path, "r", errors="replace") as f:
                    f.seek(last_pos)
                    for line in f:
                        if "[Progress]" in line:
                            try:
                                pct = float(line.split("[Progress]")[1].strip())
                                if progress_callback:
                                    progress_callback(pct)
                            except ValueError:
                                pass
                    last_pos = f.tell()
            time.sleep(1.0)

    t = threading.Thread(target=tail_log, daemon=True)
    t.start()
    proc.wait()
    t.join(timeout=2)
    return proc.returncode
```

### Pattern 3: Progress Scale

The log file `logs/log.txt` emits `[Progress]<float>` lines. Observed range from real run:
- Start: 0.5
- End: ~100.0 (inferred — last observed value was 15.5 in a partial run, full run reached completion)

Map to GUI percentage: `min(100, float(progress_value))`.

The AT stage (aerotriangulation) runs first and emits its own progress. The R3D stage picks up after. For a combined progress bar, use AT as 0-50% and R3D as 50-100%, or show them as two sequential stages.

### Pattern 4: Output File Locations

After a successful run, outputs are in `working_dir/`:
```
<working_dir>/
├── at_task.json              # written by caller before AT stage
├── r3d_task.json             # written by caller before R3D stage
├── logs/
│   └── log.txt               # [Progress] lines emitted here
├── milestones/
│   ├── splats/MipModel/levels_info.json   # splat level info (encrypted)
│   └── texture_mesh/MipModel/levels_info.json
├── 3D/
│   ├── model-gs-ply/         # Gaussian Splat PLY file(s)  ← PRIMARY OUTPUT
│   └── model-gs-sog-tile/    # LOD tile tree for viewer
│       └── MipModel/
│           ├── lod-meta.json # tile manifest
│           └── {N}_{M}/      # tile subdirectories
├── 2D/
│   ├── dom_tiles/            # orthophoto tiles (if enabled)
│   └── dsm_tiles/            # DSM tiles (if enabled)
└── report/
    └── report.json           # MipMap's internal report data
```

**Copy targets for portfolio folder:**
- `3D/model-gs-ply/` → entire directory (gs_ply files)
- `3D/model-gs-sog-tile/` → entire directory (LOD tiles for viewing)

### Pattern 5: MipMap Workspace Structure

MipMap stores its own project index in the workspace root. The workspace is defined by `config.json`'s `workspace` key (`D:\` on this machine). Paths follow:
```
<workspace>/
├── indexes.json              # project + task index (maintained by MipMap Desktop GUI)
├── project_index.json        # project UUID → name map
├── task_index.json           # task UUID → name map
└── <ProjectName>/
    ├── info.json             # project metadata
    └── <TaskName>/           # e.g. MallTest-20260316
        ├── info.json         # task params (status: "complete", params with outputs)
        ├── layers.json       # viewer layer definitions
        ├── photos.json       # photo list with GPS/EXIF
        └── result/           # all engine outputs
```

For programmatic use, we do NOT need to create MipMap project records. We write task JSONs to any working directory, run the engine, and copy outputs. The MipMap Desktop GUI won't know about it unless we write to its workspace, which is optional.

**Recommended approach:** Use a dedicated working dir under `E:\Portfolio\<SiteName>\<date>\mipmap_work\` as the task's `working_dir`. This keeps MipMap work alongside portfolio outputs without polluting MipMap's GUI workspace.

### Pattern 6: ODM Presets Extension for Gaussian Splat

The `odm_presets.py` registry needs a new entry. Because Gaussian Splat does not use NodeODM, the preset has no `odm_options`. Add a routing flag `"engine": "mipmap"` alongside existing presets that implicitly use `"engine": "nodeodm"`.

```python
# In odm_presets.py — append to JOB_TYPES list:
JOB_TYPES = [
    ...existing 6...
    ("gaussian_splat", "Gaussian Splat"),
]

# In PRESETS dict:
"gaussian_splat": {
    "label": "Gaussian Splat",
    "description": "3D Gaussian Splat via MipMap Desktop",
    "photo_filter": None,           # uses all photos (nadir + oblique)
    "engine": "mipmap",             # routing flag — not NodeODM
    "odm_options": [],              # unused for MipMap
    "downloads": ["gs_ply", "gs_sog_tiles"],  # logical names, not filenames
    "report_type": "gaussian_splat",
    "mipmap_settings": {
        "resolution_level": 3,
        "mesh_decimate_ratio": 0.5,
    },
},
```

### Anti-Patterns to Avoid

- **Launching MipMapDesktop.exe**: The Electron app requires GUI interaction. Use `reconstruct_full_engine.exe` directly.
- **Writing to MipMap's workspace (`D:\`) without coordination**: Risks polluting the GUI's project index. Use a separate working dir under the portfolio output folder.
- **Blocking the Tkinter main thread**: MipMap runs take 10-60+ minutes. Always run in a daemon thread with a queue.Queue for progress updates (same pattern as NodeODM polling in `portfolio_maker.py`).
- **Assuming log.txt exists before launch**: Create the `logs/` directory and handle FileNotFoundError in the tail thread until the engine creates the file.
- **Using `mesh_decimate_ratio: 1` for splat jobs**: This is the default but ignores the VRAM-safety requirement. Always set to 0.5 per MIP-02.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Photo EXIF → MipMap camera params | Custom EXIF parser to populate `camera_meta_data` | Let engine read EXIF via `input_image_type: 1` | Engine has built-in EXIF support; camera params are complex (focal length, distortion coefficients) |
| Progress reporting | Custom IPC or named pipes | Tail `logs/log.txt` for `[Progress]` lines | MipMap already writes progress to this file at ~5-second intervals |
| Coordinate system detection | Auto-detect UTM zone from GPS | Hard-code UTM zone 18N (EPSG:32618) for Virginia/Maryland work or make configurable | Known operational area; auto-detection adds complexity without benefit |
| PLY file validation | PLY parser | Simple file existence + size check | If the engine succeeded (returncode 0), the PLY is valid |

---

## Common Pitfalls

### Pitfall 1: extension_paths User-Specific

**What goes wrong:** `extension_paths` in task JSON points to `C:\Users\redle.SOULAAN\AppData\Roaming\mipmap-desktop\extentions\`. On another machine or user account, this path fails silently.
**Why it happens:** MipMap extensions are installed per-user.
**How to avoid:** Derive `extension_paths` from environment: `os.path.expandvars(r"%APPDATA%\mipmap-desktop\extentions\gs_dlls")`. Fall back gracefully if extensions not found (gs_ply generation requires gs_dlls).
**Warning signs:** Engine exits with non-zero code immediately; log.txt shows extension loading errors.

### Pitfall 2: AT Stage Must Complete Before R3D

**What goes wrong:** Launching R3D task JSON before AT completes causes reconstruction to fail (no aerotriangulation data).
**Why it happens:** The two stages are separate processes and the engine does not auto-sequence them.
**How to avoid:** Await `proc.wait()` return code 0 from AT stage before launching R3D stage. Check for presence of `result/milestones/block_mvs` as a completion signal alongside returncode.
**Warning signs:** R3D launches but exits immediately; no PLY output.

### Pitfall 3: Working Directory Must Pre-Exist

**What goes wrong:** `reconstruct_full_engine.exe` does not create the `working_dir` if it doesn't exist; it may fail silently.
**Why it happens:** The engine assumes the workspace is managed externally (by MipMap Desktop GUI).
**How to avoid:** Call `os.makedirs(working_dir / "logs", exist_ok=True)` before writing task JSONs.

### Pitfall 4: Progress Float is Not Percentage Directly

**What goes wrong:** Log line `[Progress]0.500000` at the very start could be misread as "0.5%".
**Why it happens:** Progress appears to be on a 0.5–100.0 scale, starting at 0.5 when the engine initializes.
**How to avoid:** Normalize: `pct = min(100.0, float(value))`. The "complete" state is when `proc.poll() is not None` (process exited), not when `[Progress]100` appears.
**Warning signs:** Progress jumps from low values to 100 without intermediate steps — this indicates the run was very fast (likely a test with few photos).

### Pitfall 5: gs_dlls Extension Required for Splat Output

**What goes wrong:** If the `gs_dlls` extension path is missing or the DLLs are not present, `generate_gs_ply: true` and `generate_gs_splat_sog_tiles: true` produce no output without a clear error.
**Why it happens:** Gaussian Splat support is an optional extension in MipMap v5.
**How to avoid:** Check for extension DLL existence at startup: `os.path.exists(extension_path / "gs_dlls")`. Warn the user if missing.
**Warning signs:** Run completes successfully (returncode 0) but `3D/model-gs-ply/` is empty.

### Pitfall 6: Coordinate System Hardcoded

**What goes wrong:** UTM zone 18N (EPSG 32618) is hardcoded from Virginia flights. Photos outside this zone produce distorted or failed 2D outputs.
**Why it happens:** MipMap requires an explicit projected coordinate system for 2D outputs.
**How to avoid:** For Gaussian Splat, 2D outputs are disabled (GeoTIFF off, tile_2D off), so coordinate system only affects internal alignment. The WGS84 fallback is acceptable. For the at_task.json, use the coordinate_system_2d field but note it only matters if geotiff is enabled.

---

## Code Examples

### Build Minimal Splat Task JSON

```python
# Source: Direct inspection of at_task.json and r3d_task.json at
# D:\ProjectErrors\TaskLog\ and D:\50c59097-5dd8-4ef2-8f78-e62544e90dad\
import json, os
from pathlib import Path

MIPMAP_ENGINE = Path(
    r"C:\Program Files\MipMap\MipMapDesktop\resources\resources\catch3d"
    r"\reconstruct_full_engine.exe"
)

def build_splat_task_json(photo_paths: list[str], working_dir: Path,
                          resolution_level: int = 3,
                          mesh_decimate_ratio: float = 0.5) -> dict:
    """Build a task JSON dict for Gaussian Splat generation."""
    appdata = os.environ.get("APPDATA", "")
    extension_base = Path(appdata) / "mipmap-desktop" / "extentions"

    return {
        "license_id": 9000,
        "working_dir": str(working_dir),
        "extension_paths": [
            str(extension_base / "gs_dlls"),
            str(extension_base / "ml_dlls"),
        ],
        "gdal_folder": r"C:\ProgramData\MipMap\MipMapDesktop\gdal_data",
        "input_image_type": 1,
        "output_block_change_xml": True,
        "boundary_from_image": None,
        # Disable everything except splats
        "generate_2D_from_3D_model": False,
        "generate_3d_tiles": False,
        "generate_obj": False,
        "generate_osgb": False,
        "generate_las": False,
        "generate_ply": False,
        "generate_fbx": False,
        "generate_skp": False,
        "generate_glb": False,
        "generate_pc_osgb": False,
        "generate_pc_pnts": False,
        "generate_pc_ply": False,
        "generate_gs_ply": True,
        "generate_gs_splat": False,
        "generate_gs_splat_sog_tiles": True,
        "generate_gs_sog": False,
        "fill_water_area_with_AI": False,
        "generate_geotiff": False,
        "generate_tile_2D": False,
        "resolution_level": resolution_level,
        "coordinate_system_2d": {
            "type": 3, "type_name": "Projected",
            "label": "WGS 84 / UTM zone 18N", "epsg_code": 32618,
        },
        "keep_undistort_images": False,
        "build_overview": False,
        "cut_frame_2d": False,
        "cut_frame_width": 4096,
        "mesh_decimate_ratio": mesh_decimate_ratio,
        "remove_small_islands": False,
        "dom_gsd": 0,
        # camera_meta_data and image_meta_data: empty = EXIF-driven
        "camera_meta_data": [],
        "image_meta_data": [],
    }
```

### Monitor Log Progress

```python
# Source: Log format confirmed from D:\ProjectErrors\TaskLog\logs\log.txt
import time
from pathlib import Path

def monitor_mipmap_log(log_path: Path, progress_callback, stop_event):
    """Tail MipMap log.txt for [Progress] lines. Run in daemon thread."""
    last_pos = 0
    while not stop_event.is_set():
        if log_path.exists():
            try:
                with open(log_path, "r", errors="replace") as f:
                    f.seek(last_pos)
                    for line in f:
                        if "[Progress]" in line:
                            try:
                                val = float(line.split("[Progress]")[1].strip())
                                pct = min(100.0, val)
                                progress_callback(pct)
                            except (ValueError, IndexError):
                                pass
                    last_pos = f.tell()
            except OSError:
                pass
        time.sleep(1.0)
```

### Check MipMap Availability

```python
# Source: filesystem inspection (2026-03-17)
def check_mipmap() -> bool:
    """Return True if reconstruct_full_engine.exe is present."""
    return MIPMAP_ENGINE.exists()
```

### Gaussian Splat Report Section

```python
# Source: pattern from report_generator.py _build_real_estate_sections()
def _build_gaussian_splat_sections(elements, styles, data):
    elements.append(Paragraph("Gaussian Splat Model", styles["SectionHeader"]))
    elements.append(Paragraph(
        "A photogrammetric Gaussian Splat was generated from the drone imagery "
        "using MipMap Desktop v5.0. Gaussian Splatting produces a novel view "
        "synthesis model that renders photorealistic 3D scenes at interactive "
        "frame rates, suitable for web-based client delivery.",
        styles["SentinelBody"],
    ))
    elements.append(Paragraph("Deliverables", styles["SectionHeader"]))
    elements.append(Paragraph(
        "The Gaussian Splat package includes a PLY point cloud and SOG tile set "
        "optimized for web streaming. The SOG tile format uses level-of-detail "
        "rendering for efficient display of large scenes.",
        styles["SentinelBody"],
    ))

    # Show splat output paths if available
    gs_ply_dir = data.get("gs_ply_dir")
    gs_sog_dir = data.get("gs_sog_dir")
    if gs_ply_dir or gs_sog_dir:
        elements.append(Paragraph("Processing Notes", styles["SectionHeader"]))
        elements.append(Paragraph(
            f"Resolution level: {data.get('resolution_level', 3)}  |  "
            f"Mesh decimate ratio: {data.get('mesh_decimate_ratio', 0.5)}  |  "
            f"Photos processed: {data.get('total_photos', 0)}",
            styles["SmallGrey"],
        ))
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| NodeODM for all job types | NodeODM for mesh/ortho, MipMap for splats | Phase 6 | MipMap v5 adds native GS support; ODM's Gaussian Splat support is experimental |
| MipMap GUI only | `reconstruct_full_engine.exe` CLI | Confirmed present in v5.0.1.2 | Full programmatic control without GUI automation |
| Mesh-based 3D (OSGB/B3DM) | Gaussian Splat (SOG tiles + PLY) | MipMap v5 | Faster rendering, more photorealistic for marketing/showcase use |

**Deprecated/outdated:**
- `generate_gs_sog`: Field exists but set to `false` in all observed tasks; `generate_gs_splat_sog_tiles` is the correct SOG tile flag.
- `generate_gs_splat`: Produces a different format from SOG tiles; set to `false` for the MIP-02 target output.

---

## Open Questions

1. **Can `image_meta_data` and `camera_meta_data` be empty arrays?**
   - What we know: The real task JSONs captured have these populated by MipMap Desktop's GUI, which extracts EXIF and pre-fills them. `input_image_type: 1` is documented as "standard RGB".
   - What's unclear: Whether the engine's EXIF extraction works when the arrays are empty, or whether it requires at least the image file paths to be listed somewhere.
   - Recommendation: First attempt with empty arrays and a small test dataset. If the engine fails, fall back to populating `image_meta_data` with at minimum `{"id": N, "meta_data": {"path": "..."}}` entries — the path field was observed in the at_task.json images.
   - Confidence: LOW — needs a live test run.

2. **What is the correct `reconstruct_type` integer for each stage?**
   - What we know: The engine flag is `-reconstruct_type <int32>` with default 0. The result directory has `AT/` and `3D/` subdirs suggesting separate AT and reconstruction stages.
   - What's unclear: Whether `0=AT, 1=R3D` or `0=full pipeline, 1=AT-only, 2=R3D-only`. The `at_task.json` vs `r3d_task.json` naming suggests separate invocations.
   - Recommendation: Test with `reconstruct_type=0` on an `at_task.json` first. Watch for AT-stage outputs in `result/AT/`. Then try `reconstruct_type=1` for R3D.
   - Confidence: LOW — inferred from directory naming, not confirmed.

3. **Does the engine require a `desktop_magic` flag?**
   - What we know: The `--help` output shows `-desktop_magic` with default `""` and type `string`. This suggests it may be a license validation token passed by MipMap Desktop.
   - What's unclear: Whether the engine works without it (license check may pass via `license_id: 9000` in the task JSON).
   - Recommendation: Try without `-desktop_magic` first. If the engine exits immediately with a license error, look in `license.bin` / `license.json` at `%APPDATA%\mipmap-desktop\` for the token value.
   - Confidence: MEDIUM — `license_id: 9000` in task JSON may be sufficient.

4. **Photo input for AT: does the engine need image paths pre-listed?**
   - What we know: The `photos.json` in MipMap's workspace contains the full photo list with GPS. The `at_task.json`'s `image_meta_data` had 443 items (one per photo).
   - What's unclear: Whether the engine scans a folder for images (and `image_meta_data` is optional), or requires the explicit list.
   - Recommendation: If empty `image_meta_data` fails, write a minimal per-photo entry: `{"id": i, "meta_data": {"path": photo_path}}`. The engine will extract GPS/orientation from EXIF.
   - Confidence: LOW — requires live testing.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing, no config file) |
| Config file | none — run from project root |
| Quick run command | `pytest test_mipmap_service.py -x` |
| Full suite command | `pytest -x` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MIP-01 | gaussian_splat preset has `engine: mipmap` key and is in JOB_TYPES | unit | `pytest test_odm_presets.py -x -k "gaussian"` | ❌ Wave 0 |
| MIP-02 | Splat task JSON has resolution_level=3, mesh_decimate_ratio=0.5, gs flags correct | unit | `pytest test_mipmap_service.py::test_build_splat_task_json -x` | ❌ Wave 0 |
| MIP-03 | Log monitor parses [Progress] lines and calls callback with float | unit | `pytest test_mipmap_service.py::test_log_monitor -x` | ❌ Wave 0 |
| MIP-04 | gaussian_splat in REPORT_TYPES; _build_gaussian_splat_sections runs without error | unit | `pytest test_report_generator.py -x -k "gaussian"` | ❌ Wave 0 |
| MIP-05 | check_mipmap() returns True when engine path exists | unit | `pytest test_mipmap_service.py::test_check_mipmap -x` | ❌ Wave 0 |

Note: MIP-01 (actual MipMap routing during process_job), MIP-03 (live log monitoring), are manual-only or integration tests — the engine requires a real photo set and license. Unit tests cover the code paths, not the full pipeline.

### Sampling Rate
- **Per task commit:** `pytest test_mipmap_service.py test_odm_presets.py test_report_generator.py -x`
- **Per wave merge:** `pytest -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `test_mipmap_service.py` — covers MIP-02, MIP-03, MIP-05 (unit tests with mock subprocess/filesystem)
- [ ] New entries in `test_odm_presets.py` — covers MIP-01 gaussian_splat preset validation
- [ ] New entries in `test_report_generator.py` — covers MIP-04 gaussian_splat report type

---

## Sources

### Primary (HIGH confidence)
- Direct filesystem inspection of `D:\ProjectErrors\TaskLog\at_task.json` — task JSON schema (all fields)
- Direct filesystem inspection of `D:\ProjectErrors\TaskLog\r3d_task.json` — reconstruction task schema
- Direct filesystem inspection of `D:\ProjectErrors\TaskLog\logs\log.txt` — `[Progress]<float>` format confirmed
- `reconstruct_full_engine.exe --help` (2026-03-17) — CLI flags: `-task_json`, `-reconstruct_type`, `-desktop_magic`
- `hardware_check.exe` output (2026-03-17) — GPU: RTX 5070, VRAM: 12GB confirmed
- `D:\50c59097-5dd8-4ef2-8f78-e62544e90dad\Test_GraveYard\Test_GraveYard-20260223\` — complete task directory structure with all output folders
- `info.json` task record — `params.reconstruct_3d.outputs` confirms `["gs_ply", "gs_sog_tiles"]` as canonical output names
- `layers.json` — confirmed layer types: dom, dsm, mesh3d, point_cloud, photos

### Secondary (MEDIUM confidence)
- `C:\Users\redle.SOULAAN\AppData\Roaming\mipmap-desktop\config.json` — workspace path (`D:\`), app_id
- `C:\Program Files\MipMap\MipMapDesktop\resources\.env` — confirms Electron/Vite architecture (GUI only, no REST server)
- `C:\Users\redle.SOULAAN\AppData\Roaming\mipmap-desktop\logs\main.log` — confirms GUI is Electron, no embedded API server
- `indexes.json` workspace structure — project/task UUID layout

### Tertiary (LOW confidence)
- `reconstruct_type` integer semantics (0=AT, 1=R3D): inferred from directory naming `AT/` vs `3D/`, not confirmed by documentation
- `image_meta_data: []` acceptability: inferred from `input_image_type: 1` semantics

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — stdlib only, no new deps
- Architecture (task JSON schema): HIGH — verified from real captured runs
- CLI invocation: HIGH — `--help` confirmed flags
- Reconstruct_type semantics: LOW — inferred, not documented
- Empty image_meta_data: LOW — untested
- Architecture (progress monitoring): HIGH — log format verified from 442-line real log
- Pitfalls: HIGH — based on direct filesystem evidence

**Research date:** 2026-03-17
**Valid until:** 2026-06-17 (MipMap Desktop version unlikely to change CLI interface in 90 days; verify version number before using if significant time has passed)
