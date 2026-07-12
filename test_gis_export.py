"""Tests for gis_export — photo points, SRT flight tracks, KML, orchestrator."""

import csv
import json
from pathlib import Path

import pytest

from gis_export import (
    decimate_track,
    export_flight_tracks_geojson,
    export_mission_gis,
    export_mission_kml,
    export_photo_points_csv,
    export_photo_points_geojson,
    find_srt_files,
    load_tracks,
)
from photo_classifier import PhotoMeta


def make_photo(name="DJI_0001.JPG", lat=36.75, lon=-76.25, alt=45.0):
    return PhotoMeta(filename=name, path=f"C:/fake/{name}", pitch=-89.9,
                     latitude=lat, longitude=lon, altitude=alt,
                     relative_altitude=30.0, platform="mini4pro",
                     classification="nadir")


def write_srt(path, points, dt=1.0 / 30):
    """Write a minimal DJI-style SRT with per-frame telemetry blocks."""
    blocks = []
    for i, (lat, lon) in enumerate(points):
        t = i * dt
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        start = f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"
        blocks.append(
            f"{i + 1}\n{start} --> {start}\n"
            f'<font size="28">[latitude: {lat:.6f}] [longitude: {lon:.6f}] '
            f"[rel_alt: 30.500 abs_alt: 45.100] [focal_len: 24.00]</font>\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return str(path)


class TestPhotoPoints:
    def test_geojson_structure(self, tmp_path):
        photos = [make_photo(), make_photo("DJI_0002.JPG", lat=36.751)]
        out = export_photo_points_geojson(photos, tmp_path / "pts.geojson")
        data = json.loads((tmp_path / "pts.geojson").read_text())
        assert out and data["type"] == "FeatureCollection"
        assert len(data["features"]) == 2
        feat = data["features"][0]
        assert feat["geometry"]["coordinates"] == [-76.25, 36.75, 45.0]
        assert feat["properties"]["filename"] == "DJI_0001.JPG"
        assert feat["properties"]["classification"] == "nadir"

    def test_geojson_skips_photos_without_gps(self, tmp_path):
        photos = [make_photo(), PhotoMeta(filename="nogps.JPG", path="x")]
        export_photo_points_geojson(photos, tmp_path / "pts.geojson")
        data = json.loads((tmp_path / "pts.geojson").read_text())
        assert len(data["features"]) == 1

    def test_geojson_none_when_no_gps(self, tmp_path):
        photos = [PhotoMeta(filename="nogps.JPG", path="x")]
        assert export_photo_points_geojson(photos, tmp_path / "p.geojson") is None
        assert not (tmp_path / "p.geojson").exists()

    def test_csv_rows(self, tmp_path):
        photos = [make_photo(), make_photo("DJI_0002.JPG")]
        export_photo_points_csv(photos, tmp_path / "pts.csv")
        with open(tmp_path / "pts.csv", newline="") as fh:
            rows = list(csv.reader(fh))
        assert rows[0][0] == "filename"
        assert len(rows) == 3
        assert rows[1][1] == "36.75"


class TestFlightTracks:
    def test_decimate_keeps_endpoints(self):
        frames = [{"time_s": i * 0.1, "lat": 1.0, "lon": 1.0} for i in range(100)]
        kept = decimate_track(frames, min_dt_s=1.0)
        assert kept[0] is frames[0] and kept[-1] is frames[-1]
        assert len(kept) < 15  # ~10s of 10 Hz → ~11 points

    def test_load_tracks(self, tmp_path):
        pts = [(36.75 + i * 1e-4, -76.25) for i in range(60)]
        srt = write_srt(tmp_path / "DJI_0001.SRT", pts)
        tracks = load_tracks([srt])
        assert len(tracks) == 1
        name, frames = tracks[0]
        assert name == "DJI_0001" and len(frames) >= 2

    def test_load_tracks_skips_unusable(self, tmp_path):
        bad = tmp_path / "empty.SRT"
        bad.write_text("not srt data")
        assert load_tracks([str(bad)]) == []

    def test_tracks_geojson(self, tmp_path):
        pts = [(36.75 + i * 1e-4, -76.25) for i in range(60)]
        srt = write_srt(tmp_path / "DJI_0001.SRT", pts)
        out = export_flight_tracks_geojson(load_tracks([srt]),
                                           tmp_path / "tracks.geojson")
        data = json.loads((tmp_path / "tracks.geojson").read_text())
        assert out and len(data["features"]) == 1
        line = data["features"][0]
        assert line["geometry"]["type"] == "LineString"
        assert line["properties"]["clip"] == "DJI_0001"
        # GeoJSON axis order is [lon, lat, alt]
        assert line["geometry"]["coordinates"][0][0] == pytest.approx(-76.25)

    def test_none_when_no_tracks(self, tmp_path):
        assert export_flight_tracks_geojson([], tmp_path / "t.geojson") is None

    def test_find_srt_files(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "DJI_0001.SRT").write_text("x")
        (tmp_path / "sub" / "DJI_0002.SRT").write_text("x")
        (tmp_path / "clip.mp4").write_text("x")
        found = find_srt_files(tmp_path)
        assert len(found) == 2

    def test_find_srt_missing_dir(self):
        assert find_srt_files("C:/does/not/exist") == []


class TestKml:
    def test_kml_contains_points_and_track(self, tmp_path):
        pts = [(36.75 + i * 1e-4, -76.25) for i in range(60)]
        srt = write_srt(tmp_path / "DJI_0001.SRT", pts)
        photos = [make_photo()]
        out = export_mission_kml(photos, load_tracks([srt]),
                                 tmp_path / "m.kml", "Test Site")
        text = (tmp_path / "m.kml").read_text()
        assert out and "<name>Test Site</name>" in text
        assert "Photo Positions" in text and "Flight Tracks" in text
        assert "-76.25,36.75,45.0" in text
        assert "<LineString>" in text

    def test_kml_none_when_nothing(self, tmp_path):
        assert export_mission_kml([], [], tmp_path / "m.kml") is None


class TestExportMissionGis:
    def test_full_export(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        write_srt(src / "DJI_0001.SRT",
                  [(36.75 + i * 1e-4, -76.25) for i in range(60)])
        photos = [make_photo()]
        out_dir = tmp_path / "out" / "gis"
        written = export_mission_gis(photos, src, out_dir, site_name="Test")
        assert set(written) == {"photo_points.geojson", "photo_points.csv",
                                "flight_tracks.geojson", "mission.kml"}
        for path in written.values():
            assert Path(path).exists(), f"returned path missing: {path}"
        assert (out_dir / "mission.kml").exists()

    def test_photos_only(self, tmp_path):
        written = export_mission_gis([make_photo()], tmp_path / "nosrc",
                                     tmp_path / "gis")
        assert set(written) == {"photo_points.geojson", "photo_points.csv",
                                "mission.kml"}

    def test_nothing_to_export(self, tmp_path):
        written = export_mission_gis([PhotoMeta(filename="x", path="x")],
                                     tmp_path / "nosrc", tmp_path / "gis")
        assert written == {}
