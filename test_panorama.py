"""Tests for panorama detection, stitching, and settings persistence."""

import json
import math
from pathlib import Path

import pytest

from photo_classifier import (
    PanoramaSet,
    ClassificationResult,
    scan_panoramas,
)


# ─── PanoramaSet dataclass ─────────────────────────────────────────────────

class TestPanoramaSet:
    def test_defaults(self):
        ps = PanoramaSet(folder="/test/PANORAMA/001", photo_count=25)
        assert ps.folder == "/test/PANORAMA/001"
        assert ps.photo_count == 25
        assert ps.photos == []
        assert ps.stitched_path == ""
        assert ps.stitch_error == ""

    def test_with_photos(self):
        photos = ["/test/PANO_0001.JPG", "/test/PANO_0002.JPG"]
        ps = PanoramaSet(folder="/test", photo_count=2, photos=photos)
        assert len(ps.photos) == 2

    def test_stitch_error_tracking(self):
        ps = PanoramaSet(folder="/test", photo_count=3)
        ps.stitch_error = "not enough overlap"
        assert ps.stitch_error == "not enough overlap"
        assert ps.stitched_path == ""


# ─── scan_panoramas ────────────────────────────────────────────────────────

class TestScanPanoramas:
    def test_detects_panorama_subfolders(self, tmp_path):
        pano_dir = tmp_path / "PANORAMA"
        set1 = pano_dir / "001_0001"
        set1.mkdir(parents=True)
        for i in range(5):
            (set1 / f"PANO_{i:04d}.JPG").write_bytes(b"fake")

        sets = scan_panoramas(str(tmp_path))
        assert len(sets) == 1
        assert sets[0].photo_count == 5
        assert str(set1) == sets[0].folder

    def test_multiple_sets(self, tmp_path):
        pano_dir = tmp_path / "PANORAMA"
        for name in ["001_0001", "002_0002", "003_0003"]:
            sub = pano_dir / name
            sub.mkdir(parents=True)
            for i in range(3):
                (sub / f"PANO_{i:04d}.JPG").write_bytes(b"fake")

        sets = scan_panoramas(str(tmp_path))
        assert len(sets) == 3

    def test_no_panorama_folder(self, tmp_path):
        (tmp_path / "DJI_0001.JPG").write_bytes(b"fake")
        sets = scan_panoramas(str(tmp_path))
        assert sets == []

    def test_empty_panorama_folder(self, tmp_path):
        (tmp_path / "PANORAMA").mkdir()
        sets = scan_panoramas(str(tmp_path))
        assert sets == []

    def test_empty_set_subfolder(self, tmp_path):
        sub = tmp_path / "PANORAMA" / "001_0001"
        sub.mkdir(parents=True)
        # No photos inside
        sets = scan_panoramas(str(tmp_path))
        assert sets == []

    def test_skips_non_jpg_files(self, tmp_path):
        sub = tmp_path / "PANORAMA" / "001_0001"
        sub.mkdir(parents=True)
        (sub / "PANO_0001.JPG").write_bytes(b"fake")
        (sub / "PANO_0002.DNG").write_bytes(b"fake")  # not jpg
        (sub / "readme.txt").write_bytes(b"fake")

        sets = scan_panoramas(str(tmp_path))
        assert sets[0].photo_count == 1  # only JPG

    def test_detects_from_parent_directory(self, tmp_path):
        """When scanning a DJI_xxx subfolder, should find PANORAMA at parent level."""
        pano_dir = tmp_path / "PANORAMA" / "001_0001"
        pano_dir.mkdir(parents=True)
        (pano_dir / "PANO_0001.JPG").write_bytes(b"fake")
        (pano_dir / "PANO_0002.JPG").write_bytes(b"fake")

        photo_dir = tmp_path / "DJI_001"
        photo_dir.mkdir()

        sets = scan_panoramas(str(photo_dir))
        assert len(sets) == 1
        assert sets[0].photo_count == 2

    def test_sorted_by_folder_name(self, tmp_path):
        pano_dir = tmp_path / "PANORAMA"
        for name in ["003", "001", "002"]:
            sub = pano_dir / name
            sub.mkdir(parents=True)
            (sub / "PANO_0001.JPG").write_bytes(b"fake")

        sets = scan_panoramas(str(tmp_path))
        folder_names = [Path(s.folder).name for s in sets]
        assert folder_names == ["001", "002", "003"]


# ─── ClassificationResult panorama fields ──────────────────────────────────

class TestClassificationResultPanorama:
    def test_default_panorama_fields(self):
        result = ClassificationResult(source_dir="/test")
        assert result.panorama_count == 0
        assert result.panorama_sets == []
        assert result.panorama_dir == ""

    def test_panorama_in_manifest(self, tmp_path):
        from photo_classifier import write_manifest, PhotoMeta

        ps = PanoramaSet(folder="/test/PANORAMA/001", photo_count=25,
                         stitched_path="/test/panorama/001_panorama.jpg")
        result = ClassificationResult(
            source_dir=str(tmp_path),
            total=5, nadir_count=3, oblique_count=2,
            panorama_count=1, panorama_sets=[ps],
            panorama_dir="/test/panorama",
            created_at="2026-03-17T00:00:00Z",
            photos=[PhotoMeta(filename="a.jpg", path="/a.jpg", classification="nadir")],
        )

        path = write_manifest(result, tmp_path / "manifest.json")
        with open(path) as f:
            data = json.load(f)

        assert data["summary"]["panoramas"] == 1
        assert data["output_dirs"]["panorama"] == "/test/panorama"
        assert len(data["panoramas"]) == 1
        assert data["panoramas"][0]["photo_count"] == 25
        assert data["panoramas"][0]["stitched_path"] == "/test/panorama/001_panorama.jpg"


# ─── MipMap metadata extraction ────────────────────────────────────────────

class TestMipMapMetadata:
    def test_parse_dewarp_data(self):
        from mipmap_service import _parse_dewarp_data

        dewarp = "2025-11-18;3708.41,3708.41,20.36,-41.76,-0.1066,-0.00275,-0.000353,-0.000156,-0.01403"
        result = _parse_dewarp_data(dewarp)
        assert result is not None
        assert len(result) == 9
        assert result[0] == pytest.approx(3708.41)  # fx
        assert result[4] == pytest.approx(-0.1066)  # k1

    def test_parse_dewarp_data_invalid(self):
        from mipmap_service import _parse_dewarp_data

        assert _parse_dewarp_data("") is None
        assert _parse_dewarp_data("no-semicolon") is None
        assert _parse_dewarp_data("date;1,2,3") is None  # too few values

    def test_gimbal_to_orientation_nadir(self):
        from mipmap_service import _gimbal_to_orientation

        # Pitch -90 (nadir), no roll, no yaw
        rot = _gimbal_to_orientation(-90.0, 0.0, 0.0)
        assert len(rot) == 9
        # sentinel_core convention: nadir produces [1,0,0, 0,-1,0, 0,0,-1]
        assert rot[0] == pytest.approx(1.0, abs=1e-10)
        assert rot[4] == pytest.approx(-1.0, abs=1e-10)
        assert rot[8] == pytest.approx(-1.0, abs=1e-10)

    def test_gimbal_to_orientation_horizon(self):
        from mipmap_service import _gimbal_to_orientation

        # Pitch 0 (horizon), no roll, yaw=0
        rot = _gimbal_to_orientation(0.0, 0.0, 0.0)
        # sentinel_core convention: horizon produces [1,0,0, 0,0,-1, 0,1,0]
        assert rot[0] == pytest.approx(1.0, abs=1e-10)
        assert rot[5] == pytest.approx(-1.0, abs=1e-10)
        assert rot[7] == pytest.approx(1.0, abs=1e-10)

    def test_gimbal_to_orientation_returns_9_elements(self):
        from mipmap_service import _gimbal_to_orientation

        rot = _gimbal_to_orientation(-45.0, 2.0, 132.0)
        assert len(rot) == 9
        # All values should be finite
        assert all(math.isfinite(v) for v in rot)

    def test_gimbal_to_orientation_rotation_matrix_unit_rows(self):
        """Rotation matrix rows should have unit length."""
        from mipmap_service import _gimbal_to_orientation

        rot = _gimbal_to_orientation(-45.0, 3.0, 90.0)
        # Row vectors
        r0 = rot[0:3]
        r1 = rot[3:6]
        r2 = rot[6:9]
        # Each row should have unit length
        for row in [r0, r1, r2]:
            length = math.sqrt(sum(v**2 for v in row))
            assert length == pytest.approx(1.0, abs=1e-10)
        # Rows 0-2 and 1-2 should be orthogonal; row 0-1 may have
        # small cross-coupling from roll (sentinel_core gimbal convention)
        dot02 = sum(a * b for a, b in zip(r0, r2))
        dot12 = sum(a * b for a, b in zip(r1, r2))
        assert dot02 == pytest.approx(0.0, abs=1e-6)
        assert dot12 == pytest.approx(0.0, abs=1e-6)


# ─── Settings persistence ─────────────────────────────────────────────────

class TestSettingsPersistence:
    def test_load_defaults_when_no_file(self, tmp_path, monkeypatch):
        import sortie
        monkeypatch.setattr(sortie, "SETTINGS_FILE", tmp_path / "nonexistent.json")
        settings = sortie.load_settings()
        assert settings["source_dir"] == ""
        assert settings["job_type"] == "construction_progress"
        assert settings["threshold"] == "-70"
        assert settings["nodeodm_url"] == "http://localhost:3000"

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        import sortie
        settings_file = tmp_path / "test_settings.json"
        monkeypatch.setattr(sortie, "SETTINGS_FILE", settings_file)

        sortie.save_settings({
            "source_dir": "D:\\Photos\\JobSite1",
            "job_type": "roof_inspection",
            "site_name": "TestSite",
            "threshold": "-75",
            "nodeodm_url": "http://192.168.1.10:3000",
            "window_geometry": "780x800+100+100",
        })

        assert settings_file.exists()
        loaded = sortie.load_settings()
        assert loaded["source_dir"] == "D:\\Photos\\JobSite1"
        assert loaded["job_type"] == "roof_inspection"
        assert loaded["site_name"] == "TestSite"
        assert loaded["threshold"] == "-75"

    def test_load_handles_corrupt_json(self, tmp_path, monkeypatch):
        import sortie
        settings_file = tmp_path / "corrupt.json"
        settings_file.write_text("{invalid json")
        monkeypatch.setattr(sortie, "SETTINGS_FILE", settings_file)

        settings = sortie.load_settings()
        assert settings["source_dir"] == ""  # falls back to defaults

    def test_load_merges_partial_file(self, tmp_path, monkeypatch):
        import sortie
        settings_file = tmp_path / "partial.json"
        settings_file.write_text('{"source_dir": "/photos", "job_type": "vegetation"}')
        monkeypatch.setattr(sortie, "SETTINGS_FILE", settings_file)

        settings = sortie.load_settings()
        assert settings["source_dir"] == "/photos"
        assert settings["job_type"] == "vegetation"
        assert settings["threshold"] == "-70"  # default filled in
