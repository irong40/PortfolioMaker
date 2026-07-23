"""Tests for opensplat_service — project assembly, preflight, path rewrite,
failure taxonomy, output copy. No GPU/docker required (subprocess mocked)."""

import json
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from opensplat_service import (
    to_wsl_path,
    extract_opensfm,
    rewrite_image_list,
    preflight_project,
    pick_downscale_factor,
    run_opensplat_pipeline,
    copy_splat_outputs,
    FAILURE_TAXONOMY,
    OPENSPLAT_IMAGE,
)


# ─── FIXTURES ────────────────────────────────────────────────────────────────

SHOTS = ["DJI_0001.JPG", "DJI_0002.JPG", "DJI_0003.JPG"]


@pytest.fixture
def all_zip(tmp_path):
    """A NodeODM-style all.zip with opensfm files (absolute container paths)."""
    recon = [{"cameras": {"cam1": {}}, "shots": {s: {} for s in SHOTS}}]
    zp = tmp_path / "all.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("opensfm/reconstruction.json", json.dumps(recon))
        z.writestr(
            "opensfm/image_list.txt",
            "\n".join(f"/var/www/data/uuid-1234/images/{s}" for s in SHOTS),
        )
    return zp


@pytest.fixture
def photo_dir(tmp_path):
    d = tmp_path / "photos"
    d.mkdir()
    for s in SHOTS:
        (d / s).write_bytes(b"jpegdata")
    return d


# ─── PATH CONVERSION ─────────────────────────────────────────────────────────

class TestWslPath:
    def test_drive_conversion(self):
        assert to_wsl_path(r"E:\Portfolio\job") == "/mnt/e/Portfolio/job"

    def test_lowercase_drive(self):
        assert to_wsl_path(r"d:\x") == "/mnt/d/x"

    def test_relative_path_rejected(self):
        with pytest.raises(ValueError):
            to_wsl_path("relative/path")


# ─── EXTRACTION + REWRITE ────────────────────────────────────────────────────

class TestAssembly:
    def test_extract_pulls_both_files(self, all_zip, tmp_path):
        proj = tmp_path / "odm_project"
        opensfm = extract_opensfm(all_zip, proj)
        assert (opensfm / "reconstruction.json").is_file()
        assert (opensfm / "image_list.txt").is_file()

    def test_extract_missing_file_raises(self, tmp_path):
        zp = tmp_path / "bad.zip"
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("opensfm/reconstruction.json", "[]")
        with pytest.raises(ValueError, match="image_list"):
            extract_opensfm(zp, tmp_path / "p")

    def test_rewrite_to_container_paths(self, all_zip, tmp_path):
        proj = tmp_path / "odm_project"
        opensfm = extract_opensfm(all_zip, proj)
        basenames = rewrite_image_list(opensfm)
        assert basenames == SHOTS
        lines = (opensfm / "image_list.txt").read_text().splitlines()
        assert lines[0] == "/work/odm_project/images/DJI_0001.JPG"

    def test_rewrite_writes_lf_only(self, all_zip, tmp_path):
        """CRLF endings turn every path into '...JPG\\r' inside the Linux
        container (live failure 2026-07-23) — the file must be pure LF."""
        proj = tmp_path / "odm_project"
        opensfm = extract_opensfm(all_zip, proj)
        rewrite_image_list(opensfm)
        raw = (opensfm / "image_list.txt").read_bytes()
        assert b"\r" not in raw


class TestDownscaleHeuristic:
    def test_small_sets_keep_base(self):
        assert pick_downscale_factor(23) == 2
        assert pick_downscale_factor(150) == 2

    def test_scale_tiers(self):
        assert pick_downscale_factor(400) == 3
        assert pick_downscale_factor(700) == 4
        assert pick_downscale_factor(905) == 5
        assert pick_downscale_factor(2000) == 5

    def test_never_below_base(self):
        assert pick_downscale_factor(23, base=4) == 4


# ─── PREFLIGHT ───────────────────────────────────────────────────────────────

class TestPreflight:
    def _assemble(self, all_zip, tmp_path, drop_image=None):
        proj = tmp_path / "odm_project"
        opensfm = extract_opensfm(all_zip, proj)
        rewrite_image_list(opensfm)
        images = proj / "images"
        images.mkdir()
        for s in SHOTS:
            if s != drop_image:
                (images / s).write_bytes(b"x")
        return proj

    def test_pass_when_complete(self, all_zip, tmp_path):
        proj = self._assemble(all_zip, tmp_path)
        assert preflight_project(proj) == []

    def test_missing_image_detected(self, all_zip, tmp_path):
        proj = self._assemble(all_zip, tmp_path, drop_image="DJI_0002.JPG")
        problems = preflight_project(proj)
        assert any("DJI_0002.JPG" in p for p in problems)

    def test_uncovered_shot_detected(self, all_zip, tmp_path):
        proj = self._assemble(all_zip, tmp_path)
        lst = proj / "opensfm" / "image_list.txt"
        lines = lst.read_text().splitlines()
        lst.write_text("\n".join(lines[:-1]) + "\n")  # drop last image
        problems = preflight_project(proj)
        assert any("shot not covered" in p for p in problems)


# ─── PIPELINE (subprocess mocked) ────────────────────────────────────────────

def _mock_proc(stdout_lines, returncode=0):
    proc = MagicMock()
    proc.stdout = iter(stdout_lines)
    proc.wait.return_value = None
    proc.returncode = returncode
    return proc


class TestPipeline:
    def test_happy_path(self, all_zip, photo_dir, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        pcts = []

        def fake_popen(cmd, **kw):
            # Simulate training output + splat write
            (work / "splat.ply").write_bytes(b"ply")
            (work / "cameras.json").write_text("{}")
            return _mock_proc(["Step 100: 0.2 (33%)\n", "Step 300: 0.1 (100%)\n"])

        with patch("opensplat_service.subprocess.Popen", side_effect=fake_popen):
            r = run_opensplat_pipeline(
                photo_dir, work, progress_callback=pcts.append,
                num_iters=300, all_zip=all_zip,
            )
        assert r["returncode"] == 0
        assert r["error"] is None
        assert Path(r["gs_ply_dir"]).joinpath("splat.ply").is_file()
        assert r["gs_sog_dir"] is None
        assert pcts[-1] == 100.0
        assert any(30 < p < 40 for p in pcts)  # Step 100/300

    def test_mount_and_image_in_command(self, all_zip, photo_dir, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        seen = {}

        def fake_popen(cmd, **kw):
            seen["cmd"] = cmd
            (work / "splat.ply").write_bytes(b"ply")
            return _mock_proc([])

        with patch("opensplat_service.subprocess.Popen", side_effect=fake_popen):
            run_opensplat_pipeline(photo_dir, work, all_zip=all_zip)
        assert OPENSPLAT_IMAGE in seen["cmd"]
        assert "--gpus" in seen["cmd"]

    @pytest.mark.parametrize("signature", list(FAILURE_TAXONOMY))
    def test_failure_taxonomy(self, all_zip, photo_dir, tmp_path, signature):
        work = tmp_path / "work"
        work.mkdir()
        with patch(
            "opensplat_service.subprocess.Popen",
            return_value=_mock_proc([f"blah {signature} blah\n"], returncode=1),
        ):
            r = run_opensplat_pipeline(photo_dir, work, all_zip=all_zip)
        assert r["returncode"] == 1
        assert r["error"] == FAILURE_TAXONOMY[signature]

    def test_success_without_splat_is_failure(self, all_zip, photo_dir, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        with patch(
            "opensplat_service.subprocess.Popen",
            return_value=_mock_proc([], returncode=0),
        ):
            r = run_opensplat_pipeline(photo_dir, work, all_zip=all_zip)
        assert r["returncode"] == 1
        assert "no splat.ply" in r["error"]

    def test_preflight_blocks_run(self, all_zip, tmp_path):
        empty_photos = tmp_path / "none"
        empty_photos.mkdir()
        work = tmp_path / "work"
        work.mkdir()
        with patch("opensplat_service.subprocess.Popen") as popen:
            r = run_opensplat_pipeline(empty_photos, work, all_zip=all_zip)
        popen.assert_not_called()
        assert "preflight failed" in r["error"]


# ─── OUTPUT COPY ─────────────────────────────────────────────────────────────

class TestCopyOutputs:
    def test_ply_only_no_sog(self, tmp_path):
        """The SOG-dir-absent case — OpenSplat never produces it."""
        work = tmp_path / "work"
        (work / "3D" / "model-gs-ply").mkdir(parents=True)
        (work / "3D" / "model-gs-ply" / "splat.ply").write_bytes(b"ply")
        dest = tmp_path / "out"
        dest.mkdir()
        copied = copy_splat_outputs(work, dest)
        assert "model-gs-ply" in copied
        assert "model-gs-sog-tile" not in copied
        assert (dest / "model-gs-ply" / "splat.ply").is_file()
