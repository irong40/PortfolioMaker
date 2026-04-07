"""
Sortie — Report Templates

Each job type has a dedicated template defining:
- AI analysis prompt with type-specific JSON schema
- Custom report sections and ordering
- Fallback static content when AI is unavailable
- Section-specific data tables and formatting
"""

from dataclasses import dataclass, field


@dataclass
class ReportSection:
    """A single section in a report template."""
    key: str                    # Internal ID
    title: str                  # Display heading
    ai_field: str = ""          # JSON key from AI response (if AI-populated)
    fallback_text: str = ""     # Static text when AI unavailable
    subsections: list = field(default_factory=list)  # list of (title, text) tuples
    table_format: str = ""      # "findings", "checklist", "matrix", or "" for prose
    include_images: bool = False  # Whether this section embeds photos


@dataclass
class ReportTemplate:
    """Complete template for one report type."""
    report_type: str
    title: str
    ai_system_addendum: str     # Added to base system prompt for this type
    ai_prompt: str              # What to ask the AI to analyze
    ai_schema: dict             # JSON keys expected from AI
    sections: list              # list of ReportSection in display order
    photo_strategy: str = "balanced"  # "nadir_heavy", "oblique_heavy", "balanced"
    max_ai_photos: int = 6


# ─── CONSTRUCTION PROGRESS ────────────────────────────────────────────────

CONSTRUCTION_PROGRESS = ReportTemplate(
    report_type="construction_progress",
    title="Construction Progress Report",
    photo_strategy="nadir_heavy",
    max_ai_photos=6,
    ai_system_addendum=(
        "You specialize in construction progress monitoring from aerial survey. "
        "Focus on quantifiable progress indicators and comparison-ready observations."
    ),
    ai_prompt=(
        "Analyze these aerial construction site photos. For each photo, assess:\n"
        "1. EARTHWORK: grading progress, excavation, fill areas, drainage\n"
        "2. FOUNDATIONS: forms, pours, curing, rebar visible\n"
        "3. FRAMING/STRUCTURE: stage of vertical construction\n"
        "4. SITE LOGISTICS: material staging, equipment positions, access roads\n"
        "5. SAFETY/COMPLIANCE: silt fencing, erosion control, safety barriers\n"
        "6. OVERALL PROGRESS: estimated phase of construction"
    ),
    ai_schema={
        "executive_summary": "2-3 sentence overall site status",
        "construction_phase": "estimated phase (site prep, foundation, framing, etc.)",
        "earthwork": {"status": "str", "observations": ["str"]},
        "foundations": {"status": "str", "observations": ["str"]},
        "structures": {"status": "str", "observations": ["str"]},
        "site_logistics": {"observations": ["str"]},
        "safety_compliance": {"findings": [{"item": "str", "status": "pass|fail|warning", "note": "str"}]},
        "progress_indicators": ["str"],
        "observations": [{"finding": "str", "location": "str", "severity": "info|minor|moderate|major"}],
        "conditions": ["str"],
        "recommendations": ["str"],
        "photo_notes": [{"photo_index": "int", "description": "str"}],
    },
    sections=[
        ReportSection(
            key="executive_summary",
            title="Executive Summary",
            ai_field="executive_summary",
        ),
        ReportSection(
            key="flight_summary",
            title="Flight Summary",
        ),
        ReportSection(
            key="ortho_preview",
            title="Site Orthomosaic",
            include_images=True,
        ),
        ReportSection(
            key="construction_phase",
            title="Construction Phase Assessment",
            ai_field="construction_phase",
            fallback_text=(
                "Construction phase assessment requires AI analysis. Configure a "
                "Gemini API key to enable automated phase detection from aerial imagery."
            ),
        ),
        ReportSection(
            key="earthwork",
            title="Earthwork & Grading",
            ai_field="earthwork",
            fallback_text=(
                "Orthomosaic and DSM outputs enable measurement of areas, volumes, and "
                "visual comparison with previous visits. DSM differencing can quantify "
                "earthwork volumes between visits. Load the point cloud into your preferred "
                "GIS or CAD software for cut/fill analysis."
            ),
        ),
        ReportSection(
            key="volume_comparison",
            title="Volume Comparison",
        ),
        ReportSection(
            key="foundations",
            title="Foundations & Structures",
            ai_field="foundations",
            fallback_text=(
                "Review the orthomosaic and 3D model for foundation progress, form placement, "
                "rebar installation, and pour status. Compare with previous visit data "
                "to track vertical construction progress."
            ),
        ),
        ReportSection(
            key="site_logistics",
            title="Site Logistics & Equipment",
            ai_field="site_logistics",
            fallback_text=(
                "Material staging areas, equipment positions, and access road conditions "
                "are visible in the orthomosaic. Track changes between visits to monitor "
                "resource deployment and site organization."
            ),
        ),
        ReportSection(
            key="safety_compliance",
            title="Safety & Environmental Compliance",
            ai_field="safety_compliance",
            table_format="checklist",
            fallback_text=(
                "Review aerial imagery for: erosion control measures (silt fence, "
                "inlet protection), safety fencing, barricades, and equipment safety zones. "
                "AI analysis can automate this checklist — configure a Gemini API key."
            ),
        ),
        ReportSection(key="photo_grid", title="Site Photography", include_images=True),
        ReportSection(key="observations", title="Findings & Observations", ai_field="observations", table_format="findings"),
        ReportSection(key="recommendations", title="Recommendations", ai_field="recommendations"),
        ReportSection(key="deliverables", title="Deliverables"),
        ReportSection(key="methodology", title="Methodology"),
    ],
)


# ─── PROPERTY SURVEY ──────────────────────────────────────────────────────

PROPERTY_SURVEY = ReportTemplate(
    report_type="property_survey",
    title="Property Survey Report",
    photo_strategy="nadir_heavy",
    max_ai_photos=6,
    ai_system_addendum=(
        "You specialize in aerial property survey documentation. "
        "Focus on boundary identification, terrain analysis, and encroachment detection."
    ),
    ai_prompt=(
        "Analyze these aerial property survey photos. Assess:\n"
        "1. BOUNDARIES: fences, walls, hedges, property line markers\n"
        "2. ENCROACHMENTS: structures or features crossing apparent boundaries\n"
        "3. TERRAIN: slopes, drainage patterns, low areas, flood risk indicators\n"
        "4. STRUCTURES: buildings, sheds, pools, driveways with estimated dimensions\n"
        "5. EASEMENTS: utility corridors, access paths, right-of-way indicators\n"
        "6. VEGETATION: tree canopy, cleared areas, landscaping"
    ),
    ai_schema={
        "executive_summary": "str",
        "boundaries": {"description": "str", "features": ["str"]},
        "encroachments": [{"item": "str", "location": "str", "severity": "str"}],
        "terrain_analysis": {"description": "str", "features": ["str"]},
        "structures_inventory": [{"type": "str", "location": "str", "condition": "str"}],
        "easements": ["str"],
        "elevation_notes": "str",
        "observations": [{"finding": "str", "location": "str", "severity": "str"}],
        "conditions": ["str"],
        "recommendations": ["str"],
        "photo_notes": [{"photo_index": "int", "description": "str"}],
    },
    sections=[
        ReportSection(key="executive_summary", title="Executive Summary", ai_field="executive_summary"),
        ReportSection(key="flight_summary", title="Flight Summary"),
        ReportSection(key="ortho_preview", title="Property Orthomosaic", include_images=True),
        ReportSection(
            key="boundaries",
            title="Boundary Analysis",
            ai_field="boundaries",
            fallback_text=(
                "Property boundaries, fences, walls, and hedges are visible in the "
                "orthomosaic. Use GIS software to overlay parcel data and measure "
                "boundary features against recorded plat dimensions."
            ),
        ),
        ReportSection(
            key="encroachments",
            title="Encroachment Assessment",
            ai_field="encroachments",
            table_format="findings",
            fallback_text=(
                "Review the orthomosaic for structures, fences, or landscaping that "
                "may cross property boundaries. AI analysis can automate encroachment "
                "detection — configure a Gemini API key."
            ),
        ),
        ReportSection(
            key="terrain_analysis",
            title="Terrain & Drainage",
            ai_field="terrain_analysis",
            fallback_text=(
                "DSM and DTM outputs provide elevation data for drainage analysis. "
                "Load into QGIS or similar software to generate contour lines, "
                "slope maps, and watershed delineation."
            ),
        ),
        ReportSection(
            key="volume_comparison",
            title="Volume Comparison",
        ),
        ReportSection(
            key="structures_inventory",
            title="Structures Inventory",
            ai_field="structures_inventory",
            table_format="findings",
            fallback_text=(
                "All visible structures are captured in the orthomosaic and 3D model. "
                "Measurements can be extracted from the point cloud for area and "
                "setback calculations."
            ),
        ),
        ReportSection(
            key="elevation_notes",
            title="Elevation Analysis",
            ai_field="elevation_notes",
            fallback_text=(
                "Digital Surface Model (DSM) captures all features including structures "
                "and vegetation. Digital Terrain Model (DTM) represents bare earth "
                "elevation. Both are provided as GeoTIFF deliverables."
            ),
        ),
        ReportSection(key="dsm_preview", title="Elevation Map", include_images=True),
        ReportSection(key="photo_grid", title="Property Photography", include_images=True),
        ReportSection(key="observations", title="Findings & Observations", ai_field="observations", table_format="findings"),
        ReportSection(key="recommendations", title="Recommendations", ai_field="recommendations"),
        ReportSection(key="deliverables", title="Deliverables"),
        ReportSection(key="methodology", title="Methodology"),
    ],
)


# ─── ROOF INSPECTION ─────────────────────────────────────────────────────

ROOF_INSPECTION = ReportTemplate(
    report_type="roof_inspection",
    title="Roof Inspection Report",
    photo_strategy="oblique_heavy",
    max_ai_photos=8,
    ai_system_addendum=(
        "You specialize in aerial roof condition assessment. Use insurance and "
        "roofing industry terminology. Rate conditions on a 1-5 scale where "
        "1=excellent, 5=replacement needed."
    ),
    ai_prompt=(
        "Analyze these aerial roof inspection photos. Assess each area:\n"
        "1. ROOFING MATERIAL: type, age indicators, general condition (1-5 scale)\n"
        "2. DAMAGE: missing/cracked/curling/lifted shingles or tiles, punctures\n"
        "3. FLASHING: condition at penetrations, valleys, edges, chimneys\n"
        "4. DRAINAGE: gutters, downspouts, ponding water, debris blockage\n"
        "5. PENETRATIONS: skylights, vents, pipes — seal condition\n"
        "6. BIOLOGICAL: moss, algae, lichen, vegetation growth\n"
        "7. MECHANICAL: HVAC units, satellite dishes, solar panels — condition\n"
        "8. OVERALL: estimated remaining life, priority repairs"
    ),
    ai_schema={
        "executive_summary": "str",
        "roof_material": {"type": "str", "estimated_age": "str", "condition_rating": "int 1-5"},
        "damage_findings": [{"type": "str", "location": "str", "severity": "minor|moderate|major", "repair_priority": "str"}],
        "flashing_condition": {"overall": "str", "problem_areas": ["str"]},
        "drainage_assessment": {"gutters": "str", "downspouts": "str", "ponding": "str"},
        "penetrations": [{"type": "str", "condition": "str"}],
        "biological_growth": {"present": "bool", "type": "str", "extent": "str"},
        "mechanical_equipment": [{"type": "str", "condition": "str"}],
        "overall_assessment": {"condition_rating": "int 1-5", "estimated_remaining_life": "str", "priority_repairs": ["str"]},
        "observations": [{"finding": "str", "location": "str", "severity": "str"}],
        "conditions": ["str"],
        "recommendations": ["str"],
        "photo_notes": [{"photo_index": "int", "description": "str"}],
    },
    sections=[
        ReportSection(key="executive_summary", title="Executive Summary", ai_field="executive_summary"),
        ReportSection(key="flight_summary", title="Flight Summary"),
        ReportSection(key="ortho_preview", title="Roof Overview", include_images=True),
        ReportSection(
            key="roof_material",
            title="Roofing Material Assessment",
            ai_field="roof_material",
            fallback_text=(
                "Roofing material type, age, and general condition should be assessed "
                "from the oblique imagery and 3D model. AI analysis provides automated "
                "material identification and condition rating on a 1-5 scale."
            ),
        ),
        ReportSection(
            key="damage_findings",
            title="Damage Findings",
            ai_field="damage_findings",
            table_format="findings",
            fallback_text=(
                "Review oblique imagery and 3D mesh for: missing or damaged shingles, "
                "cracked tiles, lifted edges, punctures, and impact damage. "
                "Check all roof planes and transitions."
            ),
        ),
        ReportSection(
            key="flashing_condition",
            title="Flashing & Seals",
            ai_field="flashing_condition",
            fallback_text=(
                "Inspect flashing at all penetrations (vents, pipes, skylights), "
                "valleys, step flashing along walls, drip edge, and chimney counter-flashing. "
                "Look for lifting, rust, missing sealant, or gaps."
            ),
        ),
        ReportSection(
            key="drainage_assessment",
            title="Drainage & Gutters",
            ai_field="drainage_assessment",
            fallback_text=(
                "Assess gutters for debris accumulation, sagging, and proper slope. "
                "Check downspouts for blockage and proper discharge away from foundation. "
                "Look for ponding water on flat sections."
            ),
        ),
        ReportSection(
            key="penetrations",
            title="Roof Penetrations",
            ai_field="penetrations",
            table_format="findings",
            fallback_text=(
                "All roof penetrations (skylights, vents, pipes, HVAC curbs) should be "
                "inspected for seal integrity, flashing condition, and boot/collar wear."
            ),
        ),
        ReportSection(
            key="biological_growth",
            title="Biological Growth",
            ai_field="biological_growth",
            fallback_text=(
                "Check for moss, algae, lichen, and vegetation growth that can trap "
                "moisture and accelerate deterioration. Note extent and affected areas."
            ),
        ),
        ReportSection(
            key="overall_assessment",
            title="Overall Condition Rating",
            ai_field="overall_assessment",
            fallback_text=(
                "Overall roof condition rating and estimated remaining service life "
                "require professional assessment. Use the 3D model and imagery provided "
                "for a detailed review by a qualified roofing professional."
            ),
        ),
        ReportSection(key="mesh_stats", title="3D Model Statistics"),
        ReportSection(key="photo_grid", title="Inspection Photography", include_images=True),
        ReportSection(key="observations", title="Additional Findings", ai_field="observations", table_format="findings"),
        ReportSection(key="recommendations", title="Repair Recommendations", ai_field="recommendations"),
        ReportSection(key="deliverables", title="Deliverables"),
        ReportSection(key="methodology", title="Methodology"),
    ],
)


# ─── STRUCTURAL INSPECTION ───────────────────────────────────────────────

STRUCTURES = ReportTemplate(
    report_type="structures",
    title="Structural Inspection Report",
    photo_strategy="oblique_heavy",
    max_ai_photos=8,
    ai_system_addendum=(
        "You specialize in aerial structural condition assessment. "
        "Use engineering inspection terminology and rate deficiency severity."
    ),
    ai_prompt=(
        "Analyze these aerial structural inspection photos. Assess:\n"
        "1. SURFACE CONDITION: deterioration, cracking patterns, spalling, efflorescence\n"
        "2. DEFORMATION: bowing, leaning, sagging, settlement indicators\n"
        "3. CORROSION: rust staining, section loss, exposed reinforcement\n"
        "4. JOINTS/CONNECTIONS: condition, movement, sealant failure\n"
        "5. VEGETATION: intrusion, root damage, biological growth\n"
        "6. DRAINAGE: water staining, erosion at base, ponding\n"
        "7. OVERALL: structural concern level (none/low/moderate/high/critical)"
    ),
    ai_schema={
        "executive_summary": "str",
        "surface_condition": {"overall": "str", "defects": [{"type": "str", "location": "str", "severity": "str"}]},
        "deformation": {"observed": "bool", "details": ["str"]},
        "corrosion": {"observed": "bool", "details": ["str"]},
        "joints_connections": {"condition": "str", "issues": ["str"]},
        "vegetation_intrusion": {"present": "bool", "details": "str"},
        "drainage_issues": {"present": "bool", "details": "str"},
        "structural_concern_level": "none|low|moderate|high|critical",
        "observations": [{"finding": "str", "location": "str", "severity": "str"}],
        "conditions": ["str"],
        "recommendations": ["str"],
        "photo_notes": [{"photo_index": "int", "description": "str"}],
    },
    sections=[
        ReportSection(key="executive_summary", title="Executive Summary", ai_field="executive_summary"),
        ReportSection(key="flight_summary", title="Flight Summary"),
        ReportSection(key="ortho_preview", title="Structure Overview", include_images=True),
        ReportSection(
            key="surface_condition",
            title="Surface Condition",
            ai_field="surface_condition",
            fallback_text=(
                "Inspect all visible surfaces for deterioration, cracking, spalling, "
                "staining, and efflorescence. The 3D model enables close inspection "
                "of areas not safely accessible from ground level."
            ),
        ),
        ReportSection(
            key="deformation",
            title="Deformation & Movement",
            ai_field="deformation",
            fallback_text=(
                "Review the 3D model for bowing, leaning, sagging, or settlement. "
                "Point cloud measurements can quantify deformation relative to "
                "design geometry."
            ),
        ),
        ReportSection(
            key="corrosion",
            title="Corrosion Assessment",
            ai_field="corrosion",
            fallback_text=(
                "Check for rust staining, section loss, exposed reinforcement, "
                "and coating failure. Pay attention to connections, bearing areas, "
                "and locations exposed to moisture."
            ),
        ),
        ReportSection(
            key="joints_connections",
            title="Joints & Connections",
            ai_field="joints_connections",
            fallback_text=(
                "Inspect joints, connections, and sealants for movement, cracking, "
                "or failure. Check expansion joints, control joints, and structural "
                "connections visible in the imagery."
            ),
        ),
        ReportSection(
            key="structural_concern",
            title="Structural Concern Level",
            ai_field="structural_concern_level",
            fallback_text=(
                "Overall structural concern level requires professional engineering "
                "assessment. Use the 3D model and imagery for preliminary review."
            ),
        ),
        ReportSection(key="mesh_stats", title="3D Model Statistics"),
        ReportSection(key="photo_grid", title="Inspection Photography", include_images=True),
        ReportSection(key="observations", title="Findings & Observations", ai_field="observations", table_format="findings"),
        ReportSection(key="recommendations", title="Recommendations", ai_field="recommendations"),
        ReportSection(key="deliverables", title="Deliverables"),
        ReportSection(key="methodology", title="Methodology"),
    ],
)


# ─── VEGETATION ANALYSIS ─────────────────────────────────────────────────

VEGETATION = ReportTemplate(
    report_type="vegetation",
    title="Vegetation Analysis Report",
    photo_strategy="nadir_heavy",
    max_ai_photos=6,
    ai_system_addendum=(
        "You specialize in aerial vegetation assessment. "
        "Focus on canopy health, species groupings, and ecological indicators."
    ),
    ai_prompt=(
        "Analyze these aerial vegetation survey photos. Assess:\n"
        "1. CANOPY HEALTH: color (green=healthy, yellow/brown=stressed), density, gaps\n"
        "2. SPECIES GROUPS: distinct tree types, monocultures vs. mixed stands\n"
        "3. DEAD/DYING: individual trees or clusters showing decline\n"
        "4. INVASIVE INDICATORS: unusual growth patterns, vine coverage\n"
        "5. GROUND COVER: erosion, bare soil, understory density\n"
        "6. WATER FEATURES: streams, ponds, wetland indicators\n"
        "7. LAND USE: cleared areas, trails, roads, structures within vegetation"
    ),
    ai_schema={
        "executive_summary": "str",
        "canopy_health": {"overall_rating": "str", "healthy_pct": "int", "stressed_areas": ["str"]},
        "species_assessment": ["str"],
        "decline_indicators": [{"type": "str", "location": "str", "extent": "str"}],
        "invasive_species": {"detected": "bool", "details": ["str"]},
        "ground_conditions": {"erosion": "str", "bare_soil_pct": "str", "understory": "str"},
        "water_features": ["str"],
        "land_use_notes": ["str"],
        "observations": [{"finding": "str", "location": "str", "severity": "str"}],
        "conditions": ["str"],
        "recommendations": ["str"],
        "photo_notes": [{"photo_index": "int", "description": "str"}],
    },
    sections=[
        ReportSection(key="executive_summary", title="Executive Summary", ai_field="executive_summary"),
        ReportSection(key="flight_summary", title="Flight Summary"),
        ReportSection(key="ortho_preview", title="Vegetation Orthomosaic", include_images=True),
        ReportSection(
            key="canopy_health",
            title="Canopy Health Assessment",
            ai_field="canopy_health",
            fallback_text=(
                "Canopy health assessment from RGB imagery evaluates color (green=healthy, "
                "yellow/brown=stressed), density, and gap patterns. For quantitative NDVI "
                "analysis, multispectral imagery or the Path E vegetation pipeline is required."
            ),
        ),
        ReportSection(
            key="species_assessment",
            title="Species & Stand Composition",
            ai_field="species_assessment",
            fallback_text=(
                "Species identification from aerial RGB imagery is limited to distinct "
                "crown shapes and seasonal color differences. Ground-truth sampling "
                "is recommended for accurate species inventory."
            ),
        ),
        ReportSection(
            key="decline_indicators",
            title="Decline & Mortality",
            ai_field="decline_indicators",
            table_format="findings",
            fallback_text=(
                "Review the orthomosaic for brown/grey canopy areas indicating dead or "
                "dying trees. Compare with previous flights to track decline progression."
            ),
        ),
        ReportSection(
            key="invasive_species",
            title="Invasive Species Indicators",
            ai_field="invasive_species",
            fallback_text=(
                "Look for unusual growth patterns, vine coverage, and monoculture patches "
                "that may indicate invasive species. Ground verification is required for "
                "positive identification."
            ),
        ),
        ReportSection(
            key="ground_conditions",
            title="Ground & Erosion Conditions",
            ai_field="ground_conditions",
            fallback_text=(
                "DSM data can identify erosion channels, bare soil exposure, and drainage "
                "patterns. Compare with DTM for canopy height model derivation."
            ),
        ),
        ReportSection(key="dsm_preview", title="Elevation Map", include_images=True),
        ReportSection(key="photo_grid", title="Survey Photography", include_images=True),
        ReportSection(key="observations", title="Findings & Observations", ai_field="observations", table_format="findings"),
        ReportSection(key="recommendations", title="Recommendations", ai_field="recommendations"),
        ReportSection(key="deliverables", title="Deliverables"),
        ReportSection(key="methodology", title="Methodology"),
    ],
)


# ─── REAL ESTATE ──────────────────────────────────────────────────────────

REAL_ESTATE = ReportTemplate(
    report_type="real_estate",
    title="Real Estate Property Report",
    photo_strategy="balanced",
    max_ai_photos=6,
    ai_system_addendum=(
        "You specialize in real estate aerial photography documentation. "
        "Write in a professional but marketing-friendly tone that highlights "
        "property features and selling points."
    ),
    ai_prompt=(
        "Analyze these aerial property photos for real estate documentation:\n"
        "1. PROPERTY SIZE: visual lot size impression, shape, frontage\n"
        "2. STRUCTURES: main dwelling, outbuildings, garage, covered areas\n"
        "3. OUTDOOR FEATURES: pool, deck, patio, garden, play areas, fire pit\n"
        "4. LANDSCAPING: maturity, maintenance level, notable plantings\n"
        "5. ACCESS: driveway, parking, sidewalks, curb appeal\n"
        "6. NEIGHBORHOOD: proximity to amenities, open space, water features\n"
        "7. MARKETING ANGLES: best aerial views, unique selling points"
    ),
    ai_schema={
        "executive_summary": "str",
        "property_overview": {"lot_impression": "str", "key_features": ["str"]},
        "structures": [{"type": "str", "description": "str"}],
        "outdoor_features": [{"feature": "str", "condition": "str", "marketing_note": "str"}],
        "landscaping": {"quality": "str", "notable": ["str"]},
        "access_parking": {"description": "str"},
        "neighborhood_context": "str",
        "marketing_highlights": ["str"],
        "observations": [{"finding": "str", "location": "str", "severity": "str"}],
        "conditions": ["str"],
        "recommendations": ["str"],
        "photo_notes": [{"photo_index": "int", "description": "str"}],
    },
    sections=[
        ReportSection(key="executive_summary", title="Property Overview", ai_field="executive_summary"),
        ReportSection(key="flight_summary", title="Flight Summary"),
        ReportSection(key="ortho_preview", title="Property Aerial View", include_images=True),
        ReportSection(
            key="property_overview",
            title="Property Features",
            ai_field="property_overview",
            fallback_text=(
                "Aerial imagery showcases the property from perspectives not available "
                "from ground level. The orthomosaic provides a high-resolution bird's eye "
                "view ideal for marketing materials and virtual tours."
            ),
        ),
        ReportSection(
            key="outdoor_features",
            title="Outdoor Living & Amenities",
            ai_field="outdoor_features",
            table_format="findings",
            fallback_text=(
                "Review aerial imagery for outdoor living spaces, pools, decks, "
                "gardens, and recreational features. These highlights enhance "
                "property marketing materials."
            ),
        ),
        ReportSection(
            key="landscaping",
            title="Landscaping & Grounds",
            ai_field="landscaping",
            fallback_text=(
                "Mature landscaping, maintained grounds, and notable plantings "
                "are captured in the overhead view. Use orthomosaic to showcase "
                "property boundaries and lot utilization."
            ),
        ),
        ReportSection(
            key="marketing_highlights",
            title="Marketing Highlights",
            ai_field="marketing_highlights",
            fallback_text=(
                "Aerial photography adds a premium dimension to property listings. "
                "Use the provided imagery for MLS listings, social media, "
                "virtual tours, and printed marketing materials."
            ),
        ),
        ReportSection(key="photo_grid", title="Property Photography", include_images=True),
        ReportSection(key="deliverables", title="Deliverables"),
        ReportSection(key="methodology", title="Methodology"),
    ],
)


# ─── GAUSSIAN SPLAT ───────────────────────────────────────────────────────

GAUSSIAN_SPLAT = ReportTemplate(
    report_type="gaussian_splat",
    title="Gaussian Splat Model Report",
    photo_strategy="balanced",
    max_ai_photos=6,
    ai_system_addendum=(
        "You specialize in 3D reconstruction from aerial photography. "
        "Focus on what the 3D model captures and how it can be used."
    ),
    ai_prompt=(
        "Analyze these aerial photos used for 3D Gaussian Splat reconstruction:\n"
        "1. SUBJECT: what is being modeled (building, site, landscape)\n"
        "2. SURFACES: materials, textures, colors that the model will capture\n"
        "3. GEOMETRY: architectural features, complex shapes, overhangs\n"
        "4. COVERAGE: areas well-captured vs. potential gaps in coverage\n"
        "5. LIGHTING: consistency, shadows that may affect model quality\n"
        "6. USE CASES: what this 3D model is best suited for"
    ),
    ai_schema={
        "executive_summary": "str",
        "subject_description": "str",
        "surface_analysis": {"materials": ["str"], "textures": ["str"]},
        "geometry_notes": ["str"],
        "coverage_assessment": {"well_covered": ["str"], "potential_gaps": ["str"]},
        "lighting_quality": "str",
        "model_use_cases": ["str"],
        "observations": [{"finding": "str", "location": "str", "severity": "str"}],
        "conditions": ["str"],
        "recommendations": ["str"],
        "photo_notes": [{"photo_index": "int", "description": "str"}],
    },
    sections=[
        ReportSection(key="executive_summary", title="Model Summary", ai_field="executive_summary"),
        ReportSection(key="flight_summary", title="Flight Summary"),
        ReportSection(
            key="subject_description",
            title="Subject Description",
            ai_field="subject_description",
            fallback_text=(
                "3D Gaussian Splatting represents scenes as collections of 3D Gaussians "
                "for photorealistic novel-view synthesis with real-time rendering."
            ),
        ),
        ReportSection(
            key="surface_analysis",
            title="Surface & Material Analysis",
            ai_field="surface_analysis",
            fallback_text=(
                "Surface materials and textures are captured with high fidelity in the "
                "Gaussian Splat model. Complex surfaces with specular reflections "
                "may show artifacts in the reconstruction."
            ),
        ),
        ReportSection(
            key="coverage_assessment",
            title="Coverage Assessment",
            ai_field="coverage_assessment",
            fallback_text=(
                "Model completeness depends on photo coverage from multiple angles. "
                "Areas with insufficient overlap may show holes or artifacts. "
                "Review the model for gaps and plan additional flights if needed."
            ),
        ),
        ReportSection(
            key="model_use_cases",
            title="Recommended Use Cases",
            ai_field="model_use_cases",
            fallback_text=(
                "Gaussian Splat models are ideal for: virtual tours, marketing materials, "
                "documentation, and web-based 3D viewing. Load the PLY file in SuperSplat, "
                "Luma AI, or Polycam for interactive exploration."
            ),
        ),
        ReportSection(
            key="processing_details",
            title="Processing Details",
            fallback_text="",  # Populated from mipmap_settings in data
        ),
        ReportSection(key="photo_grid", title="Source Photography", include_images=True),
        ReportSection(key="observations", title="Notes", ai_field="observations", table_format="findings"),
        ReportSection(key="recommendations", title="Recommendations", ai_field="recommendations"),
        ReportSection(key="deliverables", title="Deliverables"),
        ReportSection(key="methodology", title="Methodology"),
    ],
)


# ─── REGISTRY ─────────────────────────────────────────────────────────────

TEMPLATES = {
    "construction_progress": CONSTRUCTION_PROGRESS,
    "property_survey": PROPERTY_SURVEY,
    "roof_inspection": ROOF_INSPECTION,
    "structures": STRUCTURES,
    "vegetation": VEGETATION,
    "real_estate": REAL_ESTATE,
    "gaussian_splat": GAUSSIAN_SPLAT,
}


def get_template(report_type):
    """Get the report template for a given job type.

    Returns ReportTemplate or None if not found.
    """
    return TEMPLATES.get(report_type)
