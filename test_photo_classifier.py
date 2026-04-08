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
    classify_with_profile,
    sort_with_profile,
    load_profile,
    list_profiles,
    yaw_to_quadrant,
    compass_to_bearing,
    normalize_angle,
    ProfileResult,
    ProfileCategory,
    _matches_category,
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


# ─── PROFILE SYSTEM TESTS ─────────────────────────────────────────────────


METERS_PER_FOOT = 0.3048


def _profile_photo(name, pitch, yaw=None, alt_ft=None, ext=".jpg"):
    """Helper to create PhotoMeta with altitude in feet (converted to meters)."""
    alt_m = alt_ft * METERS_PER_FOOT if alt_ft is not None else None
    return PhotoMeta(
        filename=f"{name}{ext}",
        path=Path(f"/test/{name}{ext}"),
        pitch=pitch,
        yaw=yaw,
        relative_altitude=alt_m,
        classification=classify_pitch(pitch) if pitch is not None else "unknown",
    )


# ─── compass / direction helpers ───────────────────────────────────────────

class TestNormalizeAngle:
    def test_positive(self):
        assert normalize_angle(90) == 90

    def test_negative(self):
        assert normalize_angle(-90) == 270

    def test_over_360(self):
        assert normalize_angle(450) == 90


class TestCompassToBearing:
    def test_cardinal(self):
        assert compass_to_bearing("N") == 0.0
        assert compass_to_bearing("S") == 180.0
        assert compass_to_bearing("E") == 90.0
        assert compass_to_bearing("W") == 270.0

    def test_intercardinal(self):
        assert compass_to_bearing("NE") == 45.0
        assert compass_to_bearing("SW") == 225.0

    def test_numeric_string(self):
        assert compass_to_bearing("135") == 135.0

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            compass_to_bearing("INVALID")


class TestYawToQuadrant:
    def test_front_facing_south(self):
        # House faces south (180). Camera yaw 0 (pointing north) = shooting from south = front
        assert yaw_to_quadrant(0, 180) == "front"

    def test_back_facing_south(self):
        assert yaw_to_quadrant(180, 180) == "back"

    def test_right_facing_south(self):
        assert yaw_to_quadrant(90, 180) == "right"

    def test_left_facing_south(self):
        assert yaw_to_quadrant(270, 180) == "left"

    def test_none_yaw(self):
        assert yaw_to_quadrant(None, 180) == "unknown"

    def test_front_facing_north(self):
        # House faces north (0). Camera yaw 180 (pointing south) = shooting from north = front
        assert yaw_to_quadrant(180, 0) == "front"


# ─── _matches_category ─────────────────────────────────────────────────────

class TestMatchesCategory:
    def test_pitch_match(self):
        photo = _profile_photo("a", pitch=-90, alt_ft=100)
        cat = {"pitch_min": -95, "pitch_max": -75}
        assert _matches_category(photo, cat) is True

    def test_pitch_miss(self):
        photo = _profile_photo("a", pitch=-50, alt_ft=100)
        cat = {"pitch_min": -95, "pitch_max": -75}
        assert _matches_category(photo, cat) is False

    def test_altitude_match(self):
        photo = _profile_photo("a", pitch=-90, alt_ft=25)
        cat = {"pitch_min": -95, "pitch_max": -75, "alt_min_ft": 15, "alt_max_ft": 35}
        assert _matches_category(photo, cat) is True

    def test_altitude_too_high(self):
        photo = _profile_photo("a", pitch=-90, alt_ft=50)
        cat = {"pitch_min": -95, "pitch_max": -75, "alt_min_ft": 15, "alt_max_ft": 35}
        assert _matches_category(photo, cat) is False

    def test_direction_match(self):
        # House faces south (180), camera yaw 0 = front
        photo = _profile_photo("a", pitch=-45, yaw=0, alt_ft=15)
        cat = {"pitch_min": -70, "pitch_max": 0, "direction": "front"}
        assert _matches_category(photo, cat, front_bearing=180) is True

    def test_direction_miss(self):
        # House faces south (180), camera yaw 0 = front, but category wants "back"
        photo = _profile_photo("a", pitch=-45, yaw=0, alt_ft=15)
        cat = {"pitch_min": -70, "pitch_max": 0, "direction": "back"}
        assert _matches_category(photo, cat, front_bearing=180) is False

    def test_none_pitch_fails_pitch_filter(self):
        photo = _profile_photo("a", pitch=None)
        cat = {"pitch_min": -95, "pitch_max": -75}
        assert _matches_category(photo, cat) is False

    def test_none_altitude_fails_alt_filter(self):
        photo = _profile_photo("a", pitch=-90, alt_ft=None)
        cat = {"pitch_min": -95, "pitch_max": -75, "alt_min_ft": 15, "alt_max_ft": 35}
        assert _matches_category(photo, cat) is False


# ─── classify_with_profile ─────────────────────────────────────────────────

class TestClassifyWithProfile:
    @pytest.fixture
    def bees360_profile(self):
        return load_profile("bees360")

    @pytest.fixture
    def droners_profile(self):
        return load_profile("droners")

    @pytest.fixture
    def zeitview_profile(self):
        return load_profile("zeitview")

    def _make_bees360_photos(self):
        """Minimal photo set that satisfies Bees360 requirements.

        Bees360 categories use first-match-wins, so altitude matters:
        - Birdsview: pitch -55 to -30, alt 15-35ft (no direction filter)
        - Closeups: pitch -70 to 0, alt ≤20ft, WITH direction filter
        Closeups at 10ft avoid birdsview's alt_min_ft=15 gate.
        Birdsview at 25ft stays in its alt range.
        """
        photos = []
        # 1 overhead (pitch -90, alt 25ft)
        photos.append(_profile_photo("overhead_1", pitch=-90, alt_ft=25))
        # 8 birdsview (pitch -45, alt 25ft, various yaws) — above closeup alt range
        for i, yaw in enumerate([0, 45, 90, 135, 180, 225, 270, 315]):
            photos.append(_profile_photo(f"bird_{i}", pitch=-45, yaw=yaw, alt_ft=25))
        # 5 front closeups (pitch -30, alt 10ft — below birdsview alt_min_ft=15)
        for i in range(5):
            photos.append(_profile_photo(f"front_{i}", pitch=-30, yaw=0, alt_ft=10))
        # 5 right closeups (yaw=90)
        for i in range(5):
            photos.append(_profile_photo(f"right_{i}", pitch=-30, yaw=90, alt_ft=10))
        # 5 back closeups (yaw=180)
        for i in range(5):
            photos.append(_profile_photo(f"back_{i}", pitch=-30, yaw=180, alt_ft=10))
        # 5 left closeups (yaw=270)
        for i in range(5):
            photos.append(_profile_photo(f"left_{i}", pitch=-30, yaw=270, alt_ft=10))
        return photos

    def test_bees360_all_met(self, bees360_profile):
        photos = self._make_bees360_photos()
        result = _make_result(photos)
        pr = classify_with_profile(result, bees360_profile, front_bearing=180)
        assert pr.all_met is True
        assert len(pr.validation_errors) == 0
        assert pr.profile_name == "Bees360 Property Inspection"

    def test_bees360_missing_overhead(self, bees360_profile):
        photos = self._make_bees360_photos()
        # Remove the overhead photo
        photos = [p for p in photos if "overhead" not in p.filename]
        result = _make_result(photos)
        pr = classify_with_profile(result, bees360_profile, front_bearing=180)
        assert pr.all_met is False
        assert any("Overhead" in e for e in pr.validation_errors)

    def test_bees360_dng_excluded(self, bees360_profile):
        photos = [
            _profile_photo("overhead_1", pitch=-90, alt_ft=25, ext=".dng"),
        ]
        result = _make_result(photos)
        pr = classify_with_profile(result, bees360_profile, front_bearing=180)
        assert pr.total == 0  # DNG excluded

    def test_droners_happy_path(self, droners_profile):
        photos = []
        # 4 overhead
        for i in range(4):
            photos.append(_profile_photo(f"over_{i}", pitch=-90))
        # 8 oblique
        for i in range(8):
            photos.append(_profile_photo(f"obl_{i}", pitch=-45))
        # 4 detail
        for i in range(4):
            photos.append(_profile_photo(f"det_{i}", pitch=-10))
        result = _make_result(photos)
        pr = classify_with_profile(result, droners_profile)
        assert pr.all_met is True
        assert len(pr.categories) == 3

    def test_droners_insufficient_overhead(self, droners_profile):
        photos = [
            _profile_photo("over_1", pitch=-90),
            # only 1 overhead, need 4
        ]
        result = _make_result(photos)
        pr = classify_with_profile(result, droners_profile)
        assert pr.all_met is False

    def test_zeitview_categories_count(self, zeitview_profile):
        assert len(zeitview_profile["categories"]) == 7

    def test_zeitview_nadir_classification(self, zeitview_profile):
        photos = [
            _profile_photo("nadir_1", pitch=-90, alt_ft=120),
            _profile_photo("nadir_2", pitch=-85, alt_ft=100),
        ]
        result = _make_result(photos)
        pr = classify_with_profile(result, zeitview_profile, front_bearing=180)
        nadir_cat = pr.categories[0]
        assert nadir_cat.name == "nadir"
        assert len(nadir_cat.photos) == 2

    def test_front_bearing_as_string(self, bees360_profile):
        photos = self._make_bees360_photos()
        result = _make_result(photos)
        pr = classify_with_profile(result, bees360_profile, front_bearing="S")
        assert pr.all_met is True

    def test_unmatched_photos(self, droners_profile):
        # Photo with pitch 20 doesn't match any Droners category (max is 10)
        photos = [_profile_photo("weird", pitch=20)]
        result = _make_result(photos)
        pr = classify_with_profile(result, droners_profile)
        assert len(pr.unmatched) == 1

    def test_first_match_wins(self, droners_profile):
        # pitch=-80 matches overhead (-95 to -75) — should go to first category
        photos = [_profile_photo("edge", pitch=-80)]
        result = _make_result(photos)
        pr = classify_with_profile(result, droners_profile)
        assert pr.categories[0].name == "overhead"
        assert len(pr.categories[0].photos) == 1


# ─── sort_with_profile ─────────────────────────────────────────────────────

class TestSortWithProfile:
    def test_creates_category_dirs(self, tmp_path):
        profile = load_profile("droners")
        photos = []
        for i in range(4):
            photos.append(_profile_photo(f"over_{i}", pitch=-90))
        for i in range(8):
            photos.append(_profile_photo(f"obl_{i}", pitch=-45))
        for i in range(4):
            photos.append(_profile_photo(f"det_{i}", pitch=-10))

        # Write fake photo files
        src = tmp_path / "source"
        src.mkdir()
        for p in photos:
            (src / p.filename).write_bytes(b"fake_photo_data")
            p.path = src / p.filename

        result = _make_result(photos)
        out = tmp_path / "output"
        pr = sort_with_profile(result, profile, str(out), site_name="TestSite")

        assert (out / "overhead").is_dir()
        assert (out / "oblique").is_dir()
        assert (out / "detail").is_dir()
        assert pr.all_met is True

    def test_rename_pattern(self, tmp_path):
        profile = load_profile("droners")
        photos = [_profile_photo("over_1", pitch=-90)]

        src = tmp_path / "source"
        src.mkdir()
        (src / photos[0].filename).write_bytes(b"fake")
        photos[0].path = src / photos[0].filename

        result = _make_result(photos)
        out = tmp_path / "output"
        sort_with_profile(result, profile, str(out), site_name="123 Main St")

        expected = out / "overhead" / "123_Main_St_overhead_001.jpg"
        assert expected.exists()

    def test_unmatched_goes_to_unmatched_dir(self, tmp_path):
        profile = load_profile("droners")
        photos = [_profile_photo("weird", pitch=20)]

        src = tmp_path / "source"
        src.mkdir()
        (src / photos[0].filename).write_bytes(b"fake")
        photos[0].path = src / photos[0].filename

        result = _make_result(photos)
        out = tmp_path / "output"
        sort_with_profile(result, profile, str(out), site_name="Test")

        assert (out / "_unmatched").is_dir()
        assert (out / "_unmatched" / "weird.jpg").exists()


# ─── load_profile / list_profiles ──────────────────────────────────────────

class TestProfileLoading:
    def test_load_bees360(self):
        p = load_profile("bees360")
        assert p["company"] == "Bees360"
        assert len(p["categories"]) == 6

    def test_load_droners(self):
        p = load_profile("droners")
        assert p["company"] == "Droners.io"
        assert len(p["categories"]) == 3

    def test_load_zeitview(self):
        p = load_profile("zeitview")
        assert p["company"] == "Zeitview"
        assert len(p["categories"]) == 7

    def test_load_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            load_profile("nonexistent_client")

    def test_list_profiles_returns_all(self):
        profiles = list_profiles()
        names = [stem for stem, _ in profiles]
        assert "bees360" in names
        assert "droners" in names
        assert "zeitview" in names
