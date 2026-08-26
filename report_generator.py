"""
Sortie — Report Generator

Template-driven PDF report generation. Each job type has a dedicated
template (report_templates.py) defining sections, AI prompts, and fallbacks.
Supports AI-generated narratives (Gemini Vision) and embedded photo thumbnails.
"""

import os
import json
import logging
from pathlib import Path
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

# ─── SENTINEL AERIAL BRAND ───────────────────────────────────────────────
# Cover palette is sampled from the crest itself (gold/bronze), per Adam's
# decision 2026-08-04. The safety-orange accents below remain for interior
# section headers, where the crest is not present to clash with them.
#
# ⚠️ Two oranges exist across the two report systems and they do not match:
# this file used #FF6B35, SAI-Report-Template.html uses #f97316. Unify before
# the HTML and PDF paths are ever seen side by side by one client.

if REPORTLAB_AVAILABLE:
    SAI_ORANGE = HexColor("#FF6B35")
    SAI_BLACK = HexColor("#050505")
    SAI_DARK_GREY = HexColor("#1A1A1A")
    SAI_MID_GREY = HexColor("#4A4A4A")
    SAI_LIGHT = HexColor("#FFF3ED")
    SAI_ORANGE_LIGHT = HexColor("#FF8F66")
    LIGHT_GREY = HexColor("#D3D3D3")

    # Cover palette — light. The page is left white and nothing paints a
    # background, so the cover costs almost no toner and cannot suffer the
    # white-frame artefact a full-bleed dark page gets on any office printer.
    #
    # ⚠️ The gold here is the BRONZE end of the crest ramp, not the light
    # gold. #D0B060 measures 2.09:1 on white and fails WCAG AA outright.
    # #907030 measures 4.62:1 and passes, and holds 4.81:1 after greyscale
    # conversion on a mono printer. Do not swap it for the lighter gold.
    COVER_INK = HexColor("#0D1117")       # template --text-primary
    COVER_SUB = HexColor("#374151")       # template --text-secondary
    COVER_MUTED = HexColor("#6B7280")     # template --text-muted
    COVER_GOLD = HexColor("#907030")      # crest bronze
    COVER_GOLD_DIM = HexColor("#907030")
    COVER_CELL_BG = HexColor("#F7F8FA")   # template --surface
    COVER_RULE = HexColor("#E2E6EC")      # template --border
    COVER_BADGE_BG = HexColor("#FBF7EC")
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

# Logo path — bundled copy first, sibling repos only as legacy fallback.
# The bundled copy is what makes this survive a PyInstaller build and a moved
# sibling checkout. If none resolve we warn loudly rather than silently
# shipping a client-facing cover with no logo on it.
LOGO_PATH = None
for candidate in [
    Path(__file__).parent / "assets" / "sentinel-logo.png",
    Path("D:/Projects/sentinel-landing/public/sentinel-logo.png"),
    Path("D:/Projects/FaithandHarmony/public/assets/landing/sentinel-logo.png"),
]:
    if candidate.exists():
        LOGO_PATH = str(candidate)
        break
if LOGO_PATH is None:
    logging.getLogger(__name__).warning(
        "sentinel-logo.png not found in assets/ or the legacy sibling-repo "
        "paths — report covers will render without the crest."
    )


# ─── BRAND FONTS ─────────────────────────────────────────────────────────
# Registered from assets/fonts if present, else every style falls back to
# Helvetica. Falling back is not an error; it just is not on brand, so it is
# logged once at import.

FONT_DISPLAY = "Helvetica-Bold"
FONT_DISPLAY_REG = "Helvetica"
FONT_MONO = "Courier"
BRAND_FONTS_OK = False

if REPORTLAB_AVAILABLE:
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        _fdir = Path(__file__).parent / "assets" / "fonts"
        _faces = {
            "SairaCondensed": _fdir / "SairaCondensed-Regular.ttf",
            "SairaCondensed-Bold": _fdir / "SairaCondensed-Bold.ttf",
            "ShareTechMono": _fdir / "ShareTechMono-Regular.ttf",
        }
        if all(p.exists() for p in _faces.values()):
            for _name, _p in _faces.items():
                pdfmetrics.registerFont(TTFont(_name, str(_p)))
            pdfmetrics.registerFontFamily(
                "SairaCondensed", normal="SairaCondensed",
                bold="SairaCondensed-Bold",
                italic="SairaCondensed", boldItalic="SairaCondensed-Bold",
            )
            FONT_DISPLAY = "SairaCondensed-Bold"
            FONT_DISPLAY_REG = "SairaCondensed"
            FONT_MONO = "ShareTechMono"
            BRAND_FONTS_OK = True
        else:
            logging.getLogger(__name__).warning(
                "Brand fonts missing from assets/fonts — falling back to "
                "Helvetica. Expected SairaCondensed-Regular.ttf, "
                "SairaCondensed-Bold.ttf, ShareTechMono-Regular.ttf."
            )
    except Exception as e:
        logging.getLogger(__name__).warning(f"Font registration failed: {e}")

FOOTER_TEXT = (
    "Sentinel Aerial Inspections  |  FAA Part 107 Certified  |  "
    "sentinelaerialinspections.com  |  757.843.8772"
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
        fontSize=14, textColor=SAI_ORANGE_LIGHT, alignment=TA_CENTER,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "SectionHeader", parent=styles["Heading2"],
        fontSize=14, textColor=SAI_ORANGE,
        spaceBefore=16, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        "SubHeader", parent=styles["Heading3"],
        fontSize=11, textColor=SAI_BLACK,
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
        fontSize=18, textColor=SAI_ORANGE,
        alignment=TA_CENTER, spaceBefore=8, spaceAfter=8,
    ))
    return styles


# ─── PAGE TEMPLATE ─────────────────────────────────────────────────────────

def _footer(canvas_obj, doc):
    canvas_obj.saveState()
    PAGE_W, PAGE_H = letter
    canvas_obj.setStrokeColor(SAI_ORANGE)
    canvas_obj.setLineWidth(0.5)
    canvas_obj.line(0.75 * inch, 0.5 * inch, PAGE_W - 0.75 * inch, 0.5 * inch)
    canvas_obj.setFont("Helvetica", 7)
    canvas_obj.setFillColor(SAI_MID_GREY)
    canvas_obj.drawCentredString(PAGE_W / 2.0, 0.35 * inch, FOOTER_TEXT)
    canvas_obj.drawRightString(PAGE_W - 0.75 * inch, 0.35 * inch, f"Page {doc.page}")
    canvas_obj.restoreState()


# ─── SECTION RENDERERS ───────────────────────────────────────────────────
# Each renderer handles a specific section key or falls through to generic.

# ─── COVER ───────────────────────────────────────────────────────────────
# Structure is specified by report-system-spec-v1.md "Cover identity block"
# and mirrors SAI-Report-Template.html, which is the version already in
# service. Painted straight onto the canvas rather than assembled from
# flowables, because the page is full-bleed dark and flowables cannot paint
# a page background.
#
# 🔴 Cert number: the live HTML template carries a PLACEHOLDER (#4812346).
# The real certificate is #5275329, issued 2026-02-04, per sai-company-info.
# Do not copy the number back from the template.

COVER_CONTACT_LINES = [
    "Adam Pierce  ·  FAA Part 107 Cert #5275329",
    "info@faithandharmonyllc.com  ·  757.843.8772",
    "Faith & Harmony LLC  ·  Chesapeake, Virginia",
]
COVER_LOCALE = "Hampton Roads, Virginia  ·  sentinelaerialinspections.com"
COVER_BADGE = "Confidential Deliverable"
EMDASH = "—"

# Smallest type allowed anywhere on the cover. The first cut used 6.5pt for
# cell labels and 7pt for the locale line, which is legal-fine-print size:
# fine on a 27" monitor, marginal on paper, unreadable on a phone at
# fit-width. 8pt is the floor for anything a client is expected to read.
# Contrast is not the constraint here — every cover colour clears WCAG AA on
# #0A0A0A and still clears it after grayscale conversion for a B&W printer.
PT_MIN = 8.0


def _fmt_cover_date(raw):
    """ISO date to 'April 23, 2026'. Anything unparseable passes through."""
    if not raw:
        return EMDASH
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(raw)[:10], fmt).strftime("%B %d, %Y")
        except ValueError:
            continue
    return str(raw)


def _tracked_width(canvas_obj, text, font, size, tracking):
    return canvas_obj.stringWidth(text, font, size) + tracking * max(
        len(text) - 1, 0)


def _spaced(canvas_obj, x, y, text, font, size, color, tracking=0.0,
            centred_on=None):
    """Letterspaced text. Canvas has no setCharSpace; it lives on the text
    object, which is why this goes through beginText."""
    if centred_on is not None:
        x = centred_on - _tracked_width(
            canvas_obj, text, font, size, tracking) / 2.0
    t = canvas_obj.beginText(x, y)
    t.setFont(font, size)
    t.setFillColor(color)
    if tracking:
        t.setCharSpace(tracking)
    t.textOut(text)
    canvas_obj.drawText(t)


def _wrap_to_width(canvas_obj, text, font, size, max_w):
    canvas_obj.setFont(font, size)
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if canvas_obj.stringWidth(trial, font, size) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _draw_cover(canvas_obj, data, template):
    PAGE_W, PAGE_H = letter
    M = 0.75 * inch

    canvas_obj.saveState()

    # Page is left white. The gold rule spans the content width rather than
    # bleeding to the sheet edge, so it survives a printer that cannot print
    # to the edge.
    rule_y = PAGE_H - M
    canvas_obj.setFillColor(COVER_GOLD)
    canvas_obj.rect(M, rule_y, PAGE_W - 2 * M, 3, stroke=0, fill=1)

    # ── Brand lockup: crest at 0.75in beside the wordmark
    logo_sz = 0.75 * inch
    logo_top = rule_y - 22
    text_x = M
    if LOGO_PATH and os.path.exists(LOGO_PATH):
        try:
            canvas_obj.drawImage(
                LOGO_PATH, M, logo_top - logo_sz, width=logo_sz, height=logo_sz,
                mask="auto", preserveAspectRatio=True, anchor="sw",
            )
            text_x = M + logo_sz + 14
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to embed logo: {e}")
    _spaced(canvas_obj, text_x, logo_top - 22, "SENTINEL AERIAL INSPECTIONS",
            FONT_DISPLAY, 11, COVER_INK, 1.9)
    _spaced(canvas_obj, text_x, logo_top - 37, COVER_LOCALE,
            FONT_DISPLAY_REG, PT_MIN, COVER_MUTED, 1.0)

    # ── Footer, anchored to the bottom so the hero can flow above it
    y = M
    canvas_obj.setFont(FONT_DISPLAY_REG, PT_MIN)
    canvas_obj.setFillColor(COVER_MUTED)
    for line in reversed(COVER_CONTACT_LINES):
        canvas_obj.drawString(M, y, line)
        y += 11.5
    # Badge sizes to its text so raising PT_MIN cannot overflow it.
    badge_txt = COVER_BADGE.upper()
    badge_w = _tracked_width(canvas_obj, badge_txt, FONT_DISPLAY,
                             PT_MIN, 1.5) + 24
    badge_h = 20
    bx, by = PAGE_W - M - badge_w, M
    canvas_obj.setFillColor(COVER_BADGE_BG)
    canvas_obj.setStrokeColor(COVER_GOLD_DIM)
    canvas_obj.setLineWidth(0.6)
    canvas_obj.roundRect(bx, by, badge_w, badge_h, 3, stroke=1, fill=1)
    _spaced(canvas_obj, 0, by + 7, badge_txt, FONT_DISPLAY, PT_MIN,
            COVER_GOLD, 1.5, centred_on=bx + badge_w / 2.0)

    canvas_obj.setStrokeColor(COVER_RULE)
    canvas_obj.setLineWidth(0.5)
    hairline_y = M + 46
    canvas_obj.line(M, hairline_y, PAGE_W - M, hairline_y)

    # ── Hero block. Measured first, then centred in the band between the
    # brand lockup and the footer rule, which is what `margin: auto 0` does
    # in the HTML template. Anchoring it to the bottom instead leaves a dead
    # band through the middle of the page.
    title = data.get("site_name") or "Site"
    max_w = PAGE_W - 2 * M - 90
    size, leading, lines = 34, 37, None
    for trial in (34, 28, 23, 19):
        cand = _wrap_to_width(canvas_obj, title, FONT_DISPLAY, trial, max_w)
        if len(cand) <= 3:
            size, leading, lines = trial, int(trial * 1.09), cand
            break
    if lines is None:
        size, leading = 19, 21
        lines = _wrap_to_width(canvas_obj, title, FONT_DISPLAY, 19, max_w)[:3]

    subtitle = data.get("client") or data.get("client_org") or ""
    city = data.get("client_city") or ""
    if subtitle and city:
        subtitle = f"{subtitle}  ·  {city}"

    cell_w, cell_h, gap = 176, 46, 12
    KICKER_H, SUB_H, GRID_GAP = 22, (30 if subtitle else 8), 26
    grid_h = cell_h * 2 + gap
    block_h = (KICKER_H + size + (len(lines) - 1) * leading
               + SUB_H + GRID_GAP + grid_h)

    band_top = logo_top - logo_sz - 48
    band_bottom = hairline_y + 34
    cursor = (band_top + band_bottom) / 2.0 + block_h / 2.0

    # Kicker
    canvas_obj.setFillColor(COVER_GOLD)
    canvas_obj.rect(M, cursor - 8, 24, 2, stroke=0, fill=1)
    kicker = template.title
    if data.get("visit_sequence"):
        kicker = f"{kicker}  ·  {data['visit_sequence']}"
    _spaced(canvas_obj, M + 34, cursor - 10, kicker.upper(),
            FONT_DISPLAY, PT_MIN, COVER_GOLD, 2.2)
    cursor -= KICKER_H

    # Title — the property name, per spec. Report type is the kicker above.
    canvas_obj.setFont(FONT_DISPLAY, size)
    canvas_obj.setFillColor(COVER_INK)
    for ln in lines:
        cursor -= size
        canvas_obj.drawString(M, cursor, ln)
        cursor -= (leading - size)
    cursor += (leading - size)

    # Subtitle — client organisation · city
    if subtitle:
        cursor -= SUB_H
        canvas_obj.setFont(FONT_DISPLAY_REG, 10.5)
        canvas_obj.setFillColor(COVER_SUB)
        canvas_obj.drawString(M, cursor, subtitle)
    else:
        cursor -= SUB_H

    # ── Meta grid: Flight Date, Job Number, Site Address, Prepared For
    cursor -= GRID_GAP
    cells = [
        ("Flight Date", _fmt_cover_date(data.get("date"))),
        ("Job Number", data.get("job_number") or EMDASH),
        ("Site Address", data.get("site_address") or EMDASH),
        ("Prepared For", data.get("prepared_for") or EMDASH),
    ]
    row_y = [cursor - cell_h, cursor - cell_h * 2 - gap]
    for i, (label, value) in enumerate(cells):
        cx = M + (i % 2) * (cell_w + gap)
        cy = row_y[i // 2]
        canvas_obj.setFillColor(COVER_CELL_BG)
        canvas_obj.setStrokeColor(COVER_RULE)
        canvas_obj.setLineWidth(0.5)
        canvas_obj.roundRect(cx, cy, cell_w, cell_h, 4, stroke=1, fill=1)
        _spaced(canvas_obj, cx + 12, cy + cell_h - 17, label.upper(),
                FONT_DISPLAY, PT_MIN, COVER_GOLD, 1.4)
        val_lines = _wrap_to_width(canvas_obj, value, FONT_DISPLAY_REG, 9.5,
                                   cell_w - 24)[:2]
        vy = cy + cell_h - 31
        canvas_obj.setFont(FONT_DISPLAY_REG, 9.5)
        canvas_obj.setFillColor(COVER_INK)
        for ln in val_lines:
            canvas_obj.drawString(cx + 12, vy, ln)
            vy -= 11

    canvas_obj.restoreState()


def _make_cover_painter(data, template):
    """onFirstPage handler. Closure carries the job data onto the canvas."""
    def paint(canvas_obj, doc):
        try:
            _draw_cover(canvas_obj, data, template)
        except Exception as e:
            logging.getLogger(__name__).error(f"Cover render failed: {e}")
    return paint


def _render_cover(elements, styles, data, template):
    """Cover is painted by the onFirstPage handler, so the story only has to
    leave page one empty and move on."""
    elements.append(Spacer(1, 1))
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
        ("BACKGROUND", (0, 0), (-1, -1), SAI_LIGHT),
        ("BOX", (0, 0), (-1, -1), 1.5, SAI_ORANGE),
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
    if data.get("panorama_sets") is not None:
        rows.append(["Panorama Sets", str(len(data.get("panorama_sets", [])))])
        rows.append(["Skipped Straggler Photos",
                     str(data.get("panorama_stragglers", 0))])

    table = Table(rows, colWidths=[2 * inch, 4.5 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), SAI_LIGHT),
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
        ("BACKGROUND", (0, 0), (-1, 0), SAI_ORANGE),
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
    if engine == "local":
        from photo_classifier import PANORAMA_CLUSTER_RADIUS_M
        elements.append(Paragraph(
            "DJI panorama source photos were grouped by capture position "
            f"within each capture folder using a {PANORAMA_CLUSTER_RADIUS_M:g}-metre "
            "geographic radius. Existing DJI-stitched panoramas were preserved "
            "when available. Remaining sets were stitched with OpenCV, then "
            "packaged with locally hosted Pannellum viewer files.",
            styles["SentinelBody"],
        ))
        elements.append(Paragraph(
            "Panorama imagery is intended for visual documentation and portfolio "
            "presentation. It is not a survey or measurement deliverable.",
            styles["SmallGrey"],
        ))
        return
    if engine == "opensplat":
        proc = ("Camera positions were solved photogrammetrically with "
                "OpenDroneMap via NodeODM, and the scene was trained with "
                "OpenSplat, an open-source 3D Gaussian Splatting "
                "implementation, using GPU acceleration.")
    elif engine == "mipmap":
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


def _render_panorama_sets(elements, styles, data):
    """Render panorama positions and processing status."""
    panorama_sets = data.get("panorama_sets", [])
    if not panorama_sets:
        return

    elements.append(Paragraph("Panorama Sets", styles["SectionHeader"]))
    rows = [["Set", "Position", "Photos", "Source", "Status", "Viewer"]]
    for item in panorama_sets:
        latitude = item.get("latitude")
        longitude = item.get("longitude")
        position = (
            f"{latitude:.6f}, {longitude:.6f}"
            if latitude is not None and longitude is not None else "Not available"
        )
        rows.append([
            str(item.get("set", "")),
            position,
            str(item.get("photo_count", 0)),
            str(item.get("source_type", "unknown")).replace("_", " ").title(),
            str(item.get("status", "unknown")).replace("_", " ").title(),
            str(item.get("viewer_status", "not generated")).replace("_", " ").title(),
        ])

    table = Table(
        rows,
        colWidths=[0.35 * inch, 1.55 * inch, 0.55 * inch,
                   1.25 * inch, 1.25 * inch, 1.1 * inch],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SAI_ORANGE),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, LIGHT_GREY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 12))


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
        ("BACKGROUND", (0, 0), (-1, 0), SAI_ORANGE),
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
        ("BACKGROUND", (0, 0), (-1, 0), SAI_ORANGE),
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
        ("BACKGROUND", (0, 0), (0, -1), SAI_LIGHT),
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
        ("BACKGROUND", (0, 0), (0, -1), SAI_LIGHT),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, LIGHT_GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 12))


def _render_vegetation_analysis(elements, styles, section, data):
    """Render the VARI vegetation index results table. Returns True if rendered."""
    veg = data.get("veg_results")
    if not veg:
        return False

    elements.append(Paragraph(section.title, styles["SectionHeader"]))
    elements.append(Paragraph(
        "Quantitative vegetation cover from the RGB orthomosaic using the "
        "VARI index (Visible Atmospherically Resistant Index). Flagged "
        "polygons are delivered as a GeoPackage plus a styled map PDF — "
        "see Deliverables.",
        styles["SentinelBody"]))
    elements.append(Spacer(1, 6))

    rows = [
        ["Index", veg.get("index", "VARI")],
        ["Vegetation Cover", f"{veg.get('veg_pct', 0):.1f}%"],
        ["Flagged Polygons", str(veg.get("flagged_polygons", 0))],
        ["Vegetation Threshold", f"{veg.get('threshold', 0):.2f}"],
        ["Minimum Polygon Area", f"{veg.get('min_area_m2', 0):.1f} m²"],
    ]

    table = Table(rows, colWidths=[2.5 * inch, 4.0 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), SAI_LIGHT),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, LIGHT_GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 12))
    return True


def _render_processing_details(elements, styles, data):
    """Render splat-engine processing details for gaussian_splat reports."""
    opensplat = data.get("opensplat_settings", {})
    if opensplat and data.get("engine") == "opensplat":
        elements.append(Paragraph("Processing Details", styles["SectionHeader"]))
        iters = opensplat.get("num_iters", 30000)
        downscale = opensplat.get("downscale_factor", 2)
        elements.append(Paragraph(
            f"Gaussian Splat trained with OpenSplat for {iters:,} iterations "
            f"at a {downscale}x image downscale on GPU. Output is a PLY "
            f"gaussian point cloud suitable for interactive 3D viewing.",
            styles["SentinelBody"],
        ))
        return
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
    if key == "panorama_sets":
        _render_panorama_sets(elements, styles, data)
        return
    if key == "volume_comparison":
        _render_volume_comparison(elements, styles, data, images)
        return
    if key == "mesh_stats":
        _render_mesh_stats(elements, styles, data)
        return
    if key == "vegetation_analysis":
        if _render_vegetation_analysis(elements, styles, section, data):
            return
        _render_fallback(elements, styles, section)
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

    Cover keys (report-system-spec-v1.md "Cover identity block"). Every one
    is optional here and degrades to an em dash, but a client-facing report
    should carry all of them:

        site_name       Property name. Rendered as the cover TITLE.
        date            ISO flight date. Formatted to "April 23, 2026".
        job_number      SAI-YYYY-NNN, sequential per year, one series across
                        every service line.
        site_address    Street address of the site.
        prepared_for    Named recipient, e.g. "Marcus T. Williams, PM".
        client          Client organisation. Rendered as the subtitle.
        client_city     Appended to the subtitle after a middle dot.
        visit_sequence  Optional, e.g. "Visit 04 of 12". Appended to the
                        kicker, which is otherwise just the report type.

    ⚠️ The four spec-mandated fields job_number, site_address, prepared_for
    and client are not yet populated by the Sortie caller. Until they are,
    covers ship with em dashes in the meta grid.

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
        doc.build(
            elements,
            onFirstPage=_make_cover_painter(data, template),
            onLaterPages=_footer,
        )

        log.info(f"Report saved: {pdf_path}")
        return {"pdf_path": pdf_path}

    except Exception as e:
        log.error(f"Report generation failed: {e}")
        return None
