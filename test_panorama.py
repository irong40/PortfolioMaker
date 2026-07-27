"""Tests for panorama detection, stitching, and settings persistence."""

import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from photo_classifier import (
    PanoramaSet,
    ClassificationResult,
    cluster_panorama_photos,
    find_prestitched_panoramas,
    gallery_prefix,
    generate_panorama_viewer,
    match_prestitched_panoramas,
    scan_panoramas,
    scan_panorama_details,
    stitch_panoramas,
    write_panorama_launcher,
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
        assert ps.latitude is None
        assert ps.longitude is None
        assert ps.status == "pending"

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
        for i in range(8):
            (set1 / f"PANO_{i:04d}.JPG").write_bytes(b"fake")

        sets = scan_panoramas(str(tmp_path))
        assert len(sets) == 1
        assert sets[0].photo_count == 8
        assert str(set1) == sets[0].folder

    def test_multiple_sets(self, tmp_path):
        pano_dir = tmp_path / "PANORAMA"
        for name in ["001_0001", "002_0002", "003_0003"]:
            sub = pano_dir / name
            sub.mkdir(parents=True)
            for i in range(8):
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
        for i in range(8):
            (sub / f"PANO_{i:04d}.JPG").write_bytes(b"fake")
        (sub / "PANO_0002.DNG").write_bytes(b"fake")  # not jpg
        (sub / "readme.txt").write_bytes(b"fake")

        sets = scan_panoramas(str(tmp_path))
        assert sets[0].photo_count == 8  # only JPG

    def test_detects_from_parent_directory(self, tmp_path):
        """When scanning a DJI_xxx subfolder, should find PANORAMA at parent level."""
        pano_dir = tmp_path / "PANORAMA" / "001_0001"
        pano_dir.mkdir(parents=True)
        for i in range(8):
            (pano_dir / f"PANO_{i:04d}.JPG").write_bytes(b"fake")

        photo_dir = tmp_path / "DJI_001"
        photo_dir.mkdir()

        sets = scan_panoramas(str(photo_dir))
        assert len(sets) == 1
        assert sets[0].photo_count == 8

    def test_sorted_by_folder_name(self, tmp_path):
        pano_dir = tmp_path / "PANORAMA"
        for name in ["003", "001", "002"]:
            sub = pano_dir / name
            sub.mkdir(parents=True)
            for i in range(8):
                (sub / f"PANO_{i:04d}.JPG").write_bytes(b"fake")

        sets = scan_panoramas(str(tmp_path))
        folder_names = [Path(s.folder).name for s in sets]
        assert folder_names == ["001", "002", "003"]

    def test_folder_group_below_eight_is_a_straggler(self, tmp_path):
        sub = tmp_path / "PANORAMA" / "001"
        sub.mkdir(parents=True)
        for i in range(7):
            (sub / f"PANO_{i:04d}.JPG").write_bytes(b"fake")

        sets, stragglers = scan_panorama_details(str(tmp_path))

        assert sets == []
        assert len(stragglers) == 1
        assert stragglers[0].photo_count == 7
        assert stragglers[0].status == "skipped_straggler"


class TestClusterPanoramaPhotos:
    @staticmethod
    def _located(prefix, latitude, longitude, count=8):
        return [
            (f"{prefix}_{i:02d}.jpg", latitude, longitude + i * 0.000001)
            for i in range(count)
        ]

    def test_clusters_one_set_within_five_metres(self):
        clusters = cluster_panorama_photos(
            self._located("set1", 36.8500, -76.2900))

        assert len(clusters) == 1
        assert len(clusters[0]) == 8

    def test_clusters_two_positions_more_than_five_metres_apart(self):
        photos = (
            self._located("set1", 36.8500, -76.2900)
            + self._located("set2", 36.8501, -76.2900)
        )

        clusters = cluster_panorama_photos(photos)

        assert [len(cluster) for cluster in clusters] == [8, 8]

    def test_separates_sub_eight_stragglers(self, tmp_path, monkeypatch):
        sub = tmp_path / "PANORAMA" / "flight"
        sub.mkdir(parents=True)
        gps = {}
        for path, lat, lon in (
            self._located("set1", 36.8500, -76.2900)
            + self._located("stray", 36.8501, -76.2900, count=3)
        ):
            photo = sub / Path(path).name
            photo.write_bytes(b"fake")
            gps[str(photo)] = [lon, lat, 10.0]
        monkeypatch.setattr(
            "photo_classifier.get_gps_data", lambda path: gps.get(path))

        sets, stragglers = scan_panorama_details(str(tmp_path))

        assert len(sets) == 1
        assert sets[0].photo_count == 8
        assert len(stragglers) == 1
        assert stragglers[0].photo_count == 3

    def test_cluster_order_is_deterministic(self):
        first = self._located("z", 36.8501, -76.2900)
        second = self._located("a", 36.8500, -76.2900)

        clusters = cluster_panorama_photos(list(reversed(first + second)))

        assert clusters[0][0][0].startswith("a_")
        assert clusters[1][0][0].startswith("z_")

    def test_two_folders_at_one_launch_point_stay_separate(
            self, tmp_path, monkeypatch):
        """Two panoramas shot from the same spot must not merge into one set.

        Clustering pooled across folders collapsed them (they sit ~1 m apart,
        well inside the 5 m radius), producing an unstitchable 16-photo set.
        """
        gps = {}
        for folder, latitude in (("001", 36.8500000), ("002", 36.8500100)):
            sub = tmp_path / "PANORAMA" / folder
            sub.mkdir(parents=True)
            for path, lat, lon in self._located(folder, latitude, -76.2900):
                photo = sub / Path(path).name
                photo.write_bytes(b"fake")
                gps[str(photo)] = [lon, lat, 10.0]
        monkeypatch.setattr(
            "photo_classifier.get_gps_data", lambda path: gps.get(path))

        sets, stragglers = scan_panorama_details(str(tmp_path))

        assert stragglers == []
        assert [s.photo_count for s in sets] == [8, 8]
        assert sorted(Path(s.folder).name for s in sets) == ["001", "002"]

    def test_folder_splits_when_it_holds_two_distinct_positions(
            self, tmp_path, monkeypatch):
        sub = tmp_path / "PANORAMA" / "flight"
        sub.mkdir(parents=True)
        gps = {}
        for path, lat, lon in (
            self._located("near", 36.8500, -76.2900)
            + self._located("far", 36.8510, -76.2900)
        ):
            photo = sub / Path(path).name
            photo.write_bytes(b"fake")
            gps[str(photo)] = [lon, lat, 10.0]
        monkeypatch.setattr(
            "photo_classifier.get_gps_data", lambda path: gps.get(path))

        sets, _ = scan_panorama_details(str(tmp_path))

        assert [s.photo_count for s in sets] == [8, 8]

    def test_photos_without_gps_join_their_folders_set(
            self, tmp_path, monkeypatch):
        """A mixed-GPS folder is one capture, not two sets."""
        sub = tmp_path / "PANORAMA" / "001"
        sub.mkdir(parents=True)
        gps = {}
        for path, lat, lon in self._located("set1", 36.8500, -76.2900):
            photo = sub / Path(path).name
            photo.write_bytes(b"fake")
            gps[str(photo)] = [lon, lat, 10.0]
        for i in range(9):  # enough to clear min_photos on its own
            (sub / f"nogps_{i:02d}.jpg").write_bytes(b"fake")
        monkeypatch.setattr(
            "photo_classifier.get_gps_data", lambda path: gps.get(path))

        sets, stragglers = scan_panorama_details(str(tmp_path))

        assert stragglers == []
        assert len(sets) == 1
        assert sets[0].photo_count == 17
        assert sets[0].latitude is not None


class TestGalleryNaming:
    def test_prefix_uses_job_folder_name(self):
        assert gallery_prefix(
            Path(r"E:\Portfolio\Hemp Haven_panorama_2026-07-27")
        ) == "Hemp-Haven-panorama-2026-07-27"

    def test_prefix_climbs_out_of_nested_panoramas_dir(self):
        assert gallery_prefix(
            Path(r"E:\Portfolio\Hemp Haven_mapping_2026-07-27\panoramas")
        ) == "Hemp-Haven-mapping-2026-07-27"

    def test_two_jobs_do_not_overwrite_each_other_in_the_gallery(
            self, tmp_path, monkeypatch):
        """set-001.jpg from job B must not clobber job A's gallery copy."""
        gallery = tmp_path / "gallery"
        monkeypatch.setenv("PANO_GALLERY", str(gallery))
        monkeypatch.setattr(
            "photo_classifier._stitch_one_set",
            lambda ps, output_path: (
                Image.new("RGB", (32, 16)).save(output_path, "JPEG")
                or {"ok": True, "width": 32, "height": 16}))

        for job in ("siteA_panorama_2026-07-27", "siteB_panorama_2026-07-27"):
            stitch_panoramas([PanoramaSet(folder="001", photo_count=8)],
                             str(tmp_path / job))

        assert sorted(p.name for p in gallery.iterdir()) == [
            "siteA-panorama-2026-07-27_set-001.jpg",
            "siteB-panorama-2026-07-27_set-001.jpg",
        ]


class TestPrestitchedPanoramas:
    @staticmethod
    def _jpeg(path, size):
        Image.new("RGB", size, color=(40, 80, 120)).save(path, "JPEG")

    def test_detects_wide_dji_prestitched_panorama(self, tmp_path):
        candidate = tmp_path / "DJI_20260727_PANO0001.JPG"
        self._jpeg(candidate, (200, 100))

        detected = find_prestitched_panoramas(str(tmp_path))

        assert detected == [str(candidate)]

    def test_rejects_normal_four_by_three_pano_source_frame(self, tmp_path):
        candidate = tmp_path / "DJI_20260727_PANO0001.JPG"
        self._jpeg(candidate, (120, 90))

        assert find_prestitched_panoramas(str(tmp_path)) == []

    def test_matches_candidates_to_nearest_gps_set(self, tmp_path, monkeypatch):
        first = tmp_path / "DJI_PANO0001.JPG"
        second = tmp_path / "DJI_PANO0002.JPG"
        self._jpeg(first, (200, 100))
        self._jpeg(second, (200, 100))
        gps = {
            str(first): [-76.2900, 36.8500, 10.0],
            str(second): [-76.2900, 36.8501, 10.0],
        }
        monkeypatch.setattr(
            "photo_classifier.get_gps_data", lambda path: gps.get(path))
        sets = [
            PanoramaSet("set1", 8, latitude=36.8500, longitude=-76.2900),
            PanoramaSet("set2", 8, latitude=36.8501, longitude=-76.2900),
        ]

        match_prestitched_panoramas(sets, [str(second), str(first)])

        assert sets[0].prestitched_path == str(first)
        assert sets[1].prestitched_path == str(second)

    def test_prestitched_copy_skips_opencv_worker(self, tmp_path, monkeypatch):
        candidate = tmp_path / "DJI_PANO0001.JPG"
        self._jpeg(candidate, (200, 100))
        output = tmp_path / "output"
        gallery = tmp_path / "gallery"
        monkeypatch.setenv("PANO_GALLERY", str(gallery))
        panorama_set = PanoramaSet(
            "set1", 8, prestitched_path=str(candidate))

        with patch("photo_classifier._stitch_one_set") as worker:
            stitch_panoramas([panorama_set], str(output))

        worker.assert_not_called()
        assert Path(panorama_set.stitched_path).name == "set-001.jpg"
        assert Path(panorama_set.stitched_path).exists()
        assert panorama_set.status == "dji_prestitched"
        assert panorama_set.source_type == "dji_prestitched"

    def test_copy_failure_falls_back_to_opencv_worker(self, tmp_path, monkeypatch):
        output = tmp_path / "output"
        monkeypatch.setenv("PANO_GALLERY", str(tmp_path / "gallery"))
        panorama_set = PanoramaSet(
            "set1", 8, prestitched_path=str(tmp_path / "missing.jpg"))

        def successful_worker(_set, output_path):
            Path(output_path).write_bytes(b"stitched")
            return {"ok": True, "width": 200, "height": 100}

        with patch("photo_classifier._stitch_one_set", side_effect=successful_worker) as worker:
            stitch_panoramas([panorama_set], str(output))

        worker.assert_called_once()
        assert panorama_set.status == "opencv_stitched"
        assert panorama_set.source_type == "opencv_stitched"

    def test_failed_set_does_not_block_later_prestitched_set(self, tmp_path, monkeypatch):
        output = tmp_path / "output"
        monkeypatch.setenv("PANO_GALLERY", str(tmp_path / "gallery"))
        prestitched = tmp_path / "DJI_PANO0002.JPG"
        self._jpeg(prestitched, (200, 100))
        sets = [
            PanoramaSet("set1", 8),
            PanoramaSet("set2", 8, prestitched_path=str(prestitched)),
        ]

        with patch("photo_classifier._stitch_one_set", return_value={
            "ok": False, "error": "OpenCV not installed",
        }):
            stitch_panoramas(sets, str(output))

        assert sets[0].status == "failed"
        assert sets[1].status == "dji_prestitched"
        assert Path(sets[1].stitched_path).name == "set-002.jpg"


class TestPanoramaViewer:
    def test_viewer_references_local_image_and_assets(self, tmp_path):
        panorama = tmp_path / "set-001.jpg"
        panorama.write_bytes(b"jpeg")

        viewer = Path(generate_panorama_viewer(
            panorama, title="Test Site Panorama 1"))
        html = viewer.read_text(encoding="utf-8")

        assert viewer.name == "set-001.html"
        assert 'assets/pannellum.css' in html
        assert 'assets/pannellum.js' in html
        assert '"panorama": "set-001.jpg"' in html
        assert (tmp_path / "assets" / "pannellum.js").exists()
        assert (tmp_path / "assets" / "pannellum.css").exists()
        assert (tmp_path / "assets" / "LICENSE.txt").exists()

    def test_viewer_contains_no_remote_url(self, tmp_path):
        panorama = tmp_path / "set-001.jpg"
        panorama.write_bytes(b"jpeg")

        html = Path(generate_panorama_viewer(panorama)).read_text(encoding="utf-8")

        assert "http://" not in html
        assert "https://" not in html
        assert "cdn" not in html.lower()

    def test_viewer_safely_serializes_title(self, tmp_path):
        panorama = tmp_path / "set-001.jpg"
        panorama.write_bytes(b"jpeg")

        html = Path(generate_panorama_viewer(
            panorama, title='Site "A" </script>')).read_text(encoding="utf-8")

        assert 'Site \\"A\\"' in html
        assert 'Site \\"A\\" </script>' not in html
        assert r"\u003c/script\u003e" in html

    def test_launcher_serves_first_viewer_on_loopback(self, tmp_path):
        viewer = tmp_path / "set-001.html"
        viewer.write_text("viewer", encoding="utf-8")

        launcher = Path(write_panorama_launcher(tmp_path, viewer.name))
        content = launcher.read_text(encoding="utf-8")

        assert "python -m http.server 8765 --bind 127.0.0.1" in content
        assert "http://127.0.0.1:8765/set-001.html" in content

    def test_stitch_generates_viewer_for_successful_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PANO_GALLERY", str(tmp_path / "gallery"))
        panorama_set = PanoramaSet("set1", 8)

        def successful_worker(_set, output_path):
            Path(output_path).write_bytes(b"stitched")
            return {"ok": True, "width": 200, "height": 100}

        with patch("photo_classifier._stitch_one_set", side_effect=successful_worker):
            stitch_panoramas([panorama_set], str(tmp_path / "output"),
                             site_name="Test Site")

        assert Path(panorama_set.viewer_path).name == "set-001.html"
        assert Path(panorama_set.viewer_path).exists()
        assert (tmp_path / "output" / "view_panoramas.bat").exists()


class TestPanoramaGuiContract:
    def test_job_type_is_exposed_and_local_engine_skips_nodeodm(self):
        import sortie

        assert ("panorama", "Panorama / 360") in sortie.JOB_TYPES
        assert sortie.engine_requires_nodeodm("local") is False
        assert sortie.engine_requires_nodeodm("mipmap") is False
        assert sortie.engine_requires_nodeodm("opensplat") is True
        assert sortie.engine_requires_nodeodm("nodeodm") is True


# ─── ClassificationResult panorama fields ──────────────────────────────────

class TestClassificationResultPanorama:
    def test_default_panorama_fields(self):
        result = ClassificationResult(source_dir="/test")
        assert result.panorama_count == 0
        assert result.panorama_sets == []
        assert result.panorama_dir == ""

    def test_panorama_in_manifest(self, tmp_path):
        from photo_classifier import write_manifest, PhotoMeta

        ps = PanoramaSet(
            folder="/test/PANORAMA/001",
            photo_count=25,
            latitude=36.8501,
            longitude=-76.2902,
            prestitched_path="/test/DJI_PANO0001.JPG",
            stitched_path="/test/panorama/set-001.jpg",
            viewer_path="/test/panorama/set-001.html",
            status="dji_prestitched",
            source_type="dji_prestitched",
        )
        straggler = PanoramaSet(
            folder="gps-cluster-002",
            photo_count=4,
            latitude=36.8503,
            longitude=-76.2904,
            status="skipped_straggler",
        )
        result = ClassificationResult(
            source_dir=str(tmp_path),
            total=5, nadir_count=3, oblique_count=2,
            panorama_count=1, panorama_sets=[ps],
            panorama_stragglers=[straggler],
            panorama_dir="/test/panorama",
            created_at="2026-03-17T00:00:00Z",
            photos=[PhotoMeta(filename="a.jpg", path="/a.jpg", classification="nadir")],
        )

        path = write_manifest(result, tmp_path / "manifest.json")
        with open(path) as f:
            data = json.load(f)

        assert data["summary"]["panoramas"] == 1
        assert data["summary"]["panorama_straggler_photos"] == 4
        assert data["output_dirs"]["panorama"] == "/test/panorama"
        assert len(data["panoramas"]) == 1
        assert data["panoramas"][0]["photo_count"] == 25
        assert data["panoramas"][0]["latitude"] == 36.8501
        assert data["panoramas"][0]["longitude"] == -76.2902
        assert data["panoramas"][0]["prestitched_path"] == "/test/DJI_PANO0001.JPG"
        assert data["panoramas"][0]["stitched_path"] == "/test/panorama/set-001.jpg"
        assert data["panoramas"][0]["viewer_path"] == "/test/panorama/set-001.html"
        assert data["panoramas"][0]["status"] == "dji_prestitched"
        assert data["panoramas"][0]["source_type"] == "dji_prestitched"
        assert data["panorama_stragglers"] == [{
            "folder": "gps-cluster-002",
            "photo_count": 4,
            "latitude": 36.8503,
            "longitude": -76.2904,
            "status": "skipped_straggler",
        }]


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
