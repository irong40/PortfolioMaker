"""One-off script to generate customer delivery letter for Hampton Cemetery job."""

import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)

SAI_ORANGE = HexColor("#FF6B35")
SAI_BLACK = HexColor("#050505")
SAI_DARK_GREY = HexColor("#1A1A1A")
SAI_MID_GREY = HexColor("#4A4A4A")
SAI_LIGHT = HexColor("#FFF3ED")
SAI_ORANGE_LIGHT = HexColor("#FF8F66")
GREY = HexColor("#D3D3D3")

LOGO_PATH = "D:/Projects/sentinel-landing/public/sentinel-logo.png"

FOOTER_TEXT = (
    "Sentinel Aerial Inspections  |  FAA Part 107 Certified  |  "
    "sentinelaerialinspections.com  |  757.843.8772"
)


def add_footer(canvas, doc):
    canvas.saveState()
    W, _ = letter
    canvas.setStrokeColor(SAI_ORANGE)
    canvas.setLineWidth(0.5)
    canvas.line(0.75 * inch, 0.5 * inch, W - 0.75 * inch, 0.5 * inch)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(SAI_MID_GREY)
    canvas.drawCentredString(W / 2, 0.35 * inch, FOOTER_TEXT)
    canvas.restoreState()


def main():
    output_dir = "E:/Portfolio/HamptonCemetery/all"
    pdf_path = os.path.join(output_dir, "Sentinel_Delivery_CemeteryLn_Hampton_2026-04-06.pdf")

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("CoverTitle", parent=styles["Title"],
               fontSize=28, textColor=white, alignment=TA_CENTER, spaceAfter=12))
    styles.add(ParagraphStyle("CoverSub", parent=styles["Normal"],
               fontSize=14, textColor=SAI_ORANGE_LIGHT, alignment=TA_CENTER, spaceAfter=6))
    styles.add(ParagraphStyle("Section", parent=styles["Heading2"],
               fontSize=14, textColor=SAI_ORANGE, spaceBefore=16, spaceAfter=8))
    styles.add(ParagraphStyle("Body", parent=styles["Normal"],
               fontSize=10, leading=14, spaceAfter=6))
    styles.add(ParagraphStyle("SmallGrey", parent=styles["Normal"],
               fontSize=8, textColor=HexColor("#777777")))

    elements = []
    W, _ = letter

    # Cover
    cover = Table(
        [[Paragraph("SENTINEL AERIAL INSPECTIONS", styles["CoverSub"])],
         [Paragraph("Aerial Survey Delivery", styles["CoverTitle"])],
         [Paragraph("1923 Cemetery Ln, Hampton VA", styles["CoverSub"])],
         [Paragraph("April 6, 2026", styles["CoverSub"])]],
        colWidths=[W - 1.5 * inch],
        rowHeights=[30, 50, 30, 25],
    )
    cover.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SAI_BLACK),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 20),
        ("RIGHTPADDING", (0, 0), (-1, -1), 20),
    ]))
    elements.append(Spacer(1, 1.5 * inch))
    elements.append(cover)
    elements.append(Spacer(1, 0.75 * inch))

    # Logo between title block and bottom of page
    if os.path.exists(LOGO_PATH):
        from reportlab.platypus import Image as RLImage
        logo = RLImage(LOGO_PATH, width=2.0 * inch, height=2.0 * inch,
                       kind="proportional")
        logo.hAlign = "CENTER"
        elements.append(logo)

    elements.append(PageBreak())

    # Project summary
    elements.append(Paragraph("Project Summary", styles["Section"]))
    elements.append(Paragraph(
        "Aerial survey of Jewish cemetery at 1923 Cemetery Ln, Hampton VA to produce "
        "a high resolution orthomosaic for lot digitization and integration with "
        "Chronicle cemetery management software (chronicle.rip).",
        styles["Body"],
    ))

    # Survey specs
    elements.append(Paragraph("Survey Specifications", styles["Section"]))
    spec_rows = [
        ["Site", "1923 Cemetery Ln, Hampton VA"],
        ["Date Flown", "April 6, 2026"],
        ["Platform", "DJI Mini 4 Pro"],
        ["Pilot", "Adam Pierce, FAA Part 107 Certified"],
        ["Total Photos", "908 (904 nadir, 4 oblique)"],
        ["Coordinate System", "EPSG:32618 (UTM Zone 18N, WGS84)"],
        ["Processing Engine", "OpenDroneMap (NodeODM)"],
    ]
    t = Table(spec_rows, colWidths=[2.2 * inch, 4.3 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), SAI_LIGHT),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 12))

    # GSD verification
    elements.append(Paragraph("Resolution Verification", styles["Section"]))
    gsd_rows = [
        ["Required GSD", "0.4 \u2013 0.6 inches/pixel (1.2 \u2013 1.5 cm/pixel)"],
        ["Achieved GSD", "0.38 inches/pixel (0.96 cm/pixel)"],
        ["Status", "EXCEEDS REQUIREMENT"],
        ["Orthophoto Size", "23,989 \u00d7 26,851 pixels"],
        ["Pixel Size", "1.0 cm \u00d7 1.0 cm"],
        ["Coverage Area", "~35,155 m\u00b2 (~8.7 acres)"],
    ]
    gt = Table(gsd_rows, colWidths=[2.2 * inch, 4.3 * inch])
    gt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), SAI_LIGHT),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TEXTCOLOR", (1, 2), (1, 2), HexColor("#27AE60")),
        ("FONTNAME", (1, 2), (1, 2), "Helvetica-Bold"),
    ]))
    elements.append(gt)
    elements.append(Spacer(1, 12))

    # Deliverables
    elements.append(Paragraph("Deliverables", styles["Section"]))

    ortho = os.path.join(output_dir, "odm_orthophoto", "odm_orthophoto.tif")
    dsm = os.path.join(output_dir, "odm_dem", "dsm.tif")
    laz = os.path.join(output_dir, "odm_georeferencing", "odm_georeferenced_model.laz")

    def mb(path):
        return f"{os.path.getsize(path) / (1024 * 1024):.0f} MB" if os.path.exists(path) else "N/A"

    d_rows = [
        ["File", "Format", "Size", "Description"],
        ["odm_orthophoto.tif", "GeoTIFF (COG)", mb(ortho), "Georeferenced orthomosaic"],
        ["dsm.tif", "GeoTIFF", mb(dsm), "Digital Surface Model"],
        ["odm_georeferenced_model.laz", "LAZ", mb(laz), "Georeferenced point cloud"],
    ]
    dt = Table(d_rows, colWidths=[2.2 * inch, 1.2 * inch, 0.7 * inch, 2.4 * inch])
    dt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SAI_ORANGE),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(dt)
    elements.append(Spacer(1, 12))

    # Chronicle compatibility
    elements.append(Paragraph("Chronicle Compatibility", styles["Section"]))
    elements.append(Paragraph(
        "The orthomosaic is delivered as a Cloud Optimized GeoTIFF (COG) in EPSG:32618 "
        "(UTM Zone 18N). This format is directly compatible with GIS software and web "
        "mapping platforms. The achieved resolution of 0.96 cm/pixel provides sufficient "
        "detail for digitizing individual cemetery lots, headstones, and pathway boundaries.",
        styles["Body"],
    ))
    elements.append(Paragraph(
        "The coordinate reference system (EPSG:32618) uses meters as the native unit, "
        "enabling accurate area and distance measurements directly from the orthomosaic.",
        styles["Body"],
    ))

    # Notes
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(
        "This survey was conducted under FAA Part 107 regulations. GPS positions are "
        "derived from the aircraft onboard GPS (non RTK). For survey grade accuracy, "
        "ground control points should be used. All files are georeferenced and ready "
        "for use in standard GIS or mapping software.",
        styles["SmallGrey"],
    ))
    elements.append(Paragraph(
        "Sentinel Aerial Inspections | info@faithandharmonyllc.com | 757.843.8772",
        styles["SmallGrey"],
    ))

    # Build PDF
    doc = SimpleDocTemplate(pdf_path, pagesize=letter,
                            leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.75 * inch, bottomMargin=0.75 * inch)
    doc.build(elements, onFirstPage=add_footer, onLaterPages=add_footer)

    print(f"Delivery letter: {pdf_path}")
    print(f"Size: {os.path.getsize(pdf_path) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
