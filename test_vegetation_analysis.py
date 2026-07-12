"""Tests for vegetation_analysis — path resolution, availability, run bridge."""

import json
import subprocess

import pytest

import vegetation_analysis as va


@pytest.fixture
def fake_tools(tmp_path, monkeypatch):
    """Point env at an existing fake QGIS python + script."""
    qgis = tmp_path / "python-qgis-ltr.bat"
    script = tmp_path / "rgb_vegetation_analysis.py"
    qgis.write_text("@echo off")
    script.write_text("# fake")
    monkeypatch.setenv("QGIS_PYTHON", str(qgis))
    monkeypatch.setenv("VEG_SCRIPT", str(script))
    return qgis, script


def fake_summary(out_dir):
    return {
        "mission_id": "TEST", "index": "VARI", "threshold": 0.15,
        "min_area_m2": 2.0, "veg_pct": 12.13, "flagged_polygons": 2,
        "outputs": {
            "geopackage": str(out_dir / "vegetation.gpkg"),
            "pdf": str(out_dir / "vegetation.pdf"),
            "vari_raster": str(out_dir / "vegetation.tif"),
        },
    }


class TestResolution:
    def test_env_wins(self, fake_tools):
        qgis, script = fake_tools
        assert va.resolve_paths() == (str(qgis), str(script))

    def test_available_when_both_exist(self, fake_tools):
        assert va.veg_available() is True

    def test_unavailable_when_missing(self, monkeypatch):
        monkeypatch.setenv("QGIS_PYTHON", "C:/nope/python-qgis-ltr.bat")
        monkeypatch.setenv("VEG_SCRIPT", "C:/nope/script.py")
        assert va.veg_available() is False


class TestRun:
    def test_success_returns_summary(self, fake_tools, tmp_path, monkeypatch):
        ortho = tmp_path / "orthophoto.tif"
        ortho.write_bytes(b"tif")
        out_dir = tmp_path / "veg"
        calls = {}

        def fake_run(cmd, **kwargs):
            calls["cmd"] = cmd
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "summary.json").write_text(
                json.dumps(fake_summary(out_dir)))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(va.subprocess, "run", fake_run)
        result = va.run_vegetation_analysis(ortho, out_dir, mission_id="Site X")
        assert result["veg_pct"] == 12.13
        assert result["summary_path"] == str(out_dir / "summary.json")
        assert "--mission-id" in calls["cmd"] and "Site X" in calls["cmd"]
        assert "--dsm" not in calls["cmd"]  # no DSM given

    def test_threshold_and_dsm_forwarded(self, fake_tools, tmp_path, monkeypatch):
        ortho = tmp_path / "orthophoto.tif"
        dsm = tmp_path / "dsm.tif"
        ortho.write_bytes(b"t")
        dsm.write_bytes(b"d")
        out_dir = tmp_path / "veg"
        calls = {}

        def fake_run(cmd, **kwargs):
            calls["cmd"] = cmd
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "summary.json").write_text(
                json.dumps(fake_summary(out_dir)))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(va.subprocess, "run", fake_run)
        va.run_vegetation_analysis(ortho, out_dir, dsm_path=dsm,
                                   threshold=0.2, min_area_m2=5.0)
        cmd = calls["cmd"]
        assert "--dsm" in cmd and str(dsm) in cmd
        assert "--threshold" in cmd and "0.2" in cmd
        assert "--min-area" in cmd and "5.0" in cmd

    def test_nonzero_exit_returns_none(self, fake_tools, tmp_path, monkeypatch):
        ortho = tmp_path / "orthophoto.tif"
        ortho.write_bytes(b"t")
        monkeypatch.setattr(
            va.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"))
        assert va.run_vegetation_analysis(ortho, tmp_path / "veg") is None

    def test_missing_summary_returns_none(self, fake_tools, tmp_path, monkeypatch):
        ortho = tmp_path / "orthophoto.tif"
        ortho.write_bytes(b"t")
        monkeypatch.setattr(
            va.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
        assert va.run_vegetation_analysis(ortho, tmp_path / "veg") is None

    def test_missing_ortho_returns_none(self, fake_tools, tmp_path):
        assert va.run_vegetation_analysis(tmp_path / "nope.tif",
                                          tmp_path / "veg") is None

    def test_unconfigured_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QGIS_PYTHON", "C:/nope/q.bat")
        monkeypatch.setenv("VEG_SCRIPT", "C:/nope/s.py")
        ortho = tmp_path / "orthophoto.tif"
        ortho.write_bytes(b"t")
        assert va.run_vegetation_analysis(ortho, tmp_path / "veg") is None

    def test_unwritable_out_dir_returns_none(self, fake_tools, tmp_path,
                                             monkeypatch):
        ortho = tmp_path / "orthophoto.tif"
        ortho.write_bytes(b"t")

        def raise_oserror(self, **kw):
            raise PermissionError("read-only volume")

        monkeypatch.setattr(va.Path, "mkdir", raise_oserror)
        assert va.run_vegetation_analysis(ortho, tmp_path / "veg") is None

    def test_timeout_returns_none(self, fake_tools, tmp_path, monkeypatch):
        ortho = tmp_path / "orthophoto.tif"
        ortho.write_bytes(b"t")

        def raise_timeout(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 1)

        monkeypatch.setattr(va.subprocess, "run", raise_timeout)
        assert va.run_vegetation_analysis(ortho, tmp_path / "veg") is None


class TestDeliverables:
    def test_lists_existing_outputs_only(self, tmp_path):
        summary = fake_summary(tmp_path)
        (tmp_path / "vegetation.gpkg").write_bytes(b"g")
        (tmp_path / "vegetation.pdf").write_bytes(b"p")
        summary["summary_path"] = str(tmp_path / "summary.json")
        (tmp_path / "summary.json").write_text("{}")
        result = va.veg_deliverables(summary)
        assert set(result) == {"vegetation.gpkg", "vegetation.pdf",
                               "summary.json"}

    def test_empty_for_none(self):
        assert va.veg_deliverables(None) == {}
