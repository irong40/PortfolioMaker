"""
Sortie — Report Generator

Template-driven PDF report generation. Each job type has a dedicated
template (report_templates.py) defining sections, AI prompts, and fallbacks.
Supports AI-generated narratives (Gemini Vision) and embedded photo thumbnails.
"""

import os
import json
import logging
from datetime import datetime

# ─── REPORTLAB IMPORT ──────────────────────────────────────────────────────

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, Image as RLImage, KeepTogether,
    )
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

from report_templates import get_template, TEMPLATES

# ─── BRAND COLORS ──────────────────────────────────────────────────────────

if REPORTLAB_AVAILABLE:
    SENTINEL_PURPLE = HexColor("#5B2C6F")
    SENTINEL_DARK = HexColor("#1A0A2E")
    SENTINEL_MID = HexColor("#AF7AC5")
    SENTINEL_LIGHT = HexColor("#F4ECF7")
    ACCENT_GOLD = HexColor("#F4D03F")
    LIGHT_GREY = HexColor("#D3D3D3")
    SEVERITY_COLORS = {
        "major": HexColor("#C0392B"),
        "moderate": HexColor("#E67E22"),
        "minor": HexColor("#F4D03F"),
        "info": HexColor("#2ECC71"),
    }
    STATUS_COLORS = {
        "pass": HexColor("#2ECC71"),
        "fail": HexColor("#C0392B"),
        "warning": HexColor("#E67E22"),
    }

FOOTER_TEXT = (
    "Sentinel Aerial Inspections  |  FAA Part 107 Certified  |  "
    "Veteran-Owned Small Business  |  Faith & Harmony LLC"
)

REPORT_TYPES = {k: v.title for k, v in TEMPLATES.items()}

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
        "SubHeader", parent=styles["Heading3"],
        fontSize=11, textColor=SENTINEL_DARK,
        spaceBefore=10, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        "SentinelBody", parent=styles["Normal"],
        fontSize=10, leading=14, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "SmallGrey", parent=styles["Normal"],
        fontSize=8, textColor=HexColor("#777777"),
    ))
    styles.add(ParagraphStyle(
        "Caption", parent=styles["Normal"],
        fontSize=8, textColor=HexColor("#555555"),
        alignment=TA_CENTER, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        "ExecSummary", parent=styles["Normal"],
        fontSize=11, leading=16, spaceAfter=10,
    ))
    styles.add(ParagraphStyle(
        "RatingLarge", parent=styles["Normal"],
        fontSize=18, textColor=SENTINEL_PURPLE,
        alignment=TA_CENTER, spaceBefore=8, spaceAfter=8,
    ))
    return styles


# ─── PAGE TEMPLATE ─────────────────────────────────────────────────────────

def _footer(canvas_obj, doc):
    canvas_obj.saveState()
    PAGE_W, PAGE_H = letter
    canvas_obj.setStrokeColor(SENTINEL_MID)
    canvas_obj.setLineWidth(0.5)
    canvas_obj.line(0.75 * inch, 0.5 * inch, PAGE_W - 0.75 * inch, 0.5 * inch)
    canvas_obj.setFont("Helvetica", 7)
    canvas_obj.setFillColor(HexColor("#777777"))
    canvas_obj.drawCentredString(PAGE_W / 2.0, 0.35 * inch, FOOTER_TEXT)
    canvas_obj.drawRightString(PAGE_W - 0.75 * inch, 0.35 * inch, f"Page {doc.page}")
    canvas_obj.restoreState()


# ─── SECTION RENDERERS ───────────────────────────────────────────────────
# Each renderer handles a specific section key or falls through to generic.

def _render_cover(elements, styles, data, template):
    PAGE_W, _ = letter
    cover_table = Table(
        [[Paragraph("SENTINEL AERIAL INSPECTIONS", styles["CoverSubtitle"])],
         [Paragraph(template.title, styles["CoverTitle"])],
         [Paragraph(data.get("site_name", "Site"), styles["CoverSubtitle"])],
         [Paragraph(data.get("date", ""), styles["CoverSubtitle"])]],
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
    elements.append(PageBreak())


def _render_executive_summary(elements, styles, section, ai_data):
    summary = ai_data.get("executive_summary", "") if ai_data else ""
    if not summary:
        return

    elements.append(Paragraph(section.title, styles["SectionHeader"]))
    summary_table = Table(
        [[Paragraph(summary, styles["ExecSummary"])]],
        colWidths=[6.5 * inch],
    )
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SENTINEL_LIGHT),
        ("BOX", (0, 0), (-1, -1), 1.5, SENTINEL_PURPLE),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 12))


def _render_flight_summary(elements, styles, data):
    elements.append(Paragraph("Flight Summary", styles["SectionHeader"]))
    rows = [
        ["Site Name", data.get("site_name", "\u2014")],
        ["Date", data.get("date", "\u2014")],
        ["Platform", data.get("platform", "Unknown")],
        ["Total Photos", str(data.get("total_photos", 0))],
        ["Nadir Photos", str(data.get("nadir_count", 0))],
        ["Oblique Photos", str(data.get("oblique_count", 0))],
    ]
    gps = data.get("gps_bounds")
    if gps:
        lat_span = (gps[1] - gps[0]) * 111139
        lon_span = (gps[3] - gps[2]) * 111139 * 0.87
        rows.append(["GPS Footprint", f"~{lat_span:.0f}m \u00d7 {lon_span:.0f}m"])
        rows.append(["Coordinates",
                      f"{gps[0]:.6f}, {gps[2]:.6f} to {gps[1]:.6f}, {gps[3]:.6f}"])

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


def _render_deliverables(elements, styles, data):
    downloads = data.get("downloads", {})
    if not downloads:
        return
    elements.append(Paragraph("Deliverables", styles["SectionHeader"]))
    rows = [["File", "Size"]]
    for name, path in downloads.items():
        size = "\u2014"
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


def _render_methodology(elements, styles, data, has_ai):
    elements.append(Paragraph("Methodology", styles["SectionHeader"]))
    engine = data.get("engine", "nodeodm")
    if engine == "mipmap":
        proc = ("Photogrammetric processing and Gaussian Splat generation were "
                "performed using MipMap Desktop with optimized VRAM settings.")
    else:
        proc = ("Photogrammetric processing was performed using OpenDroneMap via "
                "NodeODM with split-merge enabled for memory-efficient reconstruction.")
    elements.append(Paragraph(
        "Aerial data was collected using a consumer drone platform with "
        "integrated GPS and gimbal-stabilized camera. Photos were classified "
        f"by gimbal pitch angle (nadir: straight down; oblique: angled). {proc}",
        styles["SentinelBody"],
    ))
    if has_ai:
        elements.append(Paragraph(
            "Site observations were generated using AI-assisted photo analysis "
            "(Gemini Vision). Findings are based on visual inspection of "
            "representative aerial photographs and should be verified by a "
            "qualified professional before acting on any recommendations.",
            styles["SmallGrey"],
        ))
    elements.append(Paragraph(
        "This report was generated automatically by Sortie. "
        "All measurements are approximate and derived from photogrammetric "
        "reconstruction. For survey-grade accuracy, ground control points "
        "and professional survey equipment should be used.",
        styles["SmallGrey"],
    ))


def _render_photo_grid(elements, styles, section, images):
    thumbs = images.get("photo_thumbs", []) if images else []
    if not thumbs:
        return
    elements.append(Paragraph(section.title, styles["SectionHeader"]))
    grid_rows = []
    for i in range(0, len(thumbs), 2):
        row_imgs = []
        row_caps = []
        for j in range(2):
            idx = i + j
            if idx < len(thumbs):
                path, caption = thumbs[idx]
                try:
                    img = RLImage(path, width=3.0 * inch, height=2.25 * inch,
                                  kind="proportional")
                    row_imgs.append(img)
                    row_caps.append(Paragraph(caption, styles["Caption"]))
                except Exception as e:
                    logging.getLogger(__name__).warning(f"Failed to embed photo {path}: {e}")
                    row_imgs.append("")
                    row_caps.append("")
            else:
                row_imgs.append("")
                row_caps.append("")
        grid_rows.append(row_imgs)
        grid_rows.append(row_caps)
    if grid_rows:
        table = Table(grid_rows, colWidths=[3.25 * inch, 3.25 * inch])
        table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 12))


def _render_ortho_preview(elements, styles, section, images):
    if not images:
        return
    ortho = images.get("ortho_preview")
    if not ortho:
        return
    elements.append(Paragraph(section.title, styles["SectionHeader"]))
    try:
        img = RLImage(ortho, width=6.0 * inch, height=4.5 * inch, kind="proportional")
        elements.append(img)
        elements.append(Paragraph("Orthomosaic Overview", styles["Caption"]))
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to embed ortho preview: {e}")
    elements.append(Spacer(1, 12))


def _render_dsm_preview(elements, styles, section, images):
    if not images:
        return
    dsm = images.get("dsm_preview")
    if not dsm:
        return
    elements.append(Paragraph(section.title, styles["SectionHeader"]))
    try:
        img = RLImage(dsm, width=6.0 * inch, height=4.5 * inch, kind="proportional")
        elements.append(img)
        elements.append(Paragraph(
            "Digital Surface Model (elevation: blue=low, red=high)", styles["Caption"]))
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to embed DSM preview: {e}")
    elements.append(Spacer(1, 12))


def _render_findings_table(elements, styles, section, ai_data):
    """Render a list of observations/findings as a severity-coded table."""
    items = ai_data.get(section.ai_field, []) if ai_data else []
    if not items:
        return False

    elements.append(Paragraph(section.title, styles["SectionHeader"]))
    rows = [["#", "Severity", "Finding", "Location"]]
    for i, obs in enumerate(items, 1):
        if isinstance(obs, dict):
            severity = obs.get("severity", obs.get("status", "info")).title()
            finding = obs.get("finding", obs.get("item", obs.get("type", str(obs))))
            location = obs.get("location", obs.get("note", "\u2014"))
        else:
            severity = "Info"
            finding = str(obs)
            location = "\u2014"
        rows.append([str(i), severity, finding, location])

    col_widths = [0.4 * inch, 0.8 * inch, 3.5 * inch, 1.8 * inch]
    table = Table(rows, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), SENTINEL_PURPLE),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, LIGHT_GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for i, item in enumerate(items, 1):
        if isinstance(item, dict):
            sev = item.get("severity", item.get("status", "info")).lower()
        else:
            sev = "info"
        color = SEVERITY_COLORS.get(sev, STATUS_COLORS.get(sev, SEVERITY_COLORS["info"]))
        style_cmds.append(("TEXTCOLOR", (1, i), (1, i), color))
        style_cmds.append(("FONTNAME", (1, i), (1, i), "Helvetica-Bold"))
    table.setStyle(TableStyle(style_cmds))
    elements.append(table)
    elements.append(Spacer(1, 12))
    return True


def _render_checklist_table(elements, styles, section, ai_data):
    """Render a pass/fail/warning checklist table."""
    field_data = ai_data.get(section.ai_field, {}) if ai_data else {}
    items = field_data.get("findings", []) if isinstance(field_data, dict) else []
    if not items:
        return False

    elements.append(Paragraph(section.title, styles["SectionHeader"]))
    rows = [["Item", "Status", "Notes"]]
    for item in items:
        if isinstance(item, dict):
            rows.append([
                item.get("item", ""),
                item.get("status", "").title(),
                item.get("note", ""),
            ])

    table = Table(rows, colWidths=[2.5 * inch, 1.0 * inch, 3.0 * inch], repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), SENTINEL_PURPLE),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, LIGHT_GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for i, item in enumerate(items, 1):
        if isinstance(item, dict):
            status = item.get("status", "").lower()
            color = STATUS_COLORS.get(status, HexColor("#333333"))
            style_cmds.append(("TEXTCOLOR", (1, i), (1, i), color))
            style_cmds.append(("FONTNAME", (1, i), (1, i), "Helvetica-Bold"))
    table.setStyle(TableStyle(style_cmds))
    elements.append(table)
    elements.append(Spacer(1, 12))
    return True


def _render_recommendations(elements, styles, section, ai_data):
    recs = ai_data.get("recommendations", []) if ai_data else []
    if not recs:
        return False
    elements.append(Paragraph(section.title, styles["SectionHeader"]))
    for i, rec in enumerate(recs, 1):
        elements.append(Paragraph(f"<b>{i}.</b> {rec}", styles["SentinelBody"]))
    elements.append(Spacer(1, 8))
    return True


def _render_volume_comparison(elements, styles, data, images):
    """Render DSM comparison results — cut/fill volumes and change map."""
    pc = data.get("pc_results", {})
    dsm_comp = pc.get("dsm_comparison")
    if not dsm_comp:
        return

    prev_date = pc.get("previous_date", "previous visit")
    elements.append(Paragraph("Volume Comparison", styles["SectionHeader"]))
    elements.append(Paragraph(
        f"Elevation change analysis comparing current survey with {prev_date}.",
        styles["SentinelBody"],
    ))

    rows = [
        ["Fill Volume (added)", f"{dsm_comp['fill_volume_m3']:,.0f} m\u00b3"],
        ["Cut Volume (removed)", f"{dsm_comp['cut_volume_m3']:,.0f} m\u00b3"],
        ["Net Volume Change", f"{dsm_comp['net_volume_m3']:,.0f} m\u00b3"],
        ["Mean Elevation Change", f"{dsm_comp['mean_change_m']:.2f} m"],
        ["Max Rise", f"{dsm_comp['max_rise_m']:.2f} m"],
        ["Max Drop", f"{dsm_comp['max_drop_m']:.2f} m"],
        ["Changed Area", f"{dsm_comp['changed_area_pct']:.1f}%"],
    ]

    table = Table(rows, colWidths=[2.5 * inch, 4.0 * inch])
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
    elements.append(Spacer(1, 8))

    # Embed change map image if available
    change_map = (images or {}).get("change_map") or pc.get("change_map_image")
    if change_map and os.path.exists(change_map):
        try:
            img = RLImage(change_map, width=6.0 * inch, height=4.5 * inch,
                          kind="proportional")
            elements.append(img)
            elements.append(Paragraph(
                f"Elevation change map: blue=cut (lowered), red=fill (raised), "
                f"white=no change. Compared with {prev_date}.",
                styles["Caption"]))
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to embed change map: {e}")

    elements.append(Spacer(1, 12))


def _render_mesh_stats(elements, styles, data):
    """Render mesh statistics table."""
    pc = data.get("pc_results", {})
    stats = pc.get("mesh_stats")
    if not stats:
        return

    elements.append(Paragraph("3D Model Statistics", styles["SectionHeader"]))

    rows = [
        ["Vertices", f"{stats['vertices']:,}"],
        ["Triangles", f"{stats['triangles']:,}"],
        ["Dimensions (X \u00d7 Y \u00d7 Z)",
         f"{stats['extent_x']:.1f} \u00d7 {stats['extent_y']:.1f} \u00d7 {stats['extent_z']:.1f} m"],
        ["Surface Area", f"{stats['surface_area']:.1f} m\u00b2"],
        ["Watertight", "Yes" if stats["is_watertight"] else "No"],
        ["Components", str(stats["num_components"])],
    ]

    table = Table(rows, colWidths=[2.5 * inch, 4.0 * inch])
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


def _render_processing_details(elements, styles, data):
    """Render MipMap processing details for gaussian_splat reports."""
    mipmap = data.get("mipmap_settings", {})
    if not mipmap:
        return
    elements.append(Paragraph("Processing Details", styles["SectionHeader"]))
    res_level = mipmap.get("resolution_level", 3)
    decimate = mipmap.get("mesh_decimate_ratio", 0.5)
    elements.append(Paragraph(
        f"Processed via MipMap Desktop with resolution level {res_level} and "
        f"mesh decimation ratio {decimate}. Gaussian Splat outputs include PLY "
        f"point cloud and SOG tile set for web-based viewing.",
        styles["SentinelBody"],
    ))


def _render_ai_prose(elements, styles, section, ai_data):
    """Render an AI field as prose paragraphs. Handles str, dict, and list."""
    field_data = ai_data.get(section.ai_field) if ai_data else None
    if not field_data:
        return False

    elements.append(Paragraph(section.title, styles["SectionHeader"]))

    if isinstance(field_data, str):
        elements.append(Paragraph(field_data, styles["SentinelBody"]))
    elif isinstance(field_data, dict):
        # Render each key-value pair
        for key, value in field_data.items():
            label = key.replace("_", " ").title()
            if isinstance(value, list):
                elements.append(Paragraph(f"<b>{label}:</b>", styles["SentinelBody"]))
                for item in value:
                    elements.append(Paragraph(f"\u2022 {item}", styles["SentinelBody"]))
            elif isinstance(value, str):
                elements.append(Paragraph(f"<b>{label}:</b> {value}", styles["SentinelBody"]))
            elif isinstance(value, (int, float)):
                elements.append(Paragraph(f"<b>{label}:</b> {value}", styles["SentinelBody"]))
            elif isinstance(value, bool):
                elements.append(Paragraph(
                    f"<b>{label}:</b> {'Yes' if value else 'No'}", styles["SentinelBody"]))
    elif isinstance(field_data, list):
        for item in field_data:
            if isinstance(item, dict):
                # Render dict items as inline key-value
                parts = [f"<b>{k.replace('_', ' ').title()}:</b> {v}"
                         for k, v in item.items() if isinstance(v, str)]
                elements.append(Paragraph(" | ".join(parts), styles["SentinelBody"]))
            else:
                elements.append(Paragraph(f"\u2022 {item}", styles["SentinelBody"]))

    elements.append(Spacer(1, 8))
    return True


def _render_fallback(elements, styles, section):
    """Render static fallback text for a section."""
    if not section.fallback_text:
        return
    elements.append(Paragraph(section.title, styles["SectionHeader"]))
    elements.append(Paragraph(section.fallback_text, styles["SentinelBody"]))
    elements.append(Spacer(1, 8))


# ─── TEMPLATE-DRIVEN SECTION DISPATCH ────────────────────────────────────

def _render_section(elements, styles, section, data, ai_data, images, has_ai):
    """Dispatch a single template section to the appropriate renderer."""
    key = section.key

    # Special-case sections
    if key == "executive_summary":
        _render_executive_summary(elements, styles, section, ai_data)
        return
    if key == "flight_summary":
        _render_flight_summary(elements, styles, data)
        return
    if key == "deliverables":
        _render_deliverables(elements, styles, data)
        return
    if key == "methodology":
        _render_methodology(elements, styles, data, has_ai)
        return
    if key == "photo_grid":
        _render_photo_grid(elements, styles, section, images)
        return
    if key == "ortho_preview":
        _render_ortho_preview(elements, styles, section, images)
        return
    if key == "dsm_preview":
        _render_dsm_preview(elements, styles, section, images)
        return
    if key == "processing_details":
        _render_processing_details(elements, styles, data)
        return
    if key == "volume_comparison":
        _render_volume_comparison(elements, styles, data, images)
        return
    if key == "mesh_stats":
        _render_mesh_stats(elements, styles, data)
        return
    if key == "change_map":
        # Rendered inline by volume_comparison; skip standalone
        return
    if key == "recommendations":
        if has_ai:
            if _render_recommendations(elements, styles, section, ai_data):
                return
        _render_fallback(elements, styles, section)
        return
    if key == "observations":
        if has_ai:
            if _render_findings_table(elements, styles, section, ai_data):
                return
        _render_fallback(elements, styles, section)
        return

    # Generic AI-populated sections
    if has_ai and section.ai_field:
        if section.table_format == "findings":
            if _render_findings_table(elements, styles, section, ai_data):
                return
        elif section.table_format == "checklist":
            if _render_checklist_table(elements, styles, section, ai_data):
                return
        else:
            if _render_ai_prose(elements, styles, section, ai_data):
                return

    # Fallback
    _render_fallback(elements, styles, section)


# ─── MAIN ENTRY POINT ─────────────────────────────────────────────────────

def generate_report(report_type, data, output_dir):
    """Generate a branded PDF report using the template for this job type.

    Args:
        report_type: Key from REPORT_TYPES (e.g., "construction_progress")
        data: Dict with site metadata, photos, ai_analysis, images
        output_dir: Directory to write the PDF

    Returns:
        Dict with "pdf_path" key on success, or None on failure.
    """
    log = logging.getLogger(__name__)

    template = get_template(report_type)
    if not template:
        log.error(f"Unknown report type: {report_type}")
        return None

    if not REPORTLAB_AVAILABLE:
        log.error("reportlab not installed \u2014 cannot generate PDF report")
        return None

    site_name = data.get("site_name", "Site")
    date_str = data.get("date", datetime.now().strftime("%Y-%m-%d"))
    safe_name = site_name.replace(" ", "_").replace("/", "_").replace("\\", "_")
    pdf_filename = f"Sentinel_{safe_name}_{report_type}_{date_str}.pdf"
    pdf_path = os.path.join(output_dir, pdf_filename)

    os.makedirs(output_dir, exist_ok=True)

    ai_data = data.get("ai_analysis")
    images = data.get("images")
    has_ai = ai_data is not None and bool(ai_data.get("observations") or
                                           ai_data.get("executive_summary"))

    try:
        styles = _get_styles()
        elements = []

        # Cover page
        _render_cover(elements, styles, data, template)

        # Render each section from template in order
        for section in template.sections:
            _render_section(elements, styles, section, data, ai_data, images, has_ai)

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
