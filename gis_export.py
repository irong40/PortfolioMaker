"""
gis_export.py — Sentinel Aerial
GIS-ready exports for a mission: photo positions (GeoJSON + CSV), DJI SRT
flight tracks (GeoJSON), and a combined Google Earth KML.

Geometry comes from data Sortie already extracts — PhotoMeta lat/lon/alt from
EXIF (photo_classifier) and per-frame SRT telemetry (property_highlights
parse_srt). Files are written into the mission output_dir so drive_delivery
ships them automatically and the report deliverables table lists them.

Coordinates are WGS-84 (EPSG:4326), GeoJSON axis order [lon, lat, alt].
"""

import csv
import json
import logging
from pathlib import Path
from xml.sax.saxutils import escape

from property_highlights import parse_srt

log = logging.getLogger(__name__)

# SRT sidecars are per-frame (~30 Hz); tracks keep ~1 point/sec plus endpoints.
TRACK_MIN_DT_S = 1.0


# ─── Photo positions ─────────────────────────────────────────────────────────

def _photos_with_gps(photos):
    """Photos that carry a GPS fix."""
    return [p for p in photos
            if p.latitude is not None and p.longitude is not None]


def export_photo_points_geojson(photos, out_path):
    """Write photo positions as a GeoJSON FeatureCollection of Points.

    Returns out_path, or None when no photo has GPS.
    """
    positioned = _photos_with_gps(photos)
    if not positioned:
        return None
    features = []
    for p in positioned:
        coords = [p.longitude, p.latitude]
        if p.altitude is not None:
            coords.append(p.altitude)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": coords},
            "properties": {
                "filename": p.filename,
                "altitude_m": p.altitude,
                "relative_altitude_m": p.relative_altitude,
                "gimbal_pitch_deg": p.pitch,
                "classification": p.classification,
                "platform": p.platform,
            },
        })
    collection = {
        "type": "FeatureCollection",
        "name": "photo_points",
        "crs": {"type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(collection, indent=2), encoding="utf-8")
    return str(out_path)


def export_photo_points_csv(photos, out_path):
    """Write photo positions as CSV (one row per GPS-tagged photo).

    Returns out_path, or None when no photo has GPS.
    """
    positioned = _photos_with_gps(photos)
    if not positioned:
        return None
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["filename", "latitude", "longitude", "altitude_m",
                         "relative_altitude_m", "gimbal_pitch_deg",
                         "classification", "platform"])
        for p in positioned:
            writer.writerow([p.filename, p.latitude, p.longitude, p.altitude,
                             p.relative_altitude, p.pitch,
                             p.classification, p.platform])
    return str(out_path)


# ─── Flight tracks (DJI SRT) ─────────────────────────────────────────────────

def find_srt_files(source_dir):
    """All DJI .srt sidecars under source_dir, sorted (DJI names sort in time)."""
    root = Path(source_dir)
    if not root.is_dir():
        return []
    found = {p.resolve() for p in root.rglob("*.srt")}
    found |= {p.resolve() for p in root.rglob("*.SRT")}
    return sorted(found)


def decimate_track(frames, min_dt_s=TRACK_MIN_DT_S):
    """Thin per-frame telemetry to ~one point per min_dt_s, keeping endpoints."""
    if len(frames) <= 2:
        return list(frames)
    kept = [frames[0]]
    for f in frames[1:-1]:
        if f["time_s"] - kept[-1]["time_s"] >= min_dt_s:
            kept.append(f)
    kept.append(frames[-1])
    return kept


def load_tracks(srt_files, min_dt_s=TRACK_MIN_DT_S):
    """Parse + decimate each SRT into (name, frames). Skips unusable files;
    a usable track has 2+ GPS-fixed frames."""
    tracks = []
    for srt in srt_files:
        try:
            frames = decimate_track(parse_srt(str(srt)), min_dt_s)
        except Exception as exc:
            log.warning("Skipping unparseable SRT %s: %s", srt, exc)
            continue
        if len(frames) >= 2:
            tracks.append((Path(srt).stem, frames))
    return tracks


def export_flight_tracks_geojson(tracks, out_path):
    """Write one GeoJSON LineString per loaded track (see load_tracks).

    Returns out_path, or None when there are no tracks.
    """
    features = []
    for name, frames in tracks:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[f["lon"], f["lat"], f["rel_alt"]]
                                for f in frames],
            },
            "properties": {
                "clip": name,
                "points": len(frames),
                "duration_s": round(frames[-1]["time_s"] - frames[0]["time_s"], 1),
            },
        })
    if not features:
        return None
    collection = {
        "type": "FeatureCollection",
        "name": "flight_tracks",
        "crs": {"type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(collection, indent=2), encoding="utf-8")
    return str(out_path)


# ─── KML (Google Earth) ──────────────────────────────────────────────────────

def export_mission_kml(photos, tracks, out_path, site_name="Mission"):
    """Write one KML with photo-position placemarks + flight-track lines.

    tracks: loaded (name, frames) pairs (see load_tracks).
    Returns out_path, or None when there is nothing to write.
    """
    positioned = _photos_with_gps(photos)
    if not positioned and not tracks:
        return None

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        "<Document>",
        f"<name>{escape(site_name)}</name>",
        '<Style id="track"><LineStyle><color>ff00ccff</color>'
        "<width>3</width></LineStyle></Style>",
        '<Style id="photo"><IconStyle><scale>0.7</scale></IconStyle></Style>',
    ]
    if positioned:
        parts.append("<Folder><name>Photo Positions</name>")
        for p in positioned:
            alt = p.altitude if p.altitude is not None else 0
            parts.append(
                f"<Placemark><name>{escape(p.filename)}</name>"
                '<styleUrl>#photo</styleUrl>'
                f"<Point><coordinates>{p.longitude},{p.latitude},{alt}"
                "</coordinates></Point></Placemark>")
        parts.append("</Folder>")
    if tracks:
        parts.append("<Folder><name>Flight Tracks</name>")
        for name, frames in tracks:
            coords = " ".join(f"{f['lon']},{f['lat']},{f['rel_alt']}"
                              for f in frames)
            parts.append(
                f"<Placemark><name>{escape(name)}</name>"
                '<styleUrl>#track</styleUrl>'
                "<LineString><tessellate>1</tessellate>"
                f"<coordinates>{coords}</coordinates></LineString></Placemark>")
        parts.append("</Folder>")
    parts.append("</Document></kml>")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(parts), encoding="utf-8")
    return str(out_path)


# ─── Mission orchestrator ────────────────────────────────────────────────────

def export_mission_gis(photos, source_dir, out_dir, site_name="Mission"):
    """Write every applicable GIS export into out_dir.

    Returns {filename: path} of files actually written (may be empty).
    """
    out = Path(out_dir)
    tracks = load_tracks(find_srt_files(source_dir))
    written = {}
    exports = [
        ("photo_points.geojson",
         lambda p: export_photo_points_geojson(photos, p)),
        ("photo_points.csv",
         lambda p: export_photo_points_csv(photos, p)),
        ("flight_tracks.geojson",
         lambda p: export_flight_tracks_geojson(tracks, p)),
        ("mission.kml",
         lambda p: export_mission_kml(photos, tracks, p, site_name)),
    ]
    for name, exporter in exports:
        try:
            path = exporter(str(out / name))
        except Exception as exc:
            log.warning("GIS export %s failed: %s", name, exc)
            continue
        if path:
            written[name] = path
    return written
