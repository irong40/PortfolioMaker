"""
vegetation_analysis.py — Sentinel Aerial
Bridge to the headless QGIS VARI vegetation analysis in the drone-pipeline
repo (qgis/rgb_vegetation_analysis.py). Runs the analysis on a NodeODM
orthophoto and returns the parsed summary.json.

The external script must run under the QGIS-LTR python (python-qgis-ltr.bat)
so PyQGIS/GDAL resolve. Paths resolve, in order: env vars QGIS_PYTHON /
VEG_SCRIPT > sortie_settings.json keys qgis_python / vegetation_script >
defaults below. Everything degrades gracefully — a missing QGIS install or
a failed run never breaks the mission pipeline.

Outputs (written by the external script into out_dir):
    vegetation.gpkg   flagged vegetation polygons (GeoPackage)
    vegetation.pdf    styled map export (Print Layout)
    vegetation.tif    VARI index raster (float32, -1..1)
    summary.json      machine-readable run summary (returned as dict)
"""

import json
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
SETTINGS_FILE = SCRIPT_DIR / "sortie_settings.json"

DEFAULT_QGIS_PYTHON = r"C:\Program Files\QGIS 3.44.12\bin\python-qgis-ltr.bat"
DEFAULT_VEG_SCRIPT = (
    r"C:\Users\redle.SOULAAN\Documents\drone-pipeline\qgis"
    r"\rgb_vegetation_analysis.py"
)

DEFAULT_TIMEOUT_S = 3600  # large orthos can take a while under QGIS python


def _settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def resolve_paths():
    """Resolve (qgis_python, veg_script) from env > settings > defaults."""
    settings = _settings()
    qgis_python = (os.environ.get("QGIS_PYTHON")
                   or settings.get("qgis_python")
                   or DEFAULT_QGIS_PYTHON)
    veg_script = (os.environ.get("VEG_SCRIPT")
                  or settings.get("vegetation_script")
                  or DEFAULT_VEG_SCRIPT)
    return qgis_python, veg_script


def veg_available():
    """True when both the QGIS python and the analysis script exist."""
    qgis_python, veg_script = resolve_paths()
    return Path(qgis_python).exists() and Path(veg_script).exists()


def run_vegetation_analysis(ortho_path, out_dir, mission_id="ad-hoc",
                            dsm_path=None, threshold=None, min_area_m2=None,
                            timeout_s=DEFAULT_TIMEOUT_S):
    """Run the external VARI analysis. Returns the summary dict, or None.

    None means the run failed or produced no summary — callers continue
    without vegetation results. Thresholds default to the script's own
    defaults (0.15 VARI / 2.0 m²) when not given.
    """
    qgis_python, veg_script = resolve_paths()
    if not (Path(qgis_python).exists() and Path(veg_script).exists()):
        log.info("QGIS vegetation analysis not configured "
                 "(qgis_python=%s, vegetation_script=%s)",
                 qgis_python, veg_script)
        return None
    if not Path(ortho_path).exists():
        log.warning("Vegetation analysis skipped — ortho missing: %s",
                    ortho_path)
        return None

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cmd = [str(qgis_python), str(veg_script),
           "--ortho", str(ortho_path),
           "--out", str(out),
           "--mission-id", str(mission_id)]
    if dsm_path and Path(dsm_path).exists():
        cmd += ["--dsm", str(dsm_path)]
    if threshold is not None:
        cmd += ["--threshold", str(threshold)]
    if min_area_m2 is not None:
        cmd += ["--min-area", str(min_area_m2)]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_s)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.error("Vegetation analysis failed to run: %s", exc)
        return None
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-2000:]
        log.error("Vegetation analysis exited %d:\n%s", proc.returncode, tail)
        return None

    summary_path = out / "summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.error("Vegetation analysis produced no readable summary.json: %s",
                  exc)
        return None
    summary["summary_path"] = str(summary_path)
    return summary


def veg_deliverables(summary):
    """{filename: path} of vegetation outputs that exist on disk."""
    if not summary:
        return {}
    candidates = dict(summary.get("outputs") or {})
    if summary.get("summary_path"):
        candidates["summary"] = summary["summary_path"]
    return {Path(p).name: str(p)
            for p in candidates.values() if p and Path(p).exists()}
