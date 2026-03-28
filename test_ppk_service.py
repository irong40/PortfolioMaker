"""
Tests for ppk_service.py — PPK Post-Processing Service
"""

import os
import json
import tempfile
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from ppk_service import (
    detect_rinex,
    find_nearest_cors,
    parse_mrk_file,
    parse_rtklib_pos,
    match_solutions_to_photos,
    _haversine_km,
    _extract_approx_position_from_obs,
    _extract_flight_date_from_obs,
    RinexFiles,
    PPKSolution,
    CORSStation,
)


# ── Fixtures ──

@pytest.fixture
def tmp_flight_dir(tmp_path):
    """Create a fake DJI flight folder with RINEX files."""
    # Minimal RINEX OBS header
    obs_content = """     3.03           OBSERVATION DATA    M                   RINEX VERSION / TYPE
DJI RINEX                                                   PGM / RUN BY / DATE
                                                            MARKER NAME
  -1270881.6375  -5326294.1498   3565567.3457                APPROX POSITION XYZ
         0.0000         0.0000         0.0000                ANTENNA: DELTA H/E/N
  2026    03    15    14    30    00.0000000     GPS         TIME OF FIRST OBS
                                                            END OF HEADER
"""
    (tmp_path / "Rinex.obs").write_text(obs_content)

    # Minimal NAV file
    (tmp_path / "Rinex.nav").write_text("     3.03           NAVIGATION DATA     M                   RINEX VERSION / TYPE\n                                                            END OF HEADER\n")

    # MRK file (3 photos)
    mrk_content = """1  2355  234600.000  36.7700  -76.2900  30.5  0  0  0
2  2355  234605.000  36.7701  -76.2901  30.6  0  0  0
3  2355  234610.000  36.7702  -76.2902  30.7  0  0  0
"""
    (tmp_path / "Timestamp.MRK").write_text(mrk_content)

    # Fake photo files
    for i in range(1, 4):
        (tmp_path / f"DJI_{i:04d}.JPG").write_bytes(b"\xff\xd8" + b"\x00" * 100)

    return tmp_path


@pytest.fixture
def tmp_pos_file(tmp_path):
    """Create a fake RTKLib .pos solution file."""
    pos_content = """%  GPST          latitude(deg) longitude(deg)  height(m)   Q  ns   sdn(m)   sde(m)   sdu(m)
2026/03/15 14:30:00.000   36.77000000  -76.29000000    30.500   1   15   0.005   0.004   0.012
2026/03/15 14:30:05.000   36.77010000  -76.29010000    30.600   1   14   0.006   0.005   0.013
2026/03/15 14:30:10.000   36.77020000  -76.29020000    30.700   2   12   0.015   0.012   0.025
"""
    pos_path = tmp_path / "test.pos"
    pos_path.write_text(pos_content)
    return str(pos_path)


# ── detect_rinex ──

class TestDetectRinex:
    def test_detects_rinex_files(self, tmp_flight_dir):
        result = detect_rinex(str(tmp_flight_dir))
        assert result is not None
        assert result.has_minimum
        assert "Rinex.obs" in result.obs_file
        assert "Timestamp.MRK" in result.mrk_file
        assert "Rinex.nav" in result.nav_file

    def test_returns_none_when_no_rinex(self, tmp_path):
        (tmp_path / "DJI_0001.JPG").write_bytes(b"\xff\xd8")
        result = detect_rinex(str(tmp_path))
        assert result is None

    def test_extracts_approx_position(self, tmp_flight_dir):
        result = detect_rinex(str(tmp_flight_dir))
        assert result.approx_lat is not None
        assert result.approx_lon is not None
        assert abs(result.approx_lat) > 0
        assert abs(result.approx_lon) > 0

    def test_extracts_flight_date(self, tmp_flight_dir):
        result = detect_rinex(str(tmp_flight_dir))
        assert result.flight_date is not None
        assert result.flight_date.year == 2026
        assert result.flight_date.month == 3

    def test_checks_survey_subdirectory(self, tmp_path):
        survey = tmp_path / "survey"
        survey.mkdir()
        (survey / "Rinex.obs").write_text("     3.03           OBSERVATION DATA\n                                                            END OF HEADER\n")
        (tmp_path / "Timestamp.MRK").write_text("1  2355  234600.000  36.77  -76.29  30.5  0  0  0\n")
        result = detect_rinex(str(tmp_path))
        assert result is not None
        assert "survey" in result.obs_file


# ── parse_mrk_file ──

class TestParseMrk:
    def test_parses_timestamps(self, tmp_flight_dir):
        mrk_path = str(tmp_flight_dir / "Timestamp.MRK")
        marks = parse_mrk_file(mrk_path)
        assert len(marks) == 3
        assert 1 in marks
        assert 2 in marks
        assert 3 in marks
        assert isinstance(marks[1], datetime)

    def test_timestamps_are_sequential(self, tmp_flight_dir):
        mrk_path = str(tmp_flight_dir / "Timestamp.MRK")
        marks = parse_mrk_file(mrk_path)
        assert marks[1] < marks[2] < marks[3]

    def test_empty_file_returns_empty_dict(self, tmp_path):
        empty = tmp_path / "empty.MRK"
        empty.write_text("")
        assert parse_mrk_file(str(empty)) == {}

    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert parse_mrk_file(str(tmp_path / "missing.MRK")) == {}


# ── parse_rtklib_pos ──

class TestParsePos:
    def test_parses_solutions(self, tmp_pos_file):
        solutions = parse_rtklib_pos(tmp_pos_file)
        assert len(solutions) == 3

    def test_solution_coordinates(self, tmp_pos_file):
        solutions = parse_rtklib_pos(tmp_pos_file)
        assert solutions[0].latitude == pytest.approx(36.77, abs=0.001)
        assert solutions[0].longitude == pytest.approx(-76.29, abs=0.001)
        assert solutions[0].altitude == pytest.approx(30.5, abs=0.1)

    def test_fix_quality(self, tmp_pos_file):
        solutions = parse_rtklib_pos(tmp_pos_file)
        assert solutions[0].fix_quality == 1  # fix
        assert solutions[2].fix_quality == 2  # float

    def test_empty_file(self, tmp_path):
        empty = tmp_path / "empty.pos"
        empty.write_text("")
        assert parse_rtklib_pos(str(empty)) == []


# ── match_solutions_to_photos ──

class TestMatchSolutions:
    def test_matches_by_timestamp(self):
        base_time = datetime(2026, 3, 15, 14, 30, 0, tzinfo=timezone.utc)
        solutions = [
            PPKSolution(timestamp=base_time, latitude=36.77, longitude=-76.29,
                        altitude=30.5, fix_quality=1, num_sats=15),
            PPKSolution(timestamp=base_time + timedelta(seconds=5),
                        latitude=36.7701, longitude=-76.2901,
                        altitude=30.6, fix_quality=1, num_sats=14),
        ]
        marks = {
            1: base_time + timedelta(milliseconds=50),
            2: base_time + timedelta(seconds=5, milliseconds=30),
        }
        photos = ["/photos/DJI_0001.JPG", "/photos/DJI_0002.JPG"]
        matches = match_solutions_to_photos(solutions, marks, photos)
        assert len(matches) == 2
        assert matches["/photos/DJI_0001.JPG"].latitude == pytest.approx(36.77)
        assert matches["/photos/DJI_0002.JPG"].latitude == pytest.approx(36.7701)

    def test_rejects_large_time_gap(self):
        base_time = datetime(2026, 3, 15, 14, 30, 0, tzinfo=timezone.utc)
        solutions = [
            PPKSolution(timestamp=base_time, latitude=36.77, longitude=-76.29,
                        altitude=30.5, fix_quality=1, num_sats=15),
        ]
        marks = {1: base_time + timedelta(seconds=10)}  # 10s gap > 500ms tolerance
        photos = ["/photos/DJI_0001.JPG"]
        matches = match_solutions_to_photos(solutions, marks, photos)
        assert len(matches) == 0

    def test_empty_inputs(self):
        assert match_solutions_to_photos([], {}, []) == {}


# ── haversine_km ──

class TestHaversine:
    def test_known_distance(self):
        # Chesapeake to Newport News ~35 km
        dist = _haversine_km(36.77, -76.29, 37.12, -76.47)
        assert 35 < dist < 45

    def test_zero_distance(self):
        assert _haversine_km(36.77, -76.29, 36.77, -76.29) == pytest.approx(0.0)


# ── RINEX header parsing ──

class TestRinexParsing:
    def test_extract_position_from_obs(self, tmp_flight_dir):
        obs_path = str(tmp_flight_dir / "Rinex.obs")
        lat, lon = _extract_approx_position_from_obs(obs_path)
        assert lat is not None
        assert lon is not None
        assert abs(lat) > 0

    def test_extract_date_from_obs(self, tmp_flight_dir):
        obs_path = str(tmp_flight_dir / "Rinex.obs")
        dt = _extract_flight_date_from_obs(obs_path)
        assert dt is not None
        assert dt.year == 2026
