"""
Sortie -- CloudCompare CLI Wrapper

Wraps CloudCompare command line mode for point cloud operations:
1. Volume calculation via 2.5D grid
2. M3C2 distance computation (change detection between surveys)
3. Point cloud info and stats
4. Branded PDF volume report generation

Complements point_cloud_ops.py (Open3D based) with CloudCompare's
robust algorithms for large point clouds and noisy drone data.
"""

import json
import logging
import os
import re
import struct
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

CLOUDCOMPARE_DOWNLOAD_URL = "https://www.danielgm.net/cc/release/"

# Common install locations on Windows
_CC_SEARCH_PATHS = [
    r"C:\Program Files\CloudCompare\CloudCompare.exe",
    r"C:\Program Files (x86)\CloudCompare\CloudCompare.exe",
    r"C:\Program Files\CloudCompare\cloudcompare.exe",
]


def find_cloudcompare() -> str:
    """Find CloudCompare executable on the system.

    Checks common Windows install paths and the system PATH.

    Returns:
        Full path to CloudCompare executable.

    Raises:
        FileNotFoundError: If CloudCompare is not installed, with download URL.
    """
    # Check common install locations
    for path in _CC_SEARCH_PATHS:
        if os.path.isfile(path):
            log.info(f"Found CloudCompare at {path}")
            return path

    # Check PATH
    try:
        result = subprocess.run(
            ["where", "CloudCompare"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            cc_path = result.stdout.strip().splitlines()[0]
            log.info(f"Found CloudCompare in PATH: {cc_path}")
            return cc_path
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Also try the command variant used by some installers
    try:
        result = subprocess.run(
            ["where", "cloudcompare.CloudCompare"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            cc_path = result.stdout.strip().splitlines()[0]
            log.info(f"Found CloudCompare in PATH: {cc_path}")
            return cc_path
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    raise FileNotFoundError(
        "CloudCompare not found. Install it from "
        f"{CLOUDCOMPARE_DOWNLOAD_URL} and ensure it is in your PATH or "
        "installed to C:\\Program Files\\CloudCompare\\"
    )


def _run_cloudcompare(args: list, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a CloudCompare CLI command.

    Args:
        args: Command arguments (without the CloudCompare executable).
        timeout: Max seconds to wait.

    Returns:
        CompletedProcess with stdout and stderr.

    Raises:
        FileNotFoundError: CloudCompare not installed.
        subprocess.TimeoutExpired: Command took too long.
        RuntimeError: CloudCompare returned non-zero exit code.
    """
    cc_path = find_cloudcompare()
    cmd = [cc_path, "-SILENT"] + args

    log.info(f"Running CloudCompare: {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        log.error(f"CloudCompare stderr: {result.stderr}")
        raise RuntimeError(
            f"CloudCompare exited with code {result.returncode}: {result.stderr}"
        )

    return result


def _parse_bbox_from_info(stdout: str) -> dict:
    """Parse bounding box from CloudCompare info output."""
    bbox = {}
    # CloudCompare prints bounding box as:
    # Bounding box: [{min_x},{min_y},{min_z}] - [{max_x},{max_y},{max_z}]
    match = re.search(
        r"Bounding box:\s*\[([^]]+)\]\s*-\s*\[([^]]+)\]",
        stdout,
    )
    if match:
        mins = [float(v.strip()) for v in match.group(1).split(",")]
        maxs = [float(v.strip()) for v in match.group(2).split(",")]
        bbox = {
            "min_x": mins[0], "min_y": mins[1], "min_z": mins[2],
            "max_x": maxs[0], "max_y": maxs[1], "max_z": maxs[2],
        }
    return bbox


def _parse_point_count(stdout: str) -> int:
    """Parse point count from CloudCompare output."""
    match = re.search(r"(\d+)\s+point", stdout, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0


# ---------------------------------------------------------------------------
# 1. Volume Calculation via 2.5D Grid
# ---------------------------------------------------------------------------

def calculate_volume(
    input_cloud: str,
    grid_step: float = 0.5,
    ground_level: str = "lowest",
    output_dir: str = None,
) -> dict:
    """Calculate volume of a point cloud using CloudCompare 2.5D volume grid.

    Args:
        input_cloud: Path to .las/.laz/.ply point cloud file.
        grid_step: Grid cell size in meters (0.5 = 50cm resolution).
        ground_level: 'lowest' to use the cloud's minimum Z as ground,
                      'flat' for z=0, or a float string for a specific value.
        output_dir: Directory for output files. Uses input file's directory
                    if not provided.

    Returns:
        Dictionary with volume_m3, surface_area_m2, grid_step, point_count,
        bbox, and output_report path.
    """
    input_cloud = str(Path(input_cloud).resolve())
    if not os.path.isfile(input_cloud):
        raise FileNotFoundError(f"Input cloud not found: {input_cloud}")

    if output_dir is None:
        output_dir = str(Path(input_cloud).parent)
    os.makedirs(output_dir, exist_ok=True)

    report_path = os.path.join(output_dir, "volume_report.txt")

    # Build CLI arguments
    args = [
        "-O", input_cloud,
        "-VOLUME",
        "-GRID_STEP", str(grid_step),
    ]

    # Ground level handling
    if ground_level == "flat":
        args.extend(["-CONST_HEIGHT", "0"])
    elif ground_level != "lowest":
        try:
            level = float(ground_level)
            args.extend(["-CONST_HEIGHT", str(level)])
        except (ValueError, TypeError):
            pass  # default to lowest

    result = _run_cloudcompare(args)
    stdout = result.stdout + result.stderr  # CC sometimes outputs to stderr

    # Parse volume from output
    volume = 0.0
    surface_area = 0.0

    # CloudCompare volume output patterns
    vol_match = re.search(r"Volume:\s*([-\d.eE+]+)", stdout)
    if vol_match:
        volume = float(vol_match.group(1))

    area_match = re.search(r"Surface:\s*([-\d.eE+]+)", stdout)
    if area_match:
        surface_area = float(area_match.group(1))

    # Also check for "Added volume" / "Removed volume" pattern
    added_match = re.search(r"Added volume:\s*([-\d.eE+]+)", stdout)
    removed_match = re.search(r"Removed volume:\s*([-\d.eE+]+)", stdout)
    if added_match and not vol_match:
        volume = float(added_match.group(1))
    if removed_match:
        volume = volume - float(removed_match.group(1))

    point_count = _parse_point_count(stdout)
    bbox = _parse_bbox_from_info(stdout)

    # Write report file
    with open(report_path, "w") as f:
        f.write(f"CloudCompare Volume Report\n")
        f.write(f"{'=' * 40}\n")
        f.write(f"Input: {input_cloud}\n")
        f.write(f"Grid Step: {grid_step} m\n")
        f.write(f"Ground Level: {ground_level}\n")
        f.write(f"Volume: {volume:.4f} m3\n")
        f.write(f"Surface Area: {surface_area:.4f} m2\n")
        f.write(f"Point Count: {point_count}\n")
        if bbox:
            f.write(f"Bounding Box: {json.dumps(bbox, indent=2)}\n")
        f.write(f"\nRaw Output:\n{stdout}\n")

    log.info(f"Volume calculation complete: {volume:.2f} m3")

    return {
        "volume_m3": volume,
        "surface_area_m2": surface_area,
        "grid_step": grid_step,
        "point_count": point_count,
        "bbox": bbox,
        "output_report": report_path,
    }


# ---------------------------------------------------------------------------
# 2. M3C2 Distance Computation
# ---------------------------------------------------------------------------

def _write_m3c2_params(
    normal_scale: float,
    projection_scale: float,
    output_path: str,
) -> str:
    """Generate an M3C2 parameters file for CloudCompare.

    Args:
        normal_scale: Scale for normal estimation (diameter of support sphere).
        projection_scale: Scale for projection cylinder (search radius).
        output_path: Where to write the params file.

    Returns:
        Path to the params file.
    """
    params = (
        f"[General]\n"
        f"ExportDensityAtProjScale=false\n"
        f"ExportStdDevInfo=true\n"
        f"UseMedian=false\n"
        f"MaxThreadCount=0\n"
        f"\n"
        f"[Normal]\n"
        f"NormalMode=0\n"
        f"NormalScale={normal_scale}\n"
        f"PreferredOri=4\n"
        f"UseCorePoints=false\n"
        f"\n"
        f"[Projection]\n"
        f"ProjScale={projection_scale}\n"
        f"SearchDepth={projection_scale * 3}\n"
    )
    with open(output_path, "w") as f:
        f.write(params)
    return output_path


def compute_m3c2_distance(
    cloud1: str,
    cloud2: str,
    normal_scale: float = 1.0,
    projection_scale: float = 1.0,
    output_dir: str = None,
) -> dict:
    """Compute M3C2 distance between two point clouds.

    M3C2 is better than cloud-to-cloud (C2C) distance for noisy drone
    data because it accounts for surface roughness and registration error.

    Args:
        cloud1: Path to reference point cloud (.las/.laz/.ply).
        cloud2: Path to compared point cloud.
        normal_scale: Normal estimation scale (meters). Larger values
                      smooth more for noisy data.
        projection_scale: Projection cylinder radius (meters).
        output_dir: Where to write results. Uses cloud1's directory
                    if not provided.

    Returns:
        Dictionary with mean_distance, std_distance, output_cloud path,
        and point_count.
    """
    cloud1 = str(Path(cloud1).resolve())
    cloud2 = str(Path(cloud2).resolve())

    for p in [cloud1, cloud2]:
        if not os.path.isfile(p):
            raise FileNotFoundError(f"Point cloud not found: {p}")

    if output_dir is None:
        output_dir = str(Path(cloud1).parent)
    os.makedirs(output_dir, exist_ok=True)

    # Write M3C2 params to temp file
    params_path = os.path.join(output_dir, "m3c2_params.txt")
    _write_m3c2_params(normal_scale, projection_scale, params_path)

    args = [
        "-O", cloud1,
        "-O", cloud2,
        "-M3C2", params_path,
        "-C_EXPORT_FMT", "LAS",
        "-SAVE_CLOUDS",
    ]

    result = _run_cloudcompare(args)
    stdout = result.stdout + result.stderr

    # Parse M3C2 results
    mean_distance = 0.0
    std_distance = 0.0
    point_count = 0

    mean_match = re.search(r"Mean dist(?:ance)?:\s*([-\d.eE+]+)", stdout)
    if mean_match:
        mean_distance = float(mean_match.group(1))

    std_match = re.search(r"Std\.?\s*dev\.?:\s*([-\d.eE+]+)", stdout)
    if std_match:
        std_distance = float(std_match.group(1))

    point_count = _parse_point_count(stdout)

    # Find output cloud file (CloudCompare appends _M3C2 to filename)
    output_cloud = ""
    cloud1_stem = Path(cloud1).stem
    for f in Path(output_dir).iterdir():
        if "M3C2" in f.name.upper() and f.suffix.lower() in (".las", ".laz", ".ply"):
            output_cloud = str(f)
            break

    # Also check in CloudCompare's default output location (same dir as input)
    if not output_cloud:
        input_dir = Path(cloud1).parent
        for f in input_dir.iterdir():
            if "M3C2" in f.name.upper() and f.suffix.lower() in (".las", ".laz", ".ply"):
                output_cloud = str(f)
                break

    log.info(f"M3C2 complete: mean={mean_distance:.3f}, std={std_distance:.3f}")

    return {
        "mean_distance": mean_distance,
        "std_distance": std_distance,
        "output_cloud": output_cloud,
        "point_count": point_count,
    }


# ---------------------------------------------------------------------------
# 3. Point Cloud Info and Stats
# ---------------------------------------------------------------------------

def get_cloud_info(input_cloud: str) -> dict:
    """Get basic point cloud statistics using CloudCompare CLI.

    For .ply files, also attempts direct header parsing for fast results
    without needing CloudCompare installed.

    Args:
        input_cloud: Path to .las/.laz/.ply point cloud file.

    Returns:
        Dictionary with point_count, bbox, dimensions (x/y/z in meters),
        file_size_mb, and format.
    """
    input_cloud = str(Path(input_cloud).resolve())
    if not os.path.isfile(input_cloud):
        raise FileNotFoundError(f"Input cloud not found: {input_cloud}")

    file_size_bytes = os.path.getsize(input_cloud)
    file_size_mb = round(file_size_bytes / (1024 * 1024), 4)
    ext = Path(input_cloud).suffix.lower()

    # Try fast PLY header parse first (no CloudCompare needed)
    if ext == ".ply":
        info = _parse_ply_header(input_cloud)
        if info:
            info["file_size_mb"] = file_size_mb
            info["format"] = "PLY"
            return info

    # Fall back to CloudCompare
    try:
        result = _run_cloudcompare(["-O", input_cloud, "-STAT"])
        stdout = result.stdout + result.stderr

        point_count = _parse_point_count(stdout)
        bbox = _parse_bbox_from_info(stdout)

        dimensions = {}
        if bbox:
            dimensions = {
                "x": round(bbox["max_x"] - bbox["min_x"], 3),
                "y": round(bbox["max_y"] - bbox["min_y"], 3),
                "z": round(bbox["max_z"] - bbox["min_z"], 3),
            }

        return {
            "point_count": point_count,
            "bbox": bbox,
            "dimensions": dimensions,
            "file_size_mb": file_size_mb,
            "format": ext.lstrip(".").upper(),
        }

    except (FileNotFoundError, RuntimeError) as e:
        log.warning(f"CloudCompare unavailable for stats: {e}")
        # Return what we can without CloudCompare
        return {
            "point_count": 0,
            "bbox": {},
            "dimensions": {},
            "file_size_mb": file_size_mb,
            "format": ext.lstrip(".").upper(),
        }


def _parse_ply_header(ply_path: str) -> dict:
    """Parse a PLY file header to extract point count and compute bbox.

    This avoids needing CloudCompare for basic stats on PLY files.

    Returns:
        Dictionary with point_count, bbox, dimensions, or None on failure.
    """
    try:
        with open(ply_path, "rb") as f:
            header_lines = []
            point_count = 0
            is_binary = False
            is_big_endian = False
            prop_names = []
            prop_types = []

            while True:
                line = f.readline()
                if not line:
                    return None
                decoded = line.decode("ascii", errors="replace").strip()
                header_lines.append(decoded)

                if decoded.startswith("format binary_big_endian"):
                    is_binary = True
                    is_big_endian = True
                elif decoded.startswith("format binary_little_endian"):
                    is_binary = True
                elif decoded.startswith("element vertex"):
                    point_count = int(decoded.split()[-1])
                elif decoded.startswith("property"):
                    parts = decoded.split()
                    if len(parts) >= 3:
                        prop_types.append(parts[1])
                        prop_names.append(parts[2])
                elif decoded == "end_header":
                    break

            if point_count == 0:
                return None

            # For small PLY files, read all points and compute bbox
            if point_count <= 5_000_000:
                has_xyz = all(n in prop_names for n in ("x", "y", "z"))
                if not has_xyz:
                    return {"point_count": point_count, "bbox": {}, "dimensions": {}}

                xi = prop_names.index("x")
                yi = prop_names.index("y")
                zi = prop_names.index("z")

                if is_binary:
                    # Compute struct format for one vertex row
                    type_map = {
                        "float": "f", "float32": "f",
                        "double": "d", "float64": "d",
                        "int": "i", "int32": "i",
                        "uint": "I", "uint32": "I",
                        "short": "h", "int16": "h",
                        "ushort": "H", "uint16": "H",
                        "char": "b", "int8": "b",
                        "uchar": "B", "uint8": "B",
                    }
                    fmt_chars = []
                    for pt in prop_types[:len(prop_names)]:
                        fmt_chars.append(type_map.get(pt, "f"))
                    endian = ">" if is_big_endian else "<"
                    fmt = endian + "".join(fmt_chars)
                    row_size = struct.calcsize(fmt)

                    data = f.read(row_size * point_count)
                    if len(data) < row_size * point_count:
                        return {"point_count": point_count, "bbox": {}, "dimensions": {}}

                    xs, ys, zs = [], [], []
                    for i in range(point_count):
                        offset = i * row_size
                        row = struct.unpack_from(fmt, data, offset)
                        xs.append(row[xi])
                        ys.append(row[yi])
                        zs.append(row[zi])

                    bbox = {
                        "min_x": min(xs), "min_y": min(ys), "min_z": min(zs),
                        "max_x": max(xs), "max_y": max(ys), "max_z": max(zs),
                    }
                else:
                    # ASCII PLY
                    xs, ys, zs = [], [], []
                    for _ in range(point_count):
                        line = f.readline().decode("ascii", errors="replace").strip()
                        vals = line.split()
                        if len(vals) > max(xi, yi, zi):
                            xs.append(float(vals[xi]))
                            ys.append(float(vals[yi]))
                            zs.append(float(vals[zi]))

                    if not xs:
                        return {"point_count": point_count, "bbox": {}, "dimensions": {}}

                    bbox = {
                        "min_x": min(xs), "min_y": min(ys), "min_z": min(zs),
                        "max_x": max(xs), "max_y": max(ys), "max_z": max(zs),
                    }

                dimensions = {
                    "x": round(bbox["max_x"] - bbox["min_x"], 3),
                    "y": round(bbox["max_y"] - bbox["min_y"], 3),
                    "z": round(bbox["max_z"] - bbox["min_z"], 3),
                }

                return {
                    "point_count": point_count,
                    "bbox": bbox,
                    "dimensions": dimensions,
                }

            # Large file, just return point count from header
            return {"point_count": point_count, "bbox": {}, "dimensions": {}}

    except Exception as e:
        log.debug(f"PLY header parse failed: {e}")
        return None


# ---------------------------------------------------------------------------
# 4. Volume Report (PDF)
# ---------------------------------------------------------------------------

def generate_volume_report(
    volume_result: dict,
    site_name: str,
    output_dir: str,
    date: str = None,
) -> str:
    """Generate a branded PDF volume measurement report.

    Uses Sentinel Aerial Inspections branding with professional formatting.

    Args:
        volume_result: Dictionary from calculate_volume() or compatible dict
                       with at minimum volume_m3 key.
        site_name: Name of the job site.
        output_dir: Where to write the PDF.
        date: Date string (defaults to today).

    Returns:
        Path to the generated PDF file.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    os.makedirs(output_dir, exist_ok=True)
    safe_name = re.sub(r"[^\w\-]", "_", site_name)
    pdf_path = os.path.join(output_dir, f"volume_report_{safe_name}_{date}.pdf")

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=1 * inch,
        rightMargin=1 * inch,
    )

    styles = getSampleStyleSheet()

    # Brand colors
    sai_dark = colors.HexColor("#1a1a2e")
    sai_accent = colors.HexColor("#e94560")
    sai_light = colors.HexColor("#f5f5f5")

    header_style = ParagraphStyle(
        "SAIHeader",
        parent=styles["Title"],
        fontSize=22,
        textColor=sai_dark,
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "SAISubtitle",
        parent=styles["Heading2"],
        fontSize=14,
        textColor=sai_accent,
        spaceAfter=12,
    )
    section_style = ParagraphStyle(
        "SAISection",
        parent=styles["Heading3"],
        fontSize=12,
        textColor=sai_dark,
        spaceBefore=18,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "SAIBody",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        spaceAfter=6,
    )
    note_style = ParagraphStyle(
        "SAINote",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.grey,
        leading=10,
        spaceAfter=4,
    )

    elements = []

    # Header
    elements.append(Paragraph("Sentinel Aerial Inspections", header_style))
    elements.append(Paragraph("Volume Measurement Report", subtitle_style))
    elements.append(Spacer(1, 12))

    # Site info table
    site_data = [
        ["Site Name", site_name],
        ["Report Date", date],
        ["Prepared By", "Sentinel Aerial Inspections"],
    ]
    site_table = Table(site_data, colWidths=[2 * inch, 4 * inch])
    site_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), sai_light),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("PADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(site_table)
    elements.append(Spacer(1, 20))

    # Volume results section
    elements.append(Paragraph("Volume Calculation Results", section_style))

    volume_m3 = volume_result.get("volume_m3", 0)
    volume_yd3 = volume_m3 * 1.30795  # cubic meters to cubic yards

    results_data = [
        ["Parameter", "Value"],
        ["Volume", f"{volume_m3:,.2f} m\u00b3 ({volume_yd3:,.2f} yd\u00b3)"],
    ]

    if volume_result.get("surface_area_m2"):
        area = volume_result["surface_area_m2"]
        results_data.append(["Surface Area", f"{area:,.2f} m\u00b2"])

    if volume_result.get("grid_step"):
        results_data.append(["Grid Resolution", f"{volume_result['grid_step']} m"])

    if volume_result.get("point_count"):
        results_data.append(["Points Processed", f"{volume_result['point_count']:,}"])

    results_table = Table(results_data, colWidths=[2.5 * inch, 3.5 * inch])
    results_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), sai_dark),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("PADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("BACKGROUND", (0, 1), (0, -1), sai_light),
        ("ALIGN", (1, 1), (1, -1), "RIGHT"),
    ]))
    elements.append(results_table)
    elements.append(Spacer(1, 16))

    # Bounding box section (if available)
    bbox = volume_result.get("bbox", {})
    if bbox:
        elements.append(Paragraph("Spatial Extent", section_style))

        dx = bbox.get("max_x", 0) - bbox.get("min_x", 0)
        dy = bbox.get("max_y", 0) - bbox.get("min_y", 0)
        dz = bbox.get("max_z", 0) - bbox.get("min_z", 0)

        bbox_data = [
            ["Axis", "Min", "Max", "Extent"],
            [
                "X (Easting)",
                f"{bbox.get('min_x', 0):,.2f}",
                f"{bbox.get('max_x', 0):,.2f}",
                f"{dx:,.2f} m",
            ],
            [
                "Y (Northing)",
                f"{bbox.get('min_y', 0):,.2f}",
                f"{bbox.get('max_y', 0):,.2f}",
                f"{dy:,.2f} m",
            ],
            [
                "Z (Elevation)",
                f"{bbox.get('min_z', 0):,.2f}",
                f"{bbox.get('max_z', 0):,.2f}",
                f"{dz:,.2f} m",
            ],
        ]
        bbox_table = Table(
            bbox_data,
            colWidths=[1.5 * inch, 1.5 * inch, 1.5 * inch, 1.5 * inch],
        )
        bbox_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), sai_dark),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("PADDING", (0, 0), (-1, -1), 6),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ]))
        elements.append(bbox_table)
        elements.append(Spacer(1, 16))

    # Methodology note
    elements.append(Paragraph("Methodology", section_style))
    elements.append(Paragraph(
        "Volume was calculated using a 2.5D grid projection method. The point cloud "
        "was projected onto a regular grid and the volume between the surface and the "
        "reference ground plane was integrated across all grid cells. This method is "
        "suitable for stockpile measurements, earthwork volumes, and terrain analysis "
        "from drone-captured aerial survey data.",
        body_style,
    ))
    elements.append(Paragraph(
        "Processing was performed with CloudCompare (www.danielgm.net/cc). "
        "Point cloud data was captured and processed using photogrammetric "
        "reconstruction from overlapping aerial photographs.",
        body_style,
    ))

    # Disclaimer
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(
        "This report is provided for informational purposes. Volume calculations "
        "from aerial survey data have typical accuracy of 2-5% depending on ground "
        "control, flight altitude, and surface conditions. For certified survey-grade "
        "measurements, consult a licensed surveyor.",
        note_style,
    ))
    elements.append(Paragraph(
        "Sentinel Aerial Inspections | Hampton Roads, Virginia | sentinelaerial.com",
        note_style,
    ))

    doc.build(elements)
    log.info(f"Volume report generated: {pdf_path}")
    return pdf_path
