"""
Sortie — PPK Post-Processing Service

Auto-detects DJI Matrice 4E RINEX files alongside drone photos,
downloads matching NOAA CORS base station data, runs RTKLib rnx2rtkp
for PPK correction, and writes corrected coordinates back into photo EXIF.

Usage (standalone):
    python ppk_service.py D:\\DronePhotos\\JobSite1
    python ppk_service.py D:\\DronePhotos\\JobSite1 --dry-run

Usage (from Sortie GUI):
    from ppk_service import detect_rinex, run_ppk_correction
    rinex = detect_rinex(source_dir)
    if rinex:
        result = run_ppk_correction(rinex, progress_callback=...)
"""

import os
import re
import json
import math
import struct
import logging
import zipfile
import subprocess
import urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# RTKLib binary management
SCRIPT_DIR = Path(__file__).resolve().parent
RTKLIB_DIR = SCRIPT_DIR / "rtklib"
RNX2RTKP_EXE = RTKLIB_DIR / "rnx2rtkp.exe"
RTKLIB_DOWNLOAD_URL = (
    "https://github.com/rtklibexplorer/RTKLIB/releases/download/"
    "b34j/rtklib_2.4.3_b34j_bins.zip"
)

# NOAA CORS S3 bucket
CORS_S3_BASE = "https://noaa-cors-pds.s3.amazonaws.com"
CORS_STATIONS_JSON = SCRIPT_DIR / "cors_stations.json"

# DJI M4E file patterns
RINEX_OBS_PATTERNS = ["*.obs", "*.OBS", "*_rover.obs", "Rinex.obs"]
RINEX_NAV_PATTERNS = ["*.nav", "*.NAV", "*.brdc", "*.BRDC", "*.*n", "*.*N"]
MRK_PATTERNS = ["Timestamp.MRK", "*.MRK", "*.mrk"]
PPKRAW_PATTERNS = ["PPKRAW.bin", "ppkraw.bin", "*.rtk", "*.RTK"]


# Data classes

@dataclass
class RinexFiles:
    """Detected RINEX/PPK files from a DJI flight folder."""
    source_dir: str
    obs_file: str = ""
    nav_file: str = ""
    mrk_file: str = ""
    ppkraw_file: str = ""
    approx_lat: float = None
    approx_lon: float = None
    flight_date: datetime = None
    flight_duration_minutes: float = None

    @property
    def has_minimum(self):
        return bool(self.obs_file or self.ppkraw_file) and bool(self.mrk_file)


@dataclass
class CORSStation:
    """A NOAA CORS reference station."""
    station_id: str
    latitude: float
    longitude: float
    distance_km: float = 0.0


@dataclass
class PPKSolution:
    """A single corrected position from rnx2rtkp output."""
    timestamp: datetime
    latitude: float
    longitude: float
    altitude: float
    fix_quality: int  # 1=fix, 2=float, 5=single
    num_sats: int
    sdn: float = 0.0  # std dev north (m)
    sde: float = 0.0  # std dev east (m)
    sdu: float = 0.0  # std dev up (m)


@dataclass
class PPKResult:
    """Result of PPK correction process."""
    success: bool
    solutions: list = field(default_factory=list)
    photos_corrected: int = 0
    photos_total: int = 0
    fix_rate: float = 0.0
    cors_station: str = ""
    baseline_km: float = 0.0
    error: str = ""
    output_dir: str = ""


# RTKLib binary management

def ensure_rtklib():
    """Download RTKLib binaries if not present. Returns path to rnx2rtkp.exe."""
    if RNX2RTKP_EXE.exists():
        return str(RNX2RTKP_EXE)

    log.info("Downloading RTKLib binaries...")
    RTKLIB_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = RTKLIB_DIR / "rtklib.zip"

    try:
        urllib.request.urlretrieve(RTKLIB_DOWNLOAD_URL, str(zip_path))
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            for member in zf.namelist():
                basename = Path(member).name.lower()
                if basename == "rnx2rtkp.exe":
                    zf.extract(member, str(RTKLIB_DIR))
                    extracted = RTKLIB_DIR / member
                    if extracted != RNX2RTKP_EXE:
                        extracted.rename(RNX2RTKP_EXE)
                    break
        zip_path.unlink(missing_ok=True)
    except Exception as e:
        log.error(f"Failed to download RTKLib: {e}")
        raise RuntimeError(
            f"RTKLib download failed: {e}\n\n"
            "Manual install: download rnx2rtkp.exe from\n"
            "https://github.com/rtklibexplorer/RTKLIB/releases\n"
            f"and place it in {RTKLIB_DIR}"
        )

    if not RNX2RTKP_EXE.exists():
        raise RuntimeError(f"rnx2rtkp.exe not found after extraction in {RTKLIB_DIR}")

    log.info(f"RTKLib installed: {RNX2RTKP_EXE}")
    return str(RNX2RTKP_EXE)


# RINEX file detection

def _glob_first(directory, patterns):
    """Return the first file matching any of the glob patterns."""
    d = Path(directory)
    for pattern in patterns:
        matches = sorted(d.glob(pattern))
        if matches:
            return str(matches[0])
    return ""


def _extract_approx_position_from_obs(obs_path):
    """Read approximate position from RINEX OBS header."""
    try:
        with open(obs_path, "r", errors="ignore") as f:
            for line in f:
                if "APPROX POSITION XYZ" in line:
                    parts = line.split()
                    x, y, z = float(parts[0]), float(parts[1]), float(parts[2])
                    # ECEF to LLA (approximate)
                    a = 6378137.0
                    e2 = 0.00669437999014
                    p = math.sqrt(x * x + y * y)
                    lon = math.atan2(y, x)
                    lat = math.atan2(z, p * (1 - e2))
                    for _ in range(5):
                        n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
                        lat = math.atan2(z + e2 * n * math.sin(lat), p)
                    return math.degrees(lat), math.degrees(lon)
                if "END OF HEADER" in line:
                    break
    except (OSError, ValueError):
        pass
    return None, None


def _extract_flight_date_from_obs(obs_path):
    """Read flight date from RINEX OBS header TIME OF FIRST OBS."""
    try:
        with open(obs_path, "r", errors="ignore") as f:
            for line in f:
                if "TIME OF FIRST OBS" in line:
                    parts = line.split()
                    return datetime(
                        int(parts[0]), int(parts[1]), int(parts[2]),
                        int(parts[3]), int(parts[4]), int(float(parts[5])),
                        tzinfo=timezone.utc,
                    )
                if "END OF HEADER" in line:
                    break
    except (OSError, ValueError, IndexError):
        pass
    return None


def detect_rinex(source_dir):
    """Scan a photo folder for DJI M4E RINEX/PPK files.

    Checks the photo folder itself and common DJI SD card subfolders.
    Returns RinexFiles if found, None if no PPK data present.
    """
    source = Path(source_dir)
    search_dirs = [source]

    # DJI SD card structure: DCIM/DJI_xxx/ photos, with RINEX in same folder
    # or in a survey/ subfolder
    for subdir_name in ["survey", "Survey", "SURVEY", "rtk", "RTK"]:
        sub = source / subdir_name
        if sub.is_dir():
            search_dirs.append(sub)

    # Also check parent (SD card root might have RINEX alongside DCIM/)
    parent = source.parent
    for subdir_name in ["survey", "Survey", "SURVEY"]:
        sub = parent / subdir_name
        if sub.is_dir():
            search_dirs.append(sub)

    result = RinexFiles(source_dir=str(source))

    for d in search_dirs:
        if not result.obs_file:
            result.obs_file = _glob_first(d, RINEX_OBS_PATTERNS)
        if not result.nav_file:
            result.nav_file = _glob_first(d, RINEX_NAV_PATTERNS)
        if not result.mrk_file:
            result.mrk_file = _glob_first(d, MRK_PATTERNS)
        if not result.ppkraw_file:
            result.ppkraw_file = _glob_first(d, PPKRAW_PATTERNS)

    if not result.has_minimum:
        return None

    # Extract approximate position and date from OBS header
    if result.obs_file:
        result.approx_lat, result.approx_lon = _extract_approx_position_from_obs(
            result.obs_file
        )
        result.flight_date = _extract_flight_date_from_obs(result.obs_file)

    log.info(f"PPK data detected in {source_dir}")
    log.info(f"  OBS: {result.obs_file or 'not found'}")
    log.info(f"  NAV: {result.nav_file or 'not found'}")
    log.info(f"  MRK: {result.mrk_file or 'not found'}")
    if result.approx_lat:
        log.info(f"  Approx position: {result.approx_lat:.4f}, {result.approx_lon:.4f}")

    return result


# NOAA CORS station finder and downloader

def _haversine_km(lat1, lon1, lat2, lon2):
    """Distance between two lat/lon points in kilometers."""
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _load_cors_stations():
    """Load CORS station database from bundled JSON file.

    Format: {"station_id": [lat, lon], ...}
    """
    if not CORS_STATIONS_JSON.exists():
        log.warning(f"CORS station database not found: {CORS_STATIONS_JSON}")
        return {}
    try:
        with open(CORS_STATIONS_JSON, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"Failed to load CORS station database: {e}")
        return {}


def find_nearest_cors(lat, lon, max_distance_km=100, max_results=5):
    """Find nearest NOAA CORS stations to a given position.

    Uses the bundled cors_stations.json database.
    Returns list of CORSStation sorted by distance.
    """
    log.info(f"Finding CORS stations near {lat:.4f}, {lon:.4f}...")
    db = _load_cors_stations()
    if not db:
        log.error("No CORS station data available")
        return []

    stations = []
    for station_id, (slat, slon) in db.items():
        dist = _haversine_km(lat, lon, slat, slon)
        if dist <= max_distance_km:
            stations.append(CORSStation(
                station_id=station_id,
                latitude=slat,
                longitude=slon,
                distance_km=round(dist, 1),
            ))

    stations.sort(key=lambda s: s.distance_km)
    result = stations[:max_results]

    for s in result:
        log.info(f"  CORS: {s.station_id} — {s.distance_km} km")

    return result


def download_cors_rinex(station_id, flight_date, output_dir):
    """Download CORS RINEX observation file from NOAA S3.

    File path format: rinex/YYYY/DDD/ssss/ssssJJJ0.YYo.gz

    Args:
        station_id: 4-char CORS station ID (lowercase)
        flight_date: datetime of the flight
        output_dir: where to save the downloaded file

    Returns:
        Path to downloaded .obs file, or empty string on failure.
    """
    doy = flight_date.timetuple().tm_yday
    year = flight_date.year
    yy = year % 100
    sid = station_id.lower()[:4]

    # Try both compressed and uncompressed
    filename = f"{sid}{doy:03d}0.{yy:02d}o"
    gz_filename = f"{filename}.gz"
    url = f"{CORS_S3_BASE}/rinex/{year}/{doy:03d}/{sid}/{gz_filename}"

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gz_path = out_dir / gz_filename
    obs_path = out_dir / filename

    if obs_path.exists():
        log.info(f"CORS data already downloaded: {obs_path}")
        return str(obs_path)

    log.info(f"Downloading CORS data: {url}")
    try:
        urllib.request.urlretrieve(url, str(gz_path))
    except Exception as e:
        log.warning(f"Failed to download {url}: {e}")
        # Try uncompressed
        url2 = f"{CORS_S3_BASE}/rinex/{year}/{doy:03d}/{sid}/{filename}"
        try:
            urllib.request.urlretrieve(url2, str(obs_path))
            return str(obs_path)
        except Exception as e2:
            log.error(f"CORS download failed for {station_id}: {e2}")
            return ""

    # Decompress
    import gzip
    try:
        with gzip.open(str(gz_path), "rb") as gz_in:
            with open(str(obs_path), "wb") as out:
                out.write(gz_in.read())
        gz_path.unlink(missing_ok=True)
        log.info(f"CORS data saved: {obs_path}")
        return str(obs_path)
    except Exception as e:
        log.error(f"Failed to decompress CORS data: {e}")
        return ""


# MRK file parser (DJI timestamp marks)

def parse_mrk_file(mrk_path):
    """Parse DJI Timestamp.MRK file to get photo capture timestamps.

    MRK format (space-separated):
        index  GPS_week  GPS_seconds  latitude  longitude  altitude  ...

    Returns dict mapping 1-based photo index to GPS timestamp.
    """
    marks = {}
    try:
        with open(mrk_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 6:
                    continue
                try:
                    idx = int(parts[0])
                    gps_week = int(parts[1])
                    gps_sow = float(parts[2])
                    # GPS epoch: Jan 6, 1980
                    gps_epoch = datetime(1980, 1, 6, tzinfo=timezone.utc)
                    ts = gps_epoch + timedelta(weeks=gps_week, seconds=gps_sow)
                    # Leap seconds (GPS to UTC): 18 as of 2024
                    ts = ts - timedelta(seconds=18)
                    marks[idx] = ts
                except (ValueError, IndexError):
                    continue
    except OSError as e:
        log.error(f"Failed to read MRK file: {e}")

    log.info(f"Parsed {len(marks)} photo timestamps from MRK file")
    return marks


# RTKLib solution parser

def parse_rtklib_pos(pos_path):
    """Parse rnx2rtkp output .pos file.

    Format (header lines start with %):
        GPST  latitude(deg)  longitude(deg)  height(m)  Q  ns  sdn  sde  sdu ...

    Returns list of PPKSolution.
    """
    solutions = []
    try:
        with open(pos_path, "r") as f:
            for line in f:
                if line.startswith("%") or not line.strip():
                    continue
                parts = line.split()
                if len(parts) < 9:
                    continue
                try:
                    date_str = parts[0]
                    time_str = parts[1]
                    dt_str = f"{date_str} {time_str}"
                    ts = datetime.strptime(dt_str, "%Y/%m/%d %H:%M:%S.%f")
                    ts = ts.replace(tzinfo=timezone.utc)

                    solutions.append(PPKSolution(
                        timestamp=ts,
                        latitude=float(parts[2]),
                        longitude=float(parts[3]),
                        altitude=float(parts[4]),
                        fix_quality=int(parts[5]),
                        num_sats=int(parts[6]),
                        sdn=float(parts[7]),
                        sde=float(parts[8]),
                        sdu=float(parts[9]) if len(parts) > 9 else 0.0,
                    ))
                except (ValueError, IndexError):
                    continue
    except OSError as e:
        log.error(f"Failed to read POS file: {e}")

    log.info(f"Parsed {len(solutions)} PPK solutions")
    fix_count = sum(1 for s in solutions if s.fix_quality == 1)
    if solutions:
        log.info(f"  Fix rate: {fix_count}/{len(solutions)} ({100*fix_count/len(solutions):.0f}%)")

    return solutions


# RTKLib PPK processing

def _write_rtklib_config(config_path):
    """Write an RTKLib config file optimized for DJI drone PPK."""
    config = """# RTKLib config for DJI M4E PPK
pos1-posmode       =kinematic
pos1-frequency     =l1+l2
pos1-soltype       =combined
pos1-elmask        =15
pos1-snrmask_r     =on
pos1-snrmask_b     =on
pos1-dynamics      =on
pos1-tidecorr      =off
pos1-ionoopt       =brdc
pos1-tropopt       =saas
pos1-sateph        =brdc
pos1-posopt1       =on
pos1-posopt2       =on
pos1-posopt5       =off
pos1-exclsats      =
pos1-navsys        =7
pos2-armode        =fix-and-hold
pos2-gloarmode     =on
pos2-bdsarmode     =on
pos2-arthres       =3
pos2-arlockcnt     =0
pos2-arelmask      =0
pos2-arminfix      =20
pos2-armaxiter     =1
pos2-elmaskhold    =0
pos2-aroutcnt      =5
pos2-maxage        =30
pos2-slipthres     =0.05
pos2-rejionno      =30
pos2-rejgdop       =30
pos2-niter         =1
pos2-baselen       =0
pos2-basesig       =0
out-solformat      =llh
out-outhead        =on
out-outopt         =on
out-timesys        =gpst
out-timeform       =hms
out-timendec       =3
out-degform        =deg
out-fieldsep       =
out-height         =ellipsoidal
out-geoid          =internal
out-solstatic      =all
out-nmeaintv1      =0
out-nmeaintv2      =0
out-outstat        =off
stats-eratio1      =300
stats-eratio2      =300
stats-errphase     =0.003
stats-errphasel    =0.003
stats-errphasebl   =0
stats-errdoppler   =10
stats-stdbias      =30
stats-stdiono      =0.03
stats-stdtrop      =0.3
stats-prnaccelh    =10
stats-prnaccelv    =10
stats-prnbias      =0.0001
stats-prniono      =0.001
stats-prntrop      =0.0001
stats-prnpos       =0
stats-clkstab      =5e-12
ant1-postype       =rinexhead
ant1-pos1          =0
ant1-pos2          =0
ant1-pos3          =0
ant1-anttype       =
ant1-antdele       =0
ant1-antdeln       =0
ant1-antdelu       =0
ant2-postype       =rinexhead
ant2-pos1          =0
ant2-pos2          =0
ant2-pos3          =0
ant2-anttype       =
ant2-antdele       =0
ant2-antdeln       =0
ant2-antdelu       =0
misc-timeinterp    =on
misc-sbasatsel     =0
misc-rnxopt1       =
misc-rnxopt2       =
"""
    with open(config_path, "w") as f:
        f.write(config)
    return str(config_path)


def run_rnx2rtkp(rover_obs, base_obs, nav_file, output_dir, config_path=None):
    """Run RTKLib rnx2rtkp to compute PPK solution.

    Args:
        rover_obs: Path to rover (drone) RINEX OBS file
        base_obs: Path to base station RINEX OBS file
        nav_file: Path to navigation (broadcast ephemeris) file
        output_dir: Where to write the .pos solution file
        config_path: Optional path to RTKLib config file

    Returns:
        Path to .pos solution file, or empty string on failure.
    """
    exe = ensure_rtklib()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pos_file = out_dir / "ppk_solution.pos"

    if config_path is None:
        config_path = str(out_dir / "ppk_config.conf")
        _write_rtklib_config(config_path)

    cmd = [
        exe,
        "-k", config_path,
        "-o", str(pos_file),
        rover_obs,
        base_obs,
        nav_file,
    ]

    log.info(f"Running rnx2rtkp: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            log.error(f"rnx2rtkp failed (code {result.returncode})")
            log.error(f"  stderr: {result.stderr[:500]}")
            return ""
    except subprocess.TimeoutExpired:
        log.error("rnx2rtkp timed out after 300 seconds")
        return ""
    except FileNotFoundError:
        log.error(f"rnx2rtkp.exe not found at {exe}")
        return ""

    if not pos_file.exists() or pos_file.stat().st_size == 0:
        log.error("rnx2rtkp produced no output")
        return ""

    log.info(f"PPK solution written: {pos_file}")
    return str(pos_file)


# Photo EXIF coordinate updater

def match_solutions_to_photos(solutions, mrk_marks, photo_paths, tolerance_ms=500):
    """Match PPK solutions to photos using MRK timestamps.

    Args:
        solutions: list of PPKSolution from rnx2rtkp
        mrk_marks: dict mapping 1-based index to datetime (from parse_mrk_file)
        photo_paths: sorted list of photo file paths
        tolerance_ms: max time difference for matching (milliseconds)

    Returns:
        dict mapping photo_path to PPKSolution (only matched photos included)
    """
    if not solutions or not mrk_marks:
        return {}

    matches = {}
    tolerance = timedelta(milliseconds=tolerance_ms)

    for idx, mrk_ts in sorted(mrk_marks.items()):
        if idx < 1 or idx > len(photo_paths):
            continue

        photo_path = photo_paths[idx - 1]
        best_sol = None
        best_diff = timedelta.max

        for sol in solutions:
            diff = abs(sol.timestamp - mrk_ts)
            if diff < best_diff:
                best_diff = diff
                best_sol = sol

        if best_sol and best_diff <= tolerance:
            matches[photo_path] = best_sol

    log.info(f"Matched {len(matches)}/{len(photo_paths)} photos to PPK solutions")
    fix_count = sum(1 for s in matches.values() if s.fix_quality == 1)
    log.info(f"  Fixed: {fix_count}, Float: {len(matches) - fix_count}")

    return matches


def update_photo_exif(photo_path, lat, lon, alt):
    """Write corrected GPS coordinates into a photo's EXIF data.

    Uses piexif library if available, falls back to PIL.
    """
    try:
        import piexif

        def _to_dms_rational(value):
            """Convert decimal degrees to EXIF DMS rational format."""
            value = abs(value)
            d = int(value)
            m = int((value - d) * 60)
            s = int(((value - d) * 60 - m) * 60 * 10000)
            return ((d, 1), (m, 1), (s, 10000))

        exif_dict = piexif.load(photo_path)

        lat_ref = b"N" if lat >= 0 else b"S"
        lon_ref = b"E" if lon >= 0 else b"W"
        alt_ref = 0 if alt >= 0 else 1

        gps_ifd = {
            piexif.GPSIFD.GPSLatitudeRef: lat_ref,
            piexif.GPSIFD.GPSLatitude: _to_dms_rational(lat),
            piexif.GPSIFD.GPSLongitudeRef: lon_ref,
            piexif.GPSIFD.GPSLongitude: _to_dms_rational(lon),
            piexif.GPSIFD.GPSAltitudeRef: alt_ref,
            piexif.GPSIFD.GPSAltitude: (int(abs(alt) * 1000), 1000),
        }
        exif_dict["GPS"] = gps_ifd
        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, photo_path)
        return True

    except ImportError:
        log.warning("piexif not installed — using PIL fallback (less precise)")

    try:
        from PIL import Image
        import struct

        img = Image.open(photo_path)
        exif = img.getexif()
        gps_info = exif.get_ifd(0x8825) or {}

        def _to_dms(value):
            value = abs(value)
            d = int(value)
            m = int((value - d) * 60)
            s = (value - d - m / 60) * 3600
            return (d, m, s)

        gps_info[1] = "N" if lat >= 0 else "S"
        gps_info[2] = _to_dms(lat)
        gps_info[3] = "E" if lon >= 0 else "W"
        gps_info[4] = _to_dms(lon)
        gps_info[6] = abs(alt)

        exif[0x8825] = gps_info
        img.save(photo_path, exif=exif.tobytes())
        return True

    except Exception as e:
        log.error(f"Failed to update EXIF for {photo_path}: {e}")
        return False


# Main PPK correction orchestrator

def run_ppk_correction(rinex, source_dir=None, progress_callback=None):
    """Run the full PPK correction pipeline.

    1. Find nearest CORS station
    2. Download CORS RINEX data
    3. Run rnx2rtkp
    4. Parse solutions
    5. Match to photos via MRK timestamps
    6. Update photo EXIF coordinates

    Args:
        rinex: RinexFiles from detect_rinex()
        source_dir: Photo folder (defaults to rinex.source_dir)
        progress_callback: Optional callable(stage, detail)

    Returns:
        PPKResult with correction statistics
    """
    source = Path(source_dir or rinex.source_dir)
    work_dir = source / "_ppk_work"
    work_dir.mkdir(exist_ok=True)

    result = PPKResult(success=False, output_dir=str(work_dir))

    def emit(stage, detail):
        log.info(f"[PPK:{stage}] {detail}")
        if progress_callback:
            progress_callback(stage, detail)

    # Step 1: Validate inputs
    emit("validate", "Checking RINEX files...")
    if not rinex.obs_file:
        result.error = "No RINEX OBS file found"
        return result
    if not rinex.mrk_file:
        result.error = "No MRK timestamp file found"
        return result

    # Step 2: Get approximate position
    if rinex.approx_lat is None:
        emit("validate", "No approximate position in RINEX header — reading from photos")
        from photo_classifier import scan_photos, get_gps_data
        photos = scan_photos(str(source))
        if photos:
            gps = get_gps_data(str(photos[0]))
            if gps:
                rinex.approx_lat = gps[1]
                rinex.approx_lon = gps[0]

    if rinex.approx_lat is None:
        result.error = "Cannot determine flight position for CORS lookup"
        return result

    # Step 3: Find nearest CORS station
    emit("cors", f"Finding CORS stations near {rinex.approx_lat:.4f}, {rinex.approx_lon:.4f}")
    stations = find_nearest_cors(rinex.approx_lat, rinex.approx_lon)
    if not stations:
        result.error = "No CORS stations found within 100 km"
        return result

    # Step 4: Get flight date
    flight_date = rinex.flight_date
    if flight_date is None:
        emit("cors", "No flight date in RINEX — using today")
        flight_date = datetime.now(timezone.utc)

    # Step 5: Download CORS data (try stations in order of distance)
    cors_obs = ""
    chosen_station = None
    for station in stations:
        emit("cors", f"Downloading CORS data from {station.station_id} ({station.distance_km} km)...")
        cors_obs = download_cors_rinex(
            station.station_id, flight_date, str(work_dir)
        )
        if cors_obs:
            chosen_station = station
            break
        emit("cors", f"  {station.station_id} unavailable, trying next...")

    if not cors_obs:
        result.error = "Failed to download CORS data from any nearby station"
        return result

    result.cors_station = chosen_station.station_id
    result.baseline_km = chosen_station.distance_km

    # Step 6: Ensure RTKLib is available
    emit("rtklib", "Checking RTKLib installation...")
    try:
        ensure_rtklib()
    except RuntimeError as e:
        result.error = str(e)
        return result

    # Step 7: Run rnx2rtkp
    nav = rinex.nav_file
    if not nav:
        emit("rtklib", "No navigation file — downloading broadcast ephemeris...")
        # TODO: auto-download broadcast ephemeris from IGS
        result.error = "No navigation file found and auto-download not yet implemented"
        return result

    emit("rtklib", "Running PPK processing...")
    pos_file = run_rnx2rtkp(rinex.obs_file, cors_obs, nav, str(work_dir))
    if not pos_file:
        result.error = "RTKLib processing failed — check logs for details"
        return result

    # Step 8: Parse solutions
    emit("parse", "Parsing PPK solutions...")
    solutions = parse_rtklib_pos(pos_file)
    result.solutions = solutions
    if not solutions:
        result.error = "No solutions produced — check input data quality"
        return result

    fix_count = sum(1 for s in solutions if s.fix_quality == 1)
    result.fix_rate = fix_count / len(solutions) if solutions else 0

    # Step 9: Parse MRK and match to photos
    emit("match", "Matching solutions to photos...")
    mrk_marks = parse_mrk_file(rinex.mrk_file)

    from photo_classifier import scan_photos, PHOTO_EXTENSIONS
    photo_files = scan_photos(str(source))
    photo_paths = [str(p) for p in photo_files]
    result.photos_total = len(photo_paths)

    matches = match_solutions_to_photos(solutions, mrk_marks, photo_paths)

    # Step 10: Update EXIF
    emit("exif", f"Updating coordinates for {len(matches)} photos...")
    corrected = 0
    for i, (photo_path, sol) in enumerate(matches.items()):
        if update_photo_exif(photo_path, sol.latitude, sol.longitude, sol.altitude):
            corrected += 1
        if progress_callback and ((i + 1) % 50 == 0 or (i + 1) == len(matches)):
            progress_callback("exif", f"Updated {i + 1}/{len(matches)} photos")

    result.photos_corrected = corrected
    result.success = corrected > 0

    emit("done", (
        f"PPK complete: {corrected}/{result.photos_total} photos corrected, "
        f"{result.fix_rate:.0%} fix rate, "
        f"base: {result.cors_station} ({result.baseline_km} km)"
    ))

    # Write summary
    summary = {
        "ppk_version": "1.0",
        "cors_station": result.cors_station,
        "baseline_km": result.baseline_km,
        "photos_corrected": result.photos_corrected,
        "photos_total": result.photos_total,
        "fix_rate": round(result.fix_rate, 3),
        "solutions_count": len(solutions),
    }
    summary_path = work_dir / "ppk_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    return result


# CLI entry point

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sortie — PPK Post-Processing")
    parser.add_argument("source", help="Folder containing drone photos + RINEX files")
    parser.add_argument("--dry-run", action="store_true",
                        help="Detect RINEX and show CORS stations without processing")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    source = os.path.abspath(args.source)
    if not os.path.isdir(source):
        print(f"Error: Directory not found: {source}")
        return

    rinex = detect_rinex(source)
    if not rinex:
        print("No PPK/RINEX data found in this folder.")
        print("PPK requires .obs + .mrk files from the DJI M4E flight logs.")
        return

    print(f"\nPPK data found:")
    print(f"  OBS:  {rinex.obs_file}")
    print(f"  NAV:  {rinex.nav_file}")
    print(f"  MRK:  {rinex.mrk_file}")
    if rinex.approx_lat:
        print(f"  Position: {rinex.approx_lat:.4f}, {rinex.approx_lon:.4f}")
    if rinex.flight_date:
        print(f"  Date: {rinex.flight_date.strftime('%Y-%m-%d %H:%M UTC')}")

    if args.dry_run:
        if rinex.approx_lat:
            stations = find_nearest_cors(rinex.approx_lat, rinex.approx_lon)
            if stations:
                print(f"\nNearest CORS stations:")
                for s in stations:
                    print(f"  {s.station_id}: {s.distance_km} km")
        return

    def progress(stage, detail):
        print(f"  [{stage}] {detail}")

    result = run_ppk_correction(rinex, progress_callback=progress)
    if result.success:
        print(f"\nPPK correction complete:")
        print(f"  Photos corrected: {result.photos_corrected}/{result.photos_total}")
        print(f"  Fix rate: {result.fix_rate:.0%}")
        print(f"  CORS station: {result.cors_station} ({result.baseline_km} km)")
    else:
        print(f"\nPPK correction failed: {result.error}")


if __name__ == "__main__":
    main()
