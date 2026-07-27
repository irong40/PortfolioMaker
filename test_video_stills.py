"""Tests for video_stills.py — SRT parsing, cardinal selection, extraction."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from video_stills import (
    parse_srt_telemetry, has_yaw_telemetry, select_cardinal_frames,
    yaw_coverage_deg, extract_cardinal_stills, _angle_diff,
)


def _srt_block(idx, t0, t1, yaw, lat=36.795, lon=-76.405,
               rel_alt=45.3, abs_alt=57.4):
    return (
        f"{idx}\n"
        f"00:00:{t0:02d},000 --> 00:00:{t1:02d},000\n"
        f"<font size=\"28\">SrtCnt : {idx}, DiffTime : 33ms\n"
        f"2026-07-15 15:34:{t0:02d}.123\n"
        f"[iso : 100] [shutter : 1/1000.0] [fnum : 1.7] "
        f"[latitude: {lat}] [longitude: {lon}] "
        f"[rel_alt: {rel_alt} abs_alt: {abs_alt}] "
        f"[gb_yaw: {yaw} gb_pitch: -35.0 gb_roll: 0.0] </font>\n\n"
    )


def _orbit_srt(tmp_path, yaws, name="orbit.SRT"):
    """SRT sweeping through the given yaw values, one per second."""
    text = "".join(
        _srt_block(i + 1, i, i + 1, y) for i, y in enumerate(yaws))
    p = tmp_path / name
    p.write_text(text)
    return str(p)


FULL_ORBIT = list(range(-180, 180, 10))  # DJI-style -180..180 sweep


class TestAngleDiff:
    def test_wraparound(self):
        assert _angle_diff(350, 10) == 20
        assert _angle_diff(0, 359) == 1
        assert _angle_diff(-170, 190) == 0

    def test_plain(self):
        assert _angle_diff(90, 45) == 45


class TestParseSrt:
    def test_parses_samples(self, tmp_path):
        srt = _orbit_srt(tmp_path, [0, 90, 180])
        samples = parse_srt_telemetry(srt)
        assert len(samples) == 3
        s = samples[1]
        assert s["yaw"] == 90.0
        assert s["t"] == 1.0
        assert s["lat"] == pytest.approx(36.795)
        assert s["lon"] == pytest.approx(-76.405)
        assert s["abs_alt"] == pytest.approx(57.4)
        assert s["timestamp"].startswith("2026-07-15")

    def test_missing_file(self, tmp_path):
        assert parse_srt_telemetry(str(tmp_path / "nope.SRT")) == []

    def test_no_yaw_tags(self, tmp_path):
        p = tmp_path / "plain.SRT"
        p.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n\n")
        assert parse_srt_telemetry(str(p)) == []

    def test_has_yaw_telemetry(self, tmp_path):
        srt = _orbit_srt(tmp_path, [0])
        assert has_yaw_telemetry(srt) is True
        p = tmp_path / "plain.SRT"
        p.write_text("1\n00:00:00,000 --> 00:00:01,000\nno tags here\n\n")
        assert has_yaw_telemetry(str(p)) is False
        assert has_yaw_telemetry(None) is False


class TestCardinalSelection:
    def test_full_orbit_hits_all_four(self, tmp_path):
        samples = parse_srt_telemetry(_orbit_srt(tmp_path, FULL_ORBIT))
        picks, missing = select_cardinal_frames(samples)
        assert missing == []
        assert set(picks) == {"N", "E", "S", "W"}
        assert picks["N"]["error"] <= 5
        assert picks["E"]["target"] == 90.0

    def test_partial_orbit_reports_missing(self, tmp_path):
        # Sweep only 0..90 — S and W unreachable
        samples = parse_srt_telemetry(
            _orbit_srt(tmp_path, list(range(0, 91, 10))))
        picks, missing = select_cardinal_frames(samples)
        assert set(picks) == {"N", "E"}
        assert set(missing) == {"S", "W"}

    def test_front_bearing_relative_labels(self, tmp_path):
        samples = parse_srt_telemetry(_orbit_srt(tmp_path, FULL_ORBIT))
        picks, missing = select_cardinal_frames(samples, front_bearing=45.0)
        assert missing == []
        assert set(picks) == {"front", "right", "back", "left"}
        assert picks["front"]["target"] == 45.0
        assert picks["left"]["target"] == 315.0

    def test_coverage(self, tmp_path):
        samples = parse_srt_telemetry(_orbit_srt(tmp_path, FULL_ORBIT))
        assert yaw_coverage_deg(samples) == 360.0


class TestExtractCardinalStills:
    def _fake_ffmpeg(self):
        """Patch extract_frame's subprocess to fake a written JPG."""
        def run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"\xff\xd8\xff\xdb fake jpg")
            class P: returncode = 0; stderr = ""
            return P()
        return patch("video_stills.subprocess.run", side_effect=run)

    def test_happy_path(self, tmp_path):
        srt = _orbit_srt(tmp_path, FULL_ORBIT)
        out = tmp_path / "stills"
        with self._fake_ffmpeg(), \
             patch("video_stills.inject_exif", return_value=True):
            result = extract_cardinal_stills(
                str(tmp_path / "orbit.MP4"), srt, str(out),
                site_name="TestSite")
        assert result["error"] is None
        assert set(result["stills"]) == {"N", "E", "S", "W"}
        for p in result["stills"].values():
            assert Path(p).exists()
            assert Path(p).name.startswith("TestSite_orbit_")
        manifest = json.loads(Path(result["manifest"]).read_text())
        assert len(manifest["stills"]) == 4
        assert manifest["coverage_deg"] == 360.0

    def test_no_srt(self, tmp_path):
        result = extract_cardinal_stills(
            str(tmp_path / "v.MP4"), str(tmp_path / "missing.SRT"),
            str(tmp_path / "out"))
        assert result["error"] == "no yaw telemetry in SRT"
        assert result["stills"] == {}

    def test_ffmpeg_failure_reports(self, tmp_path):
        srt = _orbit_srt(tmp_path, FULL_ORBIT)

        def run(cmd, **kwargs):
            class P: returncode = 1; stderr = "boom"
            return P()

        with patch("video_stills.subprocess.run", side_effect=run):
            result = extract_cardinal_stills(
                str(tmp_path / "v.MP4"), srt, str(tmp_path / "out"))
        assert result["stills"] == {}
        assert "ffmpeg" in result["error"]
        assert set(result["missing"]) == {"N", "E", "S", "W"}

    def test_partial_orbit_still_extracts_available(self, tmp_path):
        srt = _orbit_srt(tmp_path, list(range(0, 91, 10)))
        with self._fake_ffmpeg(), \
             patch("video_stills.inject_exif", return_value=True):
            result = extract_cardinal_stills(
                str(tmp_path / "v.MP4"), srt, str(tmp_path / "out"))
        assert set(result["stills"]) == {"N", "E"}
        assert set(result["missing"]) == {"S", "W"}
        assert result["error"] is None
