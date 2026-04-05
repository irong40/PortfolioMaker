"""Tests for photo_classifier core logic."""

import json
from pathlib import Path

import pytest

from photo_classifier import (
    classify_pitch,
    filter_photos,
    scan_photos,
    write_manifest,
    _resolve_collision,
    PhotoMeta,
    ClassificationResult,
    TransferStats,
    PHOTO_EXTENSIONS,
)


# ─── classify_pitch ─────────────────────────────────────────────────────────

class TestClassifyPitch:
    def test_nadir_at_90(self):
        assert classify_pitch(-90.0) == "nadir"

    def test_nadir_at_threshold(self):
        assert classify_pitch(-70.0) == "nadir"

    def test_nadir_at_85(self):
        assert classify_pitch(-85.0) == "nadir"

    def test_oblique_at_69(self):
        assert classify_pitch(-69.0) == "oblique"

    def test_oblique_at_45(self):
        assert classify_pitch(-45.0) == "oblique"

    def test_oblique_at_0(self):
        assert classify_pitch(0.0) == "oblique"

    def test_unknown_when_none(self):
        assert classify_pitch(None) == "unknown"

    def test_custom_threshold(self):
        assert classify_pitch(-75.0, threshold=-80.0) == "oblique"
        assert classify_pitch(-85.0, threshold=-80.0) == "nadir"

    def test_boundary_below_minus_95(self):
        assert classify_pitch(-96.0) == "oblique"

    def test_exactly_minus_95(self):
        assert classify_pitch(-95.0) == "nadir"

    def test_positive_pitch(self):
        assert classify_pitch(10.0) == "oblique"


# ─── filter_photos ──────────────────────────────────────────────────────────

def _make_result(photos):
    nadir = sum(1 for p in photos if p.classification == "nadir")
    oblique = sum(1 for p in photos if p.classification == "oblique")
    unknown = sum(1 for p in photos if p.classification == "unknown")
    return ClassificationResult(
        source_dir="/test",
        nadir_count=nadir,
        oblique_count=oblique,
        unknown_count=unknown,
        total=len(photos),
        photos=photos,
        threshold=-70.0,
    )


def _photo(name, pitch, lat, lon, classification=None):
    if classification is None:
        classification = classify_pitch(pitch)
    return PhotoMeta(
        filename=name, path=f"/test/{name}",
        pitch=pitch, latitude=lat, longitude=lon,
        classification=classification,
    )


class TestFilterPhotos:
    @pytest.fixture
    def sample_result(self):
        photos = [
            _photo("nadir_nw.jpg", -90.0, 36.828, -76.418),
            _photo("nadir_ne.jpg", -90.0, 36.828, -76.413),
            _photo("nadir_sw.jpg", -90.0, 36.825, -76.418),
            _photo("nadir_se.jpg", -90.0, 36.825, -76.413),
            _photo("oblique_nw.jpg", -55.0, 36.828, -76.418),
            _photo("oblique_ne.jpg", -55.0, 36.828, -76.413),
        ]
        return _make_result(photos)

    def test_filter_nadir_only(self, sample_result):
        r = filter_photos(sample_result, classification="nadir")
        assert r.total == 4
        assert r.nadir_count == 4
        assert r.oblique_count == 0

    def test_filter_oblique_only(self, sample_result):
        r = filter_photos(sample_result, classification="oblique")
        assert r.total == 2
        assert r.oblique_count == 2

    def test_filter_bbox_north_half(self, sample_result):
        bbox = (36.827, 36.829, -76.419, -76.412)
        r = filter_photos(sample_result, bbox=bbox)
        assert r.total == 4  # 2 nadir + 2 oblique in north
        assert all(p.latitude >= 36.827 for p in r.photos)

    def test_filter_bbox_plus_nadir(self, sample_result):
        bbox = (36.827, 36.829, -76.419, -76.412)
        r = filter_photos(sample_result, bbox=bbox, classification="nadir")
        assert r.total == 2
        assert all(p.classification == "nadir" for p in r.photos)

    def test_filter_no_match(self, sample_result):
        bbox = (37.0, 37.1, -76.0, -75.9)  # nowhere near the photos
        r = filter_photos(sample_result, bbox=bbox)
        assert r.total == 0

    def test_filter_none_returns_all(self, sample_result):
        r = filter_photos(sample_result, bbox=None, classification=None)
        assert r.total == 6

    def test_filter_skips_no_gps(self):
        photos = [
            _photo("has_gps.jpg", -90.0, 36.828, -76.418),
            PhotoMeta(filename="no_gps.jpg", path="/test/no_gps.jpg",
                      pitch=-90.0, classification="nadir"),
        ]
        result = _make_result(photos)
        bbox = (36.0, 37.0, -77.0, -76.0)
        r = filter_photos(result, bbox=bbox)
        assert r.total == 1
        assert r.photos[0].filename == "has_gps.jpg"


# ─── ClassificationResult.gps_bounds ────────────────────────────────────────

class TestGpsBounds:
    def test_gps_bounds(self):
        photos = [
            _photo("a.jpg", -90.0, 36.82, -76.42),
            _photo("b.jpg", -90.0, 36.83, -76.41),
        ]
        r = _make_result(photos)
        bounds = r.gps_bounds
        assert bounds == (36.82, 36.83, -76.42, -76.41)

    def test_gps_bounds_no_gps(self):
        photos = [PhotoMeta(filename="x.jpg", path="/x.jpg")]
        r = _make_result(photos)
        assert r.gps_bounds is None


# ─── scan_photos ────────────────────────────────────────────────────────────

class TestScanPhotos:
    def test_finds_jpg_files(self, tmp_path):
        (tmp_path / "DJI_0001.JPG").write_bytes(b"fake")
        (tmp_path / "DJI_0002.jpg").write_bytes(b"fake")
        (tmp_path / "DJI_0003.dng").write_bytes(b"fake")
        (tmp_path / "DJI_0004.MP4").write_bytes(b"fake")  # not a photo
        (tmp_path / "readme.txt").write_bytes(b"fake")

        result = scan_photos(str(tmp_path))
        names = [p.name for p in result]
        assert len(result) == 3
        assert "DJI_0001.JPG" in names
        assert "DJI_0002.jpg" in names
        assert "DJI_0003.dng" in names

    def test_skips_subdirectories(self, tmp_path):
        sub = tmp_path / "nadir"
        sub.mkdir()
        (tmp_path / "DJI_0001.JPG").write_bytes(b"fake")
        (sub / "DJI_0002.JPG").write_bytes(b"fake")

        result = scan_photos(str(tmp_path))
        assert len(result) == 1

    def test_empty_folder(self, tmp_path):
        result = scan_photos(str(tmp_path))
        assert len(result) == 0


# ─── write_manifest ─────────────────────────────────────────────────────────

class TestWriteManifest:
    def test_writes_valid_json(self, tmp_path):
        photos = [_photo("a.jpg", -90.0, 36.82, -76.42)]
        result = _make_result(photos)
        result.source_dir = str(tmp_path)
        result.created_at = "2026-03-16T00:00:00Z"

        path = write_manifest(result, tmp_path / "manifest.json")
        assert Path(path).exists()

        with open(path) as f:
            data = json.load(f)

        assert data["summary"]["total"] == 1
        assert data["summary"]["nadir"] == 1
        assert data["photos"][0]["filename"] == "a.jpg"
        assert data["photos"][0]["pitch"] == -90.0
        assert data["sortie_version"] == "1.0"

    def test_includes_gps_bounds(self, tmp_path):
        photos = [
            _photo("a.jpg", -90.0, 36.82, -76.42),
            _photo("b.jpg", -55.0, 36.83, -76.41),
        ]
        result = _make_result(photos)
        result.source_dir = str(tmp_path)
        result.created_at = "2026-03-16T00:00:00Z"

        path = write_manifest(result, tmp_path / "manifest.json")
        with open(path) as f:
            data = json.load(f)

        assert data["gps_bounds"]["min_lat"] == 36.82
        assert data["gps_bounds"]["max_lon"] == -76.41


# ─── bbox validation edge cases ─────────────────────────────────────────────

class TestBboxEdgeCases:
    def test_inverted_bbox_returns_zero(self):
        """min_lat > max_lat should match nothing."""
        photos = [_photo("a.jpg", -90.0, 36.82, -76.42)]
        result = _make_result(photos)
        bbox = (37.0, 36.0, -77.0, -76.0)  # inverted lat
        r = filter_photos(result, bbox=bbox)
        assert r.total == 0


# ─── collision resolution ──────────────────────────────────────────────────

class TestResolveCollision:
    def test_no_collision(self, tmp_path):
        dest = tmp_path / "photo.jpg"
        resolved, renamed = _resolve_collision(dest)
        assert resolved == dest
        assert renamed is False

    def test_single_collision(self, tmp_path):
        dest = tmp_path / "photo.jpg"
        dest.write_bytes(b"existing")
        resolved, renamed = _resolve_collision(dest)
        assert resolved == tmp_path / "photo_1.jpg"
        assert renamed is True

    def test_multiple_collisions(self, tmp_path):
        dest = tmp_path / "photo.jpg"
        dest.write_bytes(b"existing")
        (tmp_path / "photo_1.jpg").write_bytes(b"existing")
        (tmp_path / "photo_2.jpg").write_bytes(b"existing")
        resolved, renamed = _resolve_collision(dest)
        assert resolved == tmp_path / "photo_3.jpg"
        assert renamed is True


# ─── transfer stats ────────────────────────────────────────────────────────

class TestTransferStats:
    def test_defaults(self):
        stats = TransferStats()
        assert stats.transferred == 0
        assert stats.skipped == 0
        assert stats.failed == 0
        assert stats.renamed == 0
        assert stats.total_attempted == 0

    def test_total_attempted(self):
        stats = TransferStats(transferred=5, skipped=1, failed=2)
        assert stats.total_attempted == 8


# ─── scan_photos output dir exclusion ──────────────────────────────────────

class TestScanPhotosOutputExclusion:
    def test_skips_all_output_dirs(self, tmp_path):
        """scan_photos skips nadir/, oblique/, unknown/, panorama/ dirs."""
        for dirname in ("nadir", "oblique", "unknown", "panorama"):
            sub = tmp_path / dirname
            sub.mkdir()
            (sub / "DJI_0001.JPG").write_bytes(b"fake")

        (tmp_path / "DJI_root.JPG").write_bytes(b"fake")
        result = scan_photos(str(tmp_path))
        assert len(result) == 1
        assert result[0].name == "DJI_root.JPG"

    def test_recurses_into_non_output_dirs(self, tmp_path):
        """scan_photos still recurses into normal subdirectories."""
        sub = tmp_path / "flight_001"
        sub.mkdir()
        (tmp_path / "DJI_root.JPG").write_bytes(b"fake")
        (sub / "DJI_sub.JPG").write_bytes(b"fake")
        result = scan_photos(str(tmp_path))
        assert len(result) == 2
