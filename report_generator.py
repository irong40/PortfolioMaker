"""
Sentinel Portfolio Maker — Report Generator

Generates branded PDF reports per job type using ReportLab.
Follows the pattern established by vegetation_report.py in drone-pipeline.
"""

import os
import logging
from datetime import datetime
from pathlib import Path

# ─── REPORTLAB IMPORT ──────────────────────────────────────────────────────

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.colors import HexColor, white, black
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, HRFlowable,
    )
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

# ─── BRAND COLORS ──────────────────────────────────────────────────────────

if REPORTLAB_AVAILABLE:
    SENTINEL_PURPLE = HexColor("#5B2C6F")
    SENTINEL_DARK = HexColor("#1A0A2E")
    SENTINEL_MID = HexColor("#AF7AC5")
    SENTINEL_LIGHT = HexColor("#F4ECF7")
    ACCENT_GOLD = HexColor("#F4D03F")
    LIGHT_GREY = HexColor("#D3D3D3")

FOOTER_TEXT = (
    "Sentinel Aerial Inspections  |  FAA Part 107 Certified  |  "
    "Veteran-Owned Small Business  |  Faith & Harmony LLC"
)

# ─── REPORT TYPES REGISTRY ────────────────────────────────────────────────

REPORT_TYPES = {
    "construction_progress": "Construction Progress Report",
    "property_survey": "Property Survey Report",
    "roof_inspection": "Roof Inspection Report",
    "structures": "Structural Inspection Report",
    "vegetation": "Vegetation Analysis Report",
    "real_estate": "Real Estate Property Report",
    "gaussian_splat": "Gaussian Splat Model Report",
}

# ─── SHARED STYLES ─────────────────────────────────────────────────────────

def _get_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        "CoverTitle", parent=styles["Title"],
        fontSize=28, textColor=white, alignment=TA_CENTER,
        spaceAfter=12,
    ))
    styles.add(ParagraphStyle(
        "CoverSubtitle", parent=styles["Normal"],
        fontSize=14, textColor=SENTINEL_MID, alignment=TA_CENTER,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "SectionHeader", parent=styles["Heading2"],
        fontSize=14, textColor=SENTINEL_PURPLE,
        spaceBefore=16, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        "SentinelBody", parent=styles["Normal"],
        fontSize=10, leading=14, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "SmallGrey", parent=styles["Normal"],
        fontSize=8, textColor=HexColor("#777777"),
    ))
    return styles


# ─── PAGE TEMPLATE ─────────────────────────────────────────────────────────

def _footer(canvas_obj, doc):
    """Draw footer on every page."""
    canvas_obj.saveState()
    PAGE_W, PAGE_H = letter

    # Footer line
    canvas_obj.setStrokeColor(SENTINEL_MID)
    canvas_obj.setLineWidth(0.5)
    canvas_obj.line(0.75 * inch, 0.5 * inch, PAGE_W - 0.75 * inch, 0.5 * inch)

    # Footer text
    canvas_obj.setFont("Helvetica", 7)
    canvas_obj.setFillColor(HexColor("#777777"))
    canvas_obj.drawCentredString(PAGE_W / 2.0, 0.35 * inch, FOOTER_TEXT)

    # Page number
    canvas_obj.drawRightString(PAGE_W - 0.75 * inch, 0.35 * inch, f"Page {doc.page}")

    canvas_obj.restoreState()


# ─── COVER PAGE ────────────────────────────────────────────────────────────

def _build_cover(elements, styles, data, report_title):
    """Add cover page elements."""
    PAGE_W, PAGE_H = letter

    # Purple header block
    cover_table = Table(
        [[Paragraph("SENTINEL AERIAL INSPECTIONS", styles["CoverSubtitle"]),],
         [Paragraph(report_title, styles["CoverTitle"]),],
         [Paragraph(data.get("site_name", "Site"), styles["CoverSubtitle"]),],
         [Paragraph(data.get("date", ""), styles["CoverSubtitle"]),]],
        colWidths=[PAGE_W - 1.5 * inch],
        rowHeights=[30, 50, 30, 25],
    )
    cover_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SENTINEL_DARK),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 20),
        ("RIGHTPADDING", (0, 0), (-1, -1), 20),
    ]))
    elements.append(Spacer(1, 1.5 * inch))
    elements.append(cover_table)
    elements.append(Spacer(1, 0.5 * inch))


# ─── FLIGHT SUMMARY TABLE ─────────────────────────────────────────────────

def _build_flight_summary(elements, styles, data):
    """Add flight summary section — common to all report types."""
    elements.append(Paragraph("Flight Summary", styles["SectionHeader"]))

    rows = [
        ["Site Name", data.get("site_name", "—")],
        ["Date", data.get("date", "—")],
        ["Platform", data.get("platform", "Unknown")],
        ["Total Photos", str(data.get("total_photos", 0))],
        ["Nadir Photos", str(data.get("nadir_count", 0))],
        ["Oblique Photos", str(data.get("oblique_count", 0))],
    ]

    gps = data.get("gps_bounds")
    if gps:
        lat_span = (gps[1] - gps[0]) * 111139
        lon_span = (gps[3] - gps[2]) * 111139 * 0.87
        rows.append(["GPS Footprint", f"~{lat_span:.0f}m x {lon_span:.0f}m"])
        rows.append(["Coordinates", f"{gps[0]:.6f}, {gps[2]:.6f} to {gps[1]:.6f}, {gps[3]:.6f}"])

    table = Table(rows, colWidths=[2 * inch, 4.5 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), SENTINEL_LIGHT),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, LIGHT_GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 12))


# ─── OUTPUTS TABLE ─────────────────────────────────────────────────────────

def _build_outputs_section(elements, styles, data):
    """List downloaded deliverable files."""
    downloads = data.get("downloads", {})
    if not downloads:
        return

    elements.append(Paragraph("Deliverables", styles["SectionHeader"]))

    rows = [["File", "Size"]]
    for name, path in downloads.items():
        size = "—"
        if path and os.path.exists(path):
            size_mb = os.path.getsize(path) / (1024 * 1024)
            size = f"{size_mb:.1f} MB"
        rows.append([name, size])

    table = Table(rows, colWidths=[4 * inch, 2.5 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SENTINEL_PURPLE),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, LIGHT_GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 12))


# ─── JOB-TYPE SPECIFIC SECTIONS ───────────────────────────────────────────

def _build_construction_sections(elements, styles, data):
    elements.append(Paragraph("Construction Progress", styles["SectionHeader"]))
    elements.append(Paragraph(
        "This report documents site conditions as observed from aerial survey. "
        "Orthomosaic and DSM outputs enable measurement of areas, volumes, and "
        "visual comparison with previous visits for progress tracking.",
        styles["SentinelBody"],
    ))
    elements.append(Paragraph("Progress Tracking", styles["SectionHeader"]))
    elements.append(Paragraph(
        "Compare this visit's orthomosaic with previous dates to identify "
        "construction progress, material staging changes, and site activity. "
        "DSM differencing can quantify earthwork volumes between visits.",
        styles["SentinelBody"],
    ))


def _build_survey_sections(elements, styles, data):
    elements.append(Paragraph("Survey Data", styles["SectionHeader"]))
    elements.append(Paragraph(
        "Property boundaries, area measurements, and elevation data derived from "
        "aerial photogrammetry. Point cloud and DSM/DTM outputs provide "
        "survey-grade topographic information.",
        styles["SentinelBody"],
    ))
    elements.append(Paragraph("Elevation Analysis", styles["SectionHeader"]))
    elements.append(Paragraph(
        "Digital Surface Model (DSM) captures all features including structures "
        "and vegetation. Digital Terrain Model (DTM) represents bare earth "
        "elevation after filtering. Both are provided as GeoTIFF deliverables.",
        styles["SentinelBody"],
    ))


def _build_roof_sections(elements, styles, data):
    elements.append(Paragraph("Roof Condition Assessment", styles["SectionHeader"]))
    elements.append(Paragraph(
        "3D mesh model enables detailed inspection of roof surfaces, flashing, "
        "penetrations, and drainage. Oblique imagery provides close-up views "
        "of areas of concern without requiring physical access.",
        styles["SentinelBody"],
    ))
    elements.append(Paragraph("Observations", styles["SectionHeader"]))
    elements.append(Paragraph(
        "Review the 3D model and orthomosaic for: missing or damaged shingles, "
        "ponding water indicators, flashing condition, debris accumulation, "
        "and HVAC equipment clearance.",
        styles["SentinelBody"],
    ))


def _build_structures_sections(elements, styles, data):
    elements.append(Paragraph("Structural Assessment", styles["SectionHeader"]))
    elements.append(Paragraph(
        "High-resolution 3D model and point cloud provide comprehensive "
        "documentation of structural condition. Measurements can be extracted "
        "from the point cloud for engineering analysis.",
        styles["SentinelBody"],
    ))
    elements.append(Paragraph("Inspection Points", styles["SectionHeader"]))
    elements.append(Paragraph(
        "The 3D model captures all visible surfaces of the structure from "
        "multiple angles. Review for: surface deterioration, deformation, "
        "connection condition, and clearance measurements.",
        styles["SentinelBody"],
    ))


def _build_vegetation_sections(elements, styles, data):
    elements.append(Paragraph("Vegetation Analysis", styles["SectionHeader"]))
    elements.append(Paragraph(
        "Orthomosaic provides the base layer for vegetation analysis. "
        "For full canopy detection, species classification, and health "
        "assessment, run the Path E vegetation pipeline on the orthomosaic output.",
        styles["SentinelBody"],
    ))
    elements.append(Paragraph(
        "Note: Detailed vegetation analysis (canopy detection, NDVI, species "
        "classification) requires the drone-pipeline Path E tools. This report "
        "covers the aerial survey component.",
        styles["SmallGrey"],
    ))


def _build_real_estate_sections(elements, styles, data):
    elements.append(Paragraph("Property Overview", styles["SectionHeader"]))
    elements.append(Paragraph(
        "Aerial imagery and 3D model showcase the property from perspectives "
        "not available from ground level. The orthomosaic provides a "
        "high-resolution bird's eye view suitable for marketing materials.",
        styles["SentinelBody"],
    ))
    elements.append(Paragraph("Showcase Assets", styles["SectionHeader"]))
    elements.append(Paragraph(
        "Deliverables include orthomosaic (overhead map), DSM (elevation "
        "visualization), and 3D textured model. These assets can be used "
        "in listings, virtual tours, and property marketing.",
        styles["SentinelBody"],
    ))


def _build_gaussian_splat_sections(elements, styles, data):
    elements.append(Paragraph("Gaussian Splat Model", styles["SectionHeader"]))
    elements.append(Paragraph(
        "3D Gaussian Splatting is a novel rendering technique that represents "
        "scenes as collections of 3D Gaussians rather than traditional meshes or "
        "point clouds. The result is photorealistic novel-view synthesis with "
        "real-time rendering capability.",
        styles["SentinelBody"],
    ))
    elements.append(Paragraph("Processing Details", styles["SectionHeader"]))
    mipmap = data.get("mipmap_settings", {})
    res_level = mipmap.get("resolution_level", 3)
    decimate = mipmap.get("mesh_decimate_ratio", 0.5)
    elements.append(Paragraph(
        f"Processed via MipMap Desktop with resolution level {res_level} and "
        f"mesh decimation ratio {decimate}. Gaussian Splat outputs include PLY "
        f"point cloud and SOG tile set for web-based viewing.",
        styles["SentinelBody"],
    ))
    elements.append(Paragraph("Deliverables", styles["SectionHeader"]))
    elements.append(Paragraph(
        "The Gaussian Splat PLY file can be loaded in compatible viewers for "
        "interactive 3D exploration. SOG tiles enable streaming playback in "
        "web browsers without downloading the full model.",
        styles["SentinelBody"],
    ))


_SECTION_BUILDERS = {
    "construction_progress": _build_construction_sections,
    "property_survey": _build_survey_sections,
    "roof_inspection": _build_roof_sections,
    "structures": _build_structures_sections,
    "vegetation": _build_vegetation_sections,
    "real_estate": _build_real_estate_sections,
    "gaussian_splat": _build_gaussian_splat_sections,
}


# ─── METHODOLOGY ───────────────────────────────────────────────────────────

def _build_methodology(elements, styles, data):
    elements.append(Paragraph("Methodology", styles["SectionHeader"]))
    engine = data.get("engine", "nodeodm")
    if engine == "mipmap":
        processing_text = (
            "Photogrammetric processing and Gaussian Splat generation were "
            "performed using MipMap Desktop with optimized VRAM settings."
        )
    else:
        processing_text = (
            "Photogrammetric processing was performed using OpenDroneMap via "
            "NodeODM with split-merge enabled for memory-efficient reconstruction."
        )
    elements.append(Paragraph(
        "Aerial data was collected using a consumer drone platform with "
        "integrated GPS and gimbal-stabilized camera. Photos were classified "
        f"by gimbal pitch angle (nadir: straight down; oblique: angled). "
        f"{processing_text}",
        styles["SentinelBody"],
    ))
    elements.append(Paragraph(
        "This report was generated automatically by Sentinel Portfolio Maker. "
        "All measurements are approximate and derived from photogrammetric "
        "reconstruction. For survey-grade accuracy, ground control points "
        "and professional survey equipment should be used.",
        styles["SmallGrey"],
    ))


# ─── MAIN ENTRY POINT ─────────────────────────────────────────────────────

def generate_report(report_type, data, output_dir):
    """Generate a branded PDF report for the given job type.

    Args:
        report_type: Key from REPORT_TYPES (e.g., "construction_progress")
        data: Dict with keys: site_name, date, job_type, total_photos,
              nadir_count, oblique_count, platform, gps_bounds,
              ortho_path, dsm_path, downloads
        output_dir: Directory to write the PDF

    Returns:
        Dict with "pdf_path" key on success, or None on failure.
    """
    log = logging.getLogger(__name__)

    if report_type not in REPORT_TYPES:
        log.error(f"Unknown report type: {report_type}")
        return None

    if not REPORTLAB_AVAILABLE:
        log.error("reportlab not installed — cannot generate PDF report")
        return None

    report_title = REPORT_TYPES[report_type]
    site_name = data.get("site_name", "Site")
    date_str = data.get("date", datetime.now().strftime("%Y-%m-%d"))
    safe_name = site_name.replace(" ", "_").replace("/", "_").replace("\\", "_")
    pdf_filename = f"Sentinel_{safe_name}_{report_type}_{date_str}.pdf"
    pdf_path = os.path.join(output_dir, pdf_filename)

    os.makedirs(output_dir, exist_ok=True)

    try:
        styles = _get_styles()
        elements = []

        # Cover
        _build_cover(elements, styles, data, report_title)
        elements.append(PageBreak())

        # Flight summary (all report types)
        _build_flight_summary(elements, styles, data)

        # Outputs section
        _build_outputs_section(elements, styles, data)

        # Job-type specific sections
        section_builder = _SECTION_BUILDERS.get(report_type)
        if section_builder:
            section_builder(elements, styles, data)

        # Methodology (all report types)
        elements.append(PageBreak())
        _build_methodology(elements, styles, data)

        # Build PDF
        doc = SimpleDocTemplate(
            pdf_path,
            pagesize=letter,
            leftMargin=0.75 * inch,
            rightMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
        )
        doc.build(elements, onFirstPage=_footer, onLaterPages=_footer)

        log.info(f"Report saved: {pdf_path}")
        return {"pdf_path": pdf_path}

    except Exception as e:
        log.error(f"Report generation failed: {e}")
        return None
