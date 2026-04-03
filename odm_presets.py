"""
Sortie — ODM Processing Presets (ODM 3.5.6)

Maps job types to NodeODM processing options, photo filters,
download targets, and report templates.

Hardware target: i7-14700F, RTX 5060 Ti 12GB, 32GB RAM
Camera: DJI M4E Wide (mechanical shutter, ~2.2 cm/px GSD at 200ft ACL)

Each preset is tuned for its specific deliverable and use case.
See SOP-002A for flight altitude / overlap guidance.
"""

import copy

# Ordered list for GUI display: (key, label)
JOB_TYPES = [
    ("construction_progress", "Construction Progress"),
    ("property_survey", "Property Survey"),
    ("roof_inspection", "Roof Inspection"),
    ("structures", "Structures"),
    ("vegetation", "Vegetation / Land"),
    ("real_estate", "Real Estate / Marketing"),
    ("gaussian_splat", "Gaussian Splat"),
]

# ── Shared option blocks ────────────────────────────────────────────────────

# Split-merge: keeps each submodel within 12GB VRAM.
# split-overlap 250m ensures generous blending zone to minimize seam artifacts.
_SPLIT_MERGE = [
    {"name": "split", "value": 200},
    {"name": "split-overlap", "value": 250},
    {"name": "sm-cluster", "value": "http://localhost:3000"},
]

# Deliverable output options — COG for QGIS/web, overviews for fast display
_OUTPUT_OPTS = [
    {"name": "cog", "value": True},
    {"name": "build-overviews", "value": True},
]

PRESETS = {
    # ── Construction Progress ────────────────────────────────────────────
    # Weekly revisits for site progress tracking. Consistency across dates
    # matters more than absolute max quality. Global seam leveling stays ON
    # (default) to normalize color between passes/dates.
    # sfm-algorithm: triangulation is faster and more accurate for nadir grids.
    "construction_progress": {
        "label": "Construction Progress",
        "description": "Orthomosaic + DSM for site progress tracking",
        "photo_filter": "nadir",
        "min_photos": 20,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "dtm", "value": True},
            {"name": "orthophoto-resolution", "value": 2},
            {"name": "dem-resolution", "value": 2},
            {"name": "pc-quality", "value": "high"},
            {"name": "feature-quality", "value": "high"},
            {"name": "min-num-features", "value": 12000},
            {"name": "crop", "value": 3},
            {"name": "auto-boundary", "value": True},
            {"name": "pc-classify", "value": True},
            {"name": "sfm-algorithm", "value": "triangulation"},
            {"name": "orthophoto-cutline", "value": True},
        ] + _OUTPUT_OPTS + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif"],
        "report_type": "construction_progress",
    },

    # ── Property Survey ──────────────────────────────────────────────────
    # Accuracy is the priority. Ultra feature + high pc gives best alignment
    # without the 8.5x time penalty of ultra pc-quality. pc-classify +
    # pc-rectify are required for proper DTM ground classification.
    # gps-accuracy set for RTK (override in GUI if using standard GPS).
    "property_survey": {
        "label": "Property Survey",
        "description": "Orthomosaic + DSM + DTM + point cloud for survey",
        "photo_filter": "nadir",
        "min_photos": 20,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "dtm", "value": True},
            {"name": "orthophoto-resolution", "value": 2},
            {"name": "dem-resolution", "value": 2},
            {"name": "pc-quality", "value": "high"},
            {"name": "feature-quality", "value": "ultra"},
            {"name": "min-num-features", "value": 16000},
            {"name": "crop", "value": 3},
            {"name": "auto-boundary", "value": True},
            {"name": "pc-classify", "value": True},
            {"name": "pc-rectify", "value": True},
            {"name": "sfm-algorithm", "value": "triangulation"},
            {"name": "orthophoto-cutline", "value": True},
            {"name": "gps-accuracy", "value": 0.02},
            {"name": "pc-las", "value": True},
        ] + _OUTPUT_OPTS + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif", "dtm.tif", "georeferenced_model.laz"],
        "report_type": "property_survey",
    },

    # ── Roof Inspection ──────────────────────────────────────────────────
    # Textured 3D mesh for damage assessment. use-3dmesh is critical —
    # roofs are NOT flat, 2.5D loses gutter/edge detail. Ultra features
    # needed to match shingle-level detail on uniform surfaces. High mesh
    # vertex count. sky-removal prevents sky bleed in oblique shots.
    # crop 0 because edge-to-edge roof coverage is mandatory.
    # sfm-algorithm: incremental because mixed nadir+oblique capture.
    "roof_inspection": {
        "label": "Roof Inspection",
        "description": "Textured 3D mesh for roof condition assessment",
        "photo_filter": None,
        "min_photos": 30,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "use-3dmesh", "value": True},
            {"name": "mesh-octree-depth", "value": 12},
            {"name": "mesh-size", "value": 500000},
            {"name": "orthophoto-resolution", "value": 1},
            {"name": "pc-quality", "value": "high"},
            {"name": "feature-quality", "value": "ultra"},
            {"name": "min-num-features", "value": 16000},
            {"name": "crop", "value": 0},
            {"name": "auto-boundary", "value": True},
            {"name": "sky-removal", "value": True},
            {"name": "gltf", "value": True},
        ] + _OUTPUT_OPTS + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif", "textured_model.zip"],
        "report_type": "roof_inspection",
    },

    # ── Structures ───────────────────────────────────────────────────────
    # Full 3D reconstruction of buildings/bridges. Highest mesh detail.
    # use-3dmesh for vertical surfaces and overhangs. 3d-tiles for web
    # viewing. sfm-algorithm: incremental required for mixed oblique+nadir.
    # sky-removal critical for oblique shots against sky background.
    "structures": {
        "label": "Structures",
        "description": "3D model + point cloud for structural inspection",
        "photo_filter": None,
        "min_photos": 40,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "use-3dmesh", "value": True},
            {"name": "mesh-octree-depth", "value": 12},
            {"name": "mesh-size", "value": 600000},
            {"name": "pc-quality", "value": "high"},
            {"name": "feature-quality", "value": "ultra"},
            {"name": "min-num-features", "value": 16000},
            {"name": "crop", "value": 0},
            {"name": "auto-boundary", "value": True},
            {"name": "sky-removal", "value": True},
            {"name": "pc-las", "value": True},
            {"name": "gltf", "value": True},
            {"name": "3d-tiles", "value": True},
        ] + _OUTPUT_OPTS + _SPLIT_MERGE,
        "downloads": [
            "orthophoto.tif", "dsm.tif",
            "textured_model.zip", "georeferenced_model.laz",
        ],
        "report_type": "structures",
    },

    # ── Vegetation / Land ────────────────────────────────────────────────
    # Orthomosaic for visual vegetation assessment and Path E (DeepForest).
    # Consistent GSD across the mosaic is critical for canopy detection —
    # see SOP-002A. skip-3dmodel saves significant time when only ortho
    # is needed. For NDVI with multispectral sensor, user should enable
    # radiometric-calibration and texturing-skip-global-seam-leveling
    # manually via Advanced settings.
    # sfm-algorithm: planar is fastest for flat terrain nadir-only grids.
    "vegetation": {
        "label": "Vegetation / Land",
        "description": "Orthomosaic for vegetation analysis (Path E)",
        "photo_filter": "nadir",
        "min_photos": 20,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "orthophoto-resolution", "value": 2},
            {"name": "dem-resolution", "value": 3},
            {"name": "pc-quality", "value": "high"},
            {"name": "feature-quality", "value": "high"},
            {"name": "min-num-features", "value": 14000},
            {"name": "crop", "value": 3},
            {"name": "auto-boundary", "value": True},
            {"name": "skip-3dmodel", "value": True},
            {"name": "pc-classify", "value": True},
            {"name": "sfm-algorithm", "value": "planar"},
            {"name": "orthophoto-cutline", "value": True},
        ] + _OUTPUT_OPTS + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif"],
        "report_type": "vegetation",
    },

    # ── Real Estate / Marketing ──────────────────────────────────────────
    # Visual appeal is the priority — clean color, smooth geometry. use-3dmesh
    # for properties with vertical surfaces (houses, fences). Global seam
    # leveling stays ON to normalize color. 3d-tiles + gltf for web embed.
    # High quality (not ultra) gives excellent visual results with
    # reasonable turnaround.
    "real_estate": {
        "label": "Real Estate / Marketing",
        "description": "Orthomosaic + 3D model for property showcase",
        "photo_filter": None,
        "min_photos": 20,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "use-3dmesh", "value": True},
            {"name": "mesh-octree-depth", "value": 11},
            {"name": "mesh-size", "value": 300000},
            {"name": "orthophoto-resolution", "value": 2},
            {"name": "pc-quality", "value": "high"},
            {"name": "feature-quality", "value": "high"},
            {"name": "min-num-features", "value": 12000},
            {"name": "crop", "value": 3},
            {"name": "auto-boundary", "value": True},
            {"name": "gltf", "value": True},
            {"name": "3d-tiles", "value": True},
            {"name": "orthophoto-cutline", "value": True},
        ] + _OUTPUT_OPTS + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif", "textured_model.zip"],
        "report_type": "real_estate",
    },

    # ── Gaussian Splat ───────────────────────────────────────────────────
    # Processed via MipMap Desktop, not NodeODM.
    "gaussian_splat": {
        "label": "Gaussian Splat",
        "description": "3D Gaussian Splat via MipMap Desktop",
        "photo_filter": None,
        "min_photos": 50,
        "engine": "mipmap",
        "odm_options": [],
        "downloads": ["gs_ply", "gs_sog_tiles"],
        "report_type": "gaussian_splat",
        "mipmap_settings": {
            "resolution_level": 3,
            "mesh_decimate_ratio": 0.5,
        },
    },
}


def get_preset(job_type):
    """Return a deep copy of the preset for the given job type.

    Raises KeyError if job_type is not valid.
    """
    return copy.deepcopy(PRESETS[job_type])
