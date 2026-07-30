"""
Sortie — ODM Processing Presets (ODM 3.5.6)

Maps job types to NodeODM processing options, photo filters,
download targets, and report templates.

Hardware target: i7-14700F, RTX 5070 12GB, 32GB RAM
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
    ("panorama", "Panorama / 360"),
    ("pavement", "Parking Lot / Pavement"),
    ("steeple", "Steeple / Spire"),
    ("church_campus", "Church Campus"),
]

# Engines that run entirely on this machine. Every other engine value means
# "submit to NodeODM", so this is the one place the distinction lives.
LOCAL_ENGINES = ("mipmap", "local")


def engine_requires_nodeodm(engine):
    """Return whether a processing engine needs the NodeODM service."""
    return engine not in LOCAL_ENGINES


def delivers_gis(preset):
    """Whether GIS exports go to the client, derived from the orthomosaic.

    Policy (Adam, 2026-07-30): GIS exports — photo points and flight
    tracks — are part of ANY mission that produces an orthomosaic. If the
    client is getting a georeferenced raster they can open in QGIS, they
    get the sidecars that make it useful.

    This supersedes the hand-kept `gis_delivery` flag locked 2026-07-12,
    which listed only survey, construction and vegetation. That flag had
    drifted: roof_inspection, structures and real_estate all ship an
    orthophoto and were silently routing their GIS exports to the
    internal _gis/ directory, which drive_delivery skips. Deriving it
    from `downloads` means a new preset cannot forget to opt in.
    """
    return any("orthophoto" in target for target in preset.get("downloads", ()))

# ── Shared option blocks ────────────────────────────────────────────────────

# Split-merge: keeps each submodel's memory footprint bounded.
# split-overlap 250m ensures generous blending zone to minimize seam artifacts.
# No sm-cluster: pointing it at the same single node (localhost:3000,
# maxParallelTasks=1) just upload-churns every submodel against the parent
# task's occupied slot ("Delayed task limit reached" loop) before ODM falls
# back to local processing anyway. Submodels process locally, sequentially.
_SPLIT_MERGE = [
    {"name": "split", "value": 200},
    {"name": "split-overlap", "value": 250},
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
        # GIS exports (photo points, tracks, KML) go in the client delivery
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
        # Post-ODM headless QGIS VARI analysis (vegetation_analysis.py)
        "vegetation_analysis": True,
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
    # NodeODM runs SfM for camera poses, then the self-built OpenSplat CUDA
    # container trains the splat (opensplat_service). Replaced MipMap
    # 2026-07-23 (free tier caps at 512 images; CLI needs a rotating
    # per-GUI-session token — no headless path).
    #
    # CRITICAL (D12): NO split/split-overlap here — split-merge scatters
    # opensfm/ under submodels/ and the pose files never land at the
    # project root. Never add _SPLIT_MERGE or optimize-disk-space.
    # The task is submitted with outputs=[the two opensfm paths] (see
    # process_job), so its all.zip is a few MB of poses, not deliverables.
    # dsm=false + skip-orthophoto verified E2E 2026-07-22 (task b038d432);
    # skip-3dmodel added to save hours of unused mesh work on big sets —
    # verified at Quailshire scale before production (Phase 4 gate).
    "gaussian_splat": {
        "label": "Gaussian Splat",
        "description": "3D Gaussian Splat via NodeODM SfM + OpenSplat (GPU)",
        "photo_filter": None,
        "min_photos": 20,
        "engine": "opensplat",
        "odm_options": [
            {"name": "dsm", "value": False},
            {"name": "skip-orthophoto", "value": True},
            {"name": "skip-3dmodel", "value": True},
        ],
        "downloads": ["gs_ply"],
        "report_type": "gaussian_splat",
        "opensplat_settings": {
            # d>=2 is a hard floor on 12GB VRAM: d=1 at 20MP stalled the
            # card at 11.7GB even with 23 photos (measured 2026-07-22).
            "num_iters": 30000,
            "downscale_factor": 2,
        },
    },

    # ── Panorama / 360 ─────────────────────────────────────────────────────
    # Fully local: DJI pre-stitched fast path or the OpenCV worker. This
    # preset must never receive platform-specific ODM options.
    "panorama": {
        "label": "Panorama / 360",
        "description": "Local 360 panorama stitching and web viewer export",
        "photo_filter": None,
        "min_photos": 8,
        "engine": "local",
        "odm_options": [],
        "downloads": [],
        "report_type": "panorama",
    },

    # ── Parking Lot / Pavement ────────────────────────────────────────────
    # ASTM D6433. The orthomosaic IS the deliverable here: sample units are
    # measured off it, so resolution beats every other consideration and
    # orthophoto-resolution 1 is not negotiable. Flown as two nadir passes
    # per SOP-002B (wide grid, then medium-tele detail), so photo_filter
    # stays nadir and both passes land in the same reconstruction.
    # crop 0 because curbs, entrances and lot edges are in scope — an
    # auto-cropped ortho loses exactly the distresses at the boundary.
    # DSM retained for ponding and rutting, which read as depressions.
    # sfm-algorithm: triangulation, correct for flat nadir-only grids.
    "pavement": {
        "label": "Parking Lot / Pavement",
        "description": "High-resolution orthomosaic for ASTM D6433 distress inventory",
        "photo_filter": "nadir",
        "min_photos": 30,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "orthophoto-resolution", "value": 1},
            {"name": "dem-resolution", "value": 2},
            {"name": "pc-quality", "value": "high"},
            {"name": "feature-quality", "value": "ultra"},
            {"name": "min-num-features", "value": 16000},
            {"name": "crop", "value": 0},
            {"name": "auto-boundary", "value": True},
            {"name": "orthophoto-cutline", "value": True},
            {"name": "sfm-algorithm", "value": "triangulation"},
            {"name": "gps-accuracy", "value": 0.02},
            {"name": "pc-las", "value": True},
        ] + _OUTPUT_OPTS + _SPLIT_MERGE,
        "downloads": ["orthophoto.tif", "dsm.tif", "georeferenced_model.laz"],
        "report_type": "pavement",
    },

    # ── Steeple / Spire ───────────────────────────────────────────────────
    # A steeple job is a roof job plus the six added zones in
    # report-system-spec-v1 §5.5. Capture is the roof sequence followed by
    # two overlapping orbit rings and one close oblique per zone, so the set
    # is oblique-heavy and sky-removal is mandatory — a spire is shot almost
    # entirely against sky. use-3dmesh for the vertical geometry.
    # min_photos 60: roof grid + two rings + six zone close-ups cannot come
    # in under that, and a short set means a missing zone, which is a re-fly.
    #
    # The spec's second deliverable is the splat viewer link. That is a
    # SEPARATE gaussian_splat run over the same photos, not a second engine
    # here — photogrammetry mangles finials and crockets, which is the whole
    # reason the splat exists. Run steeple for the report evidence, then
    # gaussian_splat for the viewer.
    "steeple": {
        "label": "Steeple / Spire",
        "description": "Roof mesh + steeple zone documentation (pair with a splat run)",
        "photo_filter": None,
        "min_photos": 60,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "use-3dmesh", "value": True},
            {"name": "mesh-octree-depth", "value": 12},
            {"name": "mesh-size", "value": 600000},
            {"name": "orthophoto-resolution", "value": 1},
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
        "report_type": "steeple",
    },

    # ── Church Campus ─────────────────────────────────────────────────────
    # The whole-campus bundle: sanctuary roof and steeple, parking lot
    # pavement, and a grounds orthomosaic in one visit. The capture is
    # genuinely mixed — nadir grid over grounds and lot, oblique orbits over
    # the building — so no photo_filter, incremental SfM (the default for
    # mixed sets), and both the ortho and the mesh matter.
    #
    # orthophoto-resolution 1 rather than 2 because the lot inside this
    # bundle is still scored against D6433, and a 2 cm ortho cannot carry a
    # low-severity crack. That makes this the most expensive preset in the
    # file; it is a full-day deliverable, priced accordingly.
    "church_campus": {
        "label": "Church Campus",
        "description": "Roof + steeple + lot + grounds — whole-campus documentation",
        "photo_filter": None,
        "min_photos": 80,
        "odm_options": [
            {"name": "dsm", "value": True},
            {"name": "dtm", "value": True},
            {"name": "use-3dmesh", "value": True},
            {"name": "mesh-octree-depth", "value": 12},
            {"name": "mesh-size", "value": 600000},
            {"name": "orthophoto-resolution", "value": 1},
            {"name": "dem-resolution", "value": 2},
            {"name": "pc-quality", "value": "high"},
            {"name": "feature-quality", "value": "ultra"},
            {"name": "min-num-features", "value": 16000},
            {"name": "crop", "value": 0},
            {"name": "auto-boundary", "value": True},
            {"name": "sky-removal", "value": True},
            {"name": "orthophoto-cutline", "value": True},
            {"name": "pc-classify", "value": True},
            {"name": "pc-las", "value": True},
            {"name": "gltf", "value": True},
            {"name": "3d-tiles", "value": True},
        ] + _OUTPUT_OPTS + _SPLIT_MERGE,
        "downloads": [
            "orthophoto.tif", "dsm.tif", "dtm.tif",
            "textured_model.zip", "georeferenced_model.laz",
        ],
        "report_type": "church_campus",
    },
}


# ── Platform-specific ODM overrides ─────────────────────────────────────────
#
# Each platform profile describes the drone's sensor characteristics.
# apply_platform_overrides() merges these into any preset's odm_options
# so the processing matches the actual hardware.

PLATFORM_PROFILES = {
    "mini4pro": {
        "label": "DJI Mini 4 Pro",
        "shutter": "electronic",       # Rolling shutter CMOS
        "has_rtk": False,               # Consumer GPS only
        "sensor_size": "1/1.3in",
        "gsd_200ft_cm": 2.18,           # GSD at 200ft ACL (48MP mode)
        "odm_overrides": [
            {"name": "rolling-shutter", "value": True},
            {"name": "gps-accuracy", "value": 5},
        ],
    },
    "m4e": {
        "label": "DJI Matrice 4E",
        "shutter": "mechanical",        # Global / mechanical shutter
        "has_rtk": True,
        "sensor_size": "1/1.3in",
        "gsd_200ft_cm": 2.18,
        "odm_overrides": [
            # Mechanical shutter — no rolling-shutter correction needed
            {"name": "gps-accuracy", "value": 0.02},
        ],
    },
    "m3e": {
        "label": "DJI Mavic 3 Enterprise",
        "shutter": "mechanical",        # Mechanical shutter on wide camera
        "has_rtk": True,
        "sensor_size": "4/3in",
        "gsd_200ft_cm": 1.25,
        "odm_overrides": [
            {"name": "gps-accuracy", "value": 0.02},
        ],
    },
}


def apply_platform_overrides(preset, platform):
    """Apply platform-specific ODM option overrides to a preset.

    Merges the platform's odm_overrides into the preset's odm_options,
    replacing any existing option with the same name.

    Args:
        preset: A preset dict (already deep-copied via get_preset).
        platform: Platform string (e.g. "mini4pro", "m4e", "m3e") or None.

    Returns:
        The modified preset (same dict, mutated in place).
    """
    if not platform or platform not in PLATFORM_PROFILES:
        return preset

    profile = PLATFORM_PROFILES[platform]
    overrides = profile.get("odm_overrides", [])

    # Local engines never touch NodeODM, so ODM overrides are meaningless.
    # opensplat DOES run NodeODM SfM — platform overrides (gps-accuracy,
    # rolling-shutter) improve its poses, so they intentionally apply.
    if not overrides or preset.get("engine") in LOCAL_ENGINES:
        return preset

    existing_names = {o["name"] for o in preset["odm_options"]}

    for override in overrides:
        name = override["name"]
        if name in existing_names:
            # Replace existing value
            for opt in preset["odm_options"]:
                if opt["name"] == name:
                    opt["value"] = override["value"]
                    break
        else:
            # Add new option
            preset["odm_options"].append(copy.deepcopy(override))

    return preset


def get_preset(job_type, platform=None):
    """Return a deep copy of the preset for the given job type.

    If platform is provided, applies platform-specific overrides
    (e.g. rolling-shutter for Mini 4 Pro, RTK GPS accuracy for M4E).

    Raises KeyError if job_type is not valid.
    """
    preset = copy.deepcopy(PRESETS[job_type])
    if platform:
        apply_platform_overrides(preset, platform)
    return preset
