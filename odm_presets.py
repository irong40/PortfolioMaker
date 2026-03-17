"""
Sentinel Portfolio Maker — ODM Processing Presets

Maps job types to NodeODM processing options, photo filters,
download targets, and report templates.
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

# Shared split-merge options (all presets use these to stay within 12GB VRAM)
_SPLIT_MERGE = [
    {"name": "split", "value": 4},
    {"name": "split-overlap", "value": 150},
    {"name": "sm-cluster", "value": "none"},
]

PRESETS = {
    "construction_progress": {
        "label": "Construction Progress",
        "description": "Orthomosaic + DSM for site progress tracking",
        "photo_filter": "nadir",
        "min_photos": 20,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "dtm", "value": True},
            {"name": "orthophoto-resolution", "value": 5},
            {"name": "fast-orthophoto", "value": False},
            {"name": "auto-boundary", "value": True},
            {"name": "pc-quality", "value": "medium"},
            {"name": "feature-quality", "value": "high"},
        ] + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif"],
        "report_type": "construction_progress",
    },
    "property_survey": {
        "label": "Property Survey",
        "description": "Orthomosaic + DSM + DTM + point cloud for survey",
        "photo_filter": "nadir",
        "min_photos": 20,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "dtm", "value": True},
            {"name": "orthophoto-resolution", "value": 5},
            {"name": "fast-orthophoto", "value": False},
            {"name": "auto-boundary", "value": True},
            {"name": "pc-quality", "value": "high"},
            {"name": "feature-quality", "value": "high"},
        ] + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif", "dtm.tif", "georeferenced_model.laz"],
        "report_type": "property_survey",
    },
    "roof_inspection": {
        "label": "Roof Inspection",
        "description": "Textured 3D mesh for roof condition assessment",
        "photo_filter": None,
        "min_photos": 30,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "mesh-octree-depth", "value": 12},
            {"name": "mesh-size", "value": 300000},
            {"name": "pc-quality", "value": "high"},
            {"name": "feature-quality", "value": "ultra"},
            {"name": "auto-boundary", "value": True},
        ] + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif", "textured_model.zip"],
        "report_type": "roof_inspection",
    },
    "structures": {
        "label": "Structures",
        "description": "3D model + point cloud for structural inspection",
        "photo_filter": None,
        "min_photos": 40,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "mesh-octree-depth", "value": 12},
            {"name": "mesh-size", "value": 300000},
            {"name": "pc-quality", "value": "high"},
            {"name": "feature-quality", "value": "ultra"},
            {"name": "auto-boundary", "value": True},
        ] + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif", "textured_model.zip", "georeferenced_model.laz"],
        "report_type": "structures",
    },
    "vegetation": {
        "label": "Vegetation / Land",
        "description": "Orthomosaic for vegetation analysis (Path E)",
        "photo_filter": "nadir",
        "min_photos": 20,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "orthophoto-resolution", "value": 5},
            {"name": "fast-orthophoto", "value": False},
            {"name": "auto-boundary", "value": True},
            {"name": "pc-quality", "value": "medium"},
            {"name": "feature-quality", "value": "high"},
        ] + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif"],
        "report_type": "vegetation",
    },
    "real_estate": {
        "label": "Real Estate / Marketing",
        "description": "Orthomosaic + 3D model for property showcase",
        "photo_filter": None,
        "min_photos": 20,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "mesh-octree-depth", "value": 11},
            {"name": "mesh-size", "value": 200000},
            {"name": "pc-quality", "value": "medium"},
            {"name": "feature-quality", "value": "high"},
            {"name": "auto-boundary", "value": True},
        ] + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif", "textured_model.zip"],
        "report_type": "real_estate",
    },
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
