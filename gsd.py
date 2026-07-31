"""
Sortie — Measured Ground Sample Distance (GSD)

GSD is a DEPENDENT variable. It falls out of the flight, not out of a
platform datasheet: it depends on altitude above ground, the lens, the
sensor, and — critically — the capture mode, because the same aircraft
writes 8064 px frames in one mode and 4032 px frames in another.

Nothing in this module is nominal. Every number is read from the photo
that was actually taken. When a photo does not carry enough metadata to
measure, this module says so with a machine-readable reason code and
emits no number at all. An unverified claim is worse than no claim.

WHAT IS CLAIMABLE
    gsd_optical_cm  — ground distance per REAL sensor sample. This is the
                      only number allowed on a client-facing surface.
    gsd_output_cm   — ground distance per DELIVERED pixel. Differs from
                      the optical value when digital zoom upsampled the
                      crop back to full frame width. Recorded, never claimed.

WHAT IS NOT CLAIMABLE
    A scalar GSD for an oblique frame. Sampling distance runs from the
    near edge to the horizon; a single number is a fiction. Obliques get
    a near/centre/far band and are excluded from every mission statistic.

    Anything derived from this module may NOT be routed into "survey-grade",
    "accuracy checkpoint" or positional-accuracy language. This is an
    optical sampling distance computed from flight metadata. It says
    nothing about georeferencing accuracy, which requires ground control.
    See ACCURACY_CLAIM_BAR.

FORMULA (true-sensor, width-referenced, aspect-immune)
    swath_m        = (sensor_width_mm / focal_mm) * agl_m
    gsd_output_cm  = swath_m * 100 / width_px
    effective_px   = min(width_px, native_px_width / zoom)
    gsd_optical_cm = swath_m * 100 / effective_px

    The 35mm-equivalent form (36 * agl * 100) / (f35 * width) is the same
    expression with a 3:2 sensor baked into the constant. DJI sensors are
    4:3 and DJI does not re-reference FocalLengthIn35mmFilm when it crops
    to 16:9, so that form runs ~4% HIGH — it reports a COARSER GSD than the
    true-sensor form. Measured on TanRd: 35mm form 1.13839 cm/px vs
    true-sensor 1.09288, i.e. +4.16%. (The ~3.7% figure is the sensor-width
    ratio, 9.677 vs 10.08 mm — a different comparison, opposite direction.)
    Verified against ODM's own self-calibrated cameras — see SENSOR_SPECS.
"""

import math
import os
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from sentinel_core.metadata import extract_xmp_fields

# Stitched drone panoramas exceed Pillow's decompression-bomb limit. Mission
# files are trusted local input; sentinel_core lifts the same cap.
Image.MAX_IMAGE_PIXELS = None

# ─── EXIF TAG NUMBERS ───────────────────────────────────────────────────────

_TAG_MODEL = 0x0110
_EXIF_IFD = 0x8769
_TAG_FOCAL_LENGTH = 0x920A
_TAG_FOCAL_35MM = 0xA405
_TAG_PIXEL_X = 0xA002        # PixelXDimension. NEVER PIL im.size — a DJI DNG
_TAG_PIXEL_Y = 0xA003        # reports its 160x90 embedded preview there.
_TAG_DIGITAL_ZOOM = 0xA404

# ─── CONSTANTS ──────────────────────────────────────────────────────────────

FRAME_35MM_DIAG_MM = 43.26661   # hypot(36, 24)

# Aspect ratio at or above which a frame is a stitched composite rather than
# a single capture. Used ONLY when a camera family has too few frames to
# establish a modal geometry (see modal_geometry). Real 16:9 DJI frames are
# 8064/4536 = 1.7778, so the repo's existing 1.8 panorama threshold sits just
# 1.25% above a legitimate frame — far too tight to reuse here.
FALLBACK_COMPOSITE_ASPECT = 1.90

# A frame whose aspect deviates from its family's modal aspect by more than
# this fraction is a composite. Data-derived, so it self-corrects for cameras
# that do not exist yet.
MODAL_ASPECT_TOLERANCE = 0.05
MIN_FAMILY_FRAMES_FOR_MODE = 5

# Metrology gate. Deliberately stricter than photo_classifier's -70 sorting
# threshold: sorting asks "is this a mapping frame", metrology asks "is a
# single sampling distance physically meaningful across this frame".
NADIR_METROLOGY_PITCH_DEG = 80.0

# Below this the aircraft is on or near the ground and the frame is not a
# mapping capture. Also catches sentinel_core's absent-RelativeAltitude
# default of 0.0, which would otherwise read as an infinitely sharp lie.
MIN_AGL_M = 2.0

# Claim gates. Both must pass before any mission-level number is permitted.
MIN_MEASURED_FRAMES = 5      # below this: T0_unclassified, no claim
MIN_COVERAGE = 0.25          # measured / considered
MIN_RECOMMEND_FRAMES = 20    # before any orthophoto-resolution change

# Stated uncertainty by measurement method. The sensor-table figure is set by
# ODM's own scatter: two archived reconstructions of the same FC8482 solved
# focal lengths 2.7% apart (5583.8 px vs 5435.2 px). We cannot be tighter
# than the reconstruction engine.
UNCERTAINTY_SENSOR_TABLE_PCT = 3.0
UNCERTAINTY_EXIF_DERIVED_PCT = 5.0
UNCERTAINTY_CROPPED_ASPECT_PCT = 9.0

# Predicted GSD is systematically COARSER than the value ODM derives from the
# reconstructed surface, because XMP RelativeAltitude is barometric height
# above the TAKEOFF POINT while ODM measures camera height above the
# reconstructed ground. Measured at HamptonCemetery: predicted 1.093 cm/px
# against odm_processing_statistics average_gsd 0.9586 — 14% coarse, caused
# by mature tree canopy sitting above bare ground. The margin below is set
# comfortably outside that observed systematic so a preset is only coarsened
# when the shortfall is unambiguous.
COARSEN_TRIGGER_RATIO = 1.25

ACCURACY_CLAIM_BAR = (
    "Optical sampling distance computed from flight metadata. This is not a "
    "positional accuracy figure and does not support survey-grade or "
    "accuracy-checkpoint claims, which require ground control."
)

# ─── SENSOR TABLE ───────────────────────────────────────────────────────────
#
# CALIBRATION DATA, NOT DATASHEET TRIVIA.
#
# An entry is only added when its implied focal length in pixels can be
# checked against a real ODM/OpenSfM cameras.json from a completed
# reconstruction. OpenSfM stores focal normalised by the larger image
# dimension, so f_px = focal_x * max(width, height).
#
# Growing this table by datasheet guesswork would recreate exactly the defect
# this module exists to remove (odm_presets' old gsd_200ft_cm, which was
# right for one capture mode and 2x wrong for the other). Do not do it.
#
# Note also that PLATFORM_PROFILES carries a "sensor_size" marketing string
# ("1/1.3in", "4/3in") that is wrong for at least one aircraft. Never derive
# a sensor width from those strings.
#
# Key: (EXIF Model, int(FocalLengthIn35mmFilm)) — read from EACH PHOTO, never
# from ClassificationResult.platform. That field is detected from photos[0]
# alone and stamped onto every frame, so a folder holding two aircraft (or
# whose first walked file is a thumbnail from a prior run) carries one wrong
# platform string for the whole set. Keying on the photo's own EXIF Model
# makes GSD correct regardless. The platform bug itself is out of scope here:
# it drives apply_platform_overrides, i.e. preset semantics.
SENSOR_SPECS = {
    ("FC8482", 24): {
        "sensor_width_mm": 9.677,
        "native_px_width": 8064,
        "label": "DJI Mini 4 Pro wide",
        # 6.72 mm / 9.677 mm * 8064 px = 5599.9 px implied focal.
        # E:\Portfolio\HamptonCemetery\all\cameras.json  focal_x 0.6924393
        #   -> 0.6924393 * 8064 = 5583.8 px   (+0.29%)
        # E:\Portfolio\TrainingPhotos\organized\ortho-output\cameras.json
        #   focal_x 0.6740040 -> 5435.2 px    (+3.03%)
        # The two ODM solutions disagree with each other by 2.7%, which is
        # the noise floor for this check.
        "source": "verified against ODM cameras.json (HamptonCemetery, Training1)",
    },
}

# ─── REASON CODES ───────────────────────────────────────────────────────────
#
# One vocabulary, read identically by the GUI, the manifest, the report and
# any future CRM rule.

REASON_MEASURED = "measured"
REASON_UNREADABLE = "unreadable"
REASON_NO_CAMERA_EXIF = "no_camera_exif"
REASON_NO_PIXEL_DIMENSIONS = "no_pixel_dimensions"
REASON_COMPOSITE_IMAGE = "composite_image"
REASON_NO_FOCAL = "no_focal_length"
REASON_NO_FOCAL_EQUIV = "no_focal_equivalent"
REASON_NO_AGL = "no_agl"
REASON_AGL_BELOW_FLOOR = "agl_below_floor"
REASON_NO_GIMBAL_PITCH = "no_gimbal_pitch"
REASON_RAW_SIDECAR = "raw_sidecar"

REASON_TEXT = {
    REASON_MEASURED: "measured",
    REASON_UNREADABLE: "file could not be opened",
    REASON_NO_CAMERA_EXIF: "no camera EXIF (derived artefact or stripped file)",
    REASON_NO_PIXEL_DIMENSIONS: "no EXIF frame size (RAW/DNG)",
    REASON_COMPOSITE_IMAGE: "stitched composite, not a single frame",
    REASON_NO_FOCAL: "no EXIF focal length",
    REASON_NO_FOCAL_EQUIV: "no 35mm-equivalent focal length",
    REASON_NO_AGL: "no relative altitude in XMP",
    REASON_AGL_BELOW_FLOOR: f"relative altitude below {MIN_AGL_M} m",
    REASON_NO_GIMBAL_PITCH: "no gimbal pitch",
    REASON_RAW_SIDECAR: "RAW sibling of a measured frame",
}

STATUS_OK = "ok"
STATUS_UNMEASURABLE = "unmeasurable"
STATUS_EXCLUDED = "excluded"      # not a failure — removed from both sides
                                  # of the coverage ratio

GEOMETRY_NADIR = "nadir"
GEOMETRY_OBLIQUE = "oblique"

METHOD_SENSOR_TABLE = "sensor_table"
METHOD_EXIF_DERIVED = "exif_derived"

FLAG_DIGITAL_ZOOM = "digital_zoom"
FLAG_ASSUMED_NATIVE_ASPECT = "assumed_native_aspect"
FLAG_CROPPED_ASPECT_SUSPECTED = "cropped_aspect_suspected"

# ─── RESOLUTION TIERS ───────────────────────────────────────────────────────
#
# Named for what the imagery supports, computed on the p95 (the coarsest
# realistic value) of measured nadir frames — the mosaic is resampled onto
# one grid, so the honest bound is set by the coarsest contributing frames.

TIER_BOUNDS = (
    (1.0, "T1_inspection"),
    (2.0, "T2_survey"),
    (4.0, "T3_mapping"),
    (10.0, "T4_overview"),
)
TIER_COARSEST = "T5_reconnaissance"
TIER_UNCLASSIFIED = "T0_unclassified"

TIER_TEXT = {
    "T1_inspection": "sub-centimetre — ASTM D6433 low-severity distress, shingle detail",
    "T2_survey": "boundary and structure survey, canopy delineation",
    "T3_mapping": "progress tracking, volumes, vegetation extent",
    "T4_overview": "context and marketing only — not a measurement product",
    TIER_COARSEST: "situational awareness only — not a measurement product",
    TIER_UNCLASSIFIED: "not classified — too few measured frames",
}

NOT_A_MEASUREMENT_PRODUCT = ("T4_overview", TIER_COARSEST)

CONSISTENCY_UNIFORM = "uniform"
CONSISTENCY_MIXED = "mixed"
CONSISTENCY_STRATIFIED = "stratified"
CONSISTENCY_UNKNOWN = "unknown"


# ─── PER-FRAME RESULT ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class FrameGSD:
    """What is known about the ground sampling of one frame.

    ``gsd_cm`` is populated for NADIR frames only and equals gsd_optical_cm.
    It is the only field a claim may be built from.
    """
    filename: str = ""
    path: str = ""
    status: str = STATUS_UNMEASURABLE
    reason: str = REASON_UNREADABLE
    method: str = None
    geometry: str = None

    model: str = None
    focal_mm: float = None
    focal_equiv_mm: float = None
    sensor_width_mm: float = None
    zoom: float = None
    width_px: int = None
    height_px: int = None
    agl_m: float = None
    pitch_deg: float = None
    half_fov_deg: float = None

    gsd_cm: float = None
    gsd_optical_cm: float = None
    gsd_output_cm: float = None

    gsd_center_cm: float = None
    gsd_near_cm: float = None
    gsd_far_cm: float = None

    uncertainty_pct: float = None
    flags: tuple = ()

    @property
    def measured(self):
        return self.status == STATUS_OK

    @property
    def capture_mode(self):
        """Stable identity for HOW this frame was taken."""
        if not self.measured:
            return None
        return (self.model, self.focal_equiv_mm, self.width_px)

    def as_dict(self):
        return {
            "filename": self.filename,
            "status": self.status,
            "reason": self.reason,
            "method": self.method,
            "geometry": self.geometry,
            "model": self.model,
            "focal_mm": self.focal_mm,
            "focal_equiv_mm": self.focal_equiv_mm,
            "sensor_width_mm": self.sensor_width_mm,
            "zoom": self.zoom,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "agl_m": self.agl_m,
            "pitch_deg": self.pitch_deg,
            "gsd_cm": self.gsd_cm,
            "gsd_optical_cm": self.gsd_optical_cm,
            "gsd_output_cm": self.gsd_output_cm,
            "gsd_center_cm": self.gsd_center_cm,
            "gsd_near_cm": self.gsd_near_cm,
            "gsd_far_cm": self.gsd_far_cm,
            "uncertainty_pct": self.uncertainty_pct,
            "flags": list(self.flags),
        }


def _unmeasurable(path, reason, **extra):
    return FrameGSD(
        filename=os.path.basename(str(path)),
        path=str(path),
        status=STATUS_UNMEASURABLE,
        reason=reason,
        **extra,
    )


# ─── EXIF READING ───────────────────────────────────────────────────────────

def read_camera_geometry(path):
    """Read the camera geometry EXIF a GSD needs, or None if unreadable.

    Returns a dict with model / focal_mm / focal_equiv_mm / width_px /
    height_px / zoom. Any of them may be None; the caller decides which
    absence is fatal.

    Frame size comes from the Exif IFD's PixelXDimension/PixelYDimension
    ONLY. PIL's ``im.size`` reports a DJI DNG's 160x90 embedded preview,
    which would yield a ~57 cm/px "measurement" on half of every card.
    """
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            model = exif.get(_TAG_MODEL)
            sub = exif.get_ifd(_EXIF_IFD)
    except Exception:
        return None

    def _num(value):
        if value is None:
            return None
        try:
            out = float(value)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        if not math.isfinite(out):
            return None
        return out

    def _int(value):
        out = _num(value)
        return int(out) if out else None

    return {
        "model": str(model).strip() if model else None,
        "focal_mm": _num(sub.get(_TAG_FOCAL_LENGTH)),
        "focal_equiv_mm": _num(sub.get(_TAG_FOCAL_35MM)),
        "width_px": _int(sub.get(_TAG_PIXEL_X)),
        "height_px": _int(sub.get(_TAG_PIXEL_Y)),
        "zoom": _num(sub.get(_TAG_DIGITAL_ZOOM)) or 1.0,
    }


def read_agl(path, xmp_fields=None):
    """Return (agl_m, reason). reason is None when the value is usable.

    sentinel_core.extract_xmp_gimbal collapses an ABSENT RelativeAltitude to
    0.0, which downstream reads as a perfect zero-altitude measurement. This
    reader distinguishes absent from present-and-tiny, and it is the only
    altitude path this module uses.
    """
    fields = xmp_fields if xmp_fields is not None else extract_xmp_fields(path)
    if not fields:
        return None, REASON_NO_AGL
    raw = fields.get("RelativeAltitude")
    if raw is None:
        return None, REASON_NO_AGL
    try:
        agl = float(raw)
    except (TypeError, ValueError):
        return None, REASON_NO_AGL
    if not math.isfinite(agl) or agl <= MIN_AGL_M:
        return agl, REASON_AGL_BELOW_FLOOR
    return agl, None


def read_pitch(path, xmp_fields=None):
    """Return gimbal pitch in degrees, or None."""
    fields = xmp_fields if xmp_fields is not None else extract_xmp_fields(path)
    if not fields:
        return None
    raw = fields.get("GimbalPitchDegree")
    if raw is None:
        return None
    try:
        pitch = float(raw)
    except (TypeError, ValueError):
        return None
    return pitch if math.isfinite(pitch) else None


# ─── SENSOR RESOLUTION ──────────────────────────────────────────────────────

def resolve_sensor(geom):
    """Return (sensor_width_mm, native_px_width, method, flags) or None.

    Tier A: a verified (Model, f35) entry in SENSOR_SPECS. Aspect-immune, so
            a 4:3 -> 16:9 in-camera crop needs no special handling.
    Tier B: derive the sensor diagonal from the crop factor and split it at
            the CAPTURED aspect. Carries assumed_native_aspect, and when the
            capture looks like a 16:9 crop of a 4:3 sensor it also carries
            cropped_aspect_suspected and a widened uncertainty, because that
            is the case where the assumption is known to be ~8% wrong.
    """
    model = geom.get("model")
    focal = geom.get("focal_mm")
    f35 = geom.get("focal_equiv_mm")
    width = geom.get("width_px")
    height = geom.get("height_px")

    if not focal or focal <= 0:
        return None

    if model and f35:
        spec = SENSOR_SPECS.get((model, int(round(f35))))
        if spec:
            return (spec["sensor_width_mm"], spec["native_px_width"],
                    METHOD_SENSOR_TABLE, ())

    if not f35 or f35 <= 0 or not width or not height:
        return None

    diag_mm = FRAME_35MM_DIAG_MM * focal / f35
    sensor_width = diag_mm / math.hypot(1.0, height / width)
    flags = [FLAG_ASSUMED_NATIVE_ASPECT]
    aspect = width / height
    if abs(aspect - (16.0 / 9.0)) / (16.0 / 9.0) <= 0.005:
        flags.append(FLAG_CROPPED_ASPECT_SUSPECTED)
    return sensor_width, width, METHOD_EXIF_DERIVED, tuple(flags)


def _uncertainty_for(method, flags):
    if FLAG_CROPPED_ASPECT_SUSPECTED in flags:
        return UNCERTAINTY_CROPPED_ASPECT_PCT
    if method == METHOD_SENSOR_TABLE:
        return UNCERTAINTY_SENSOR_TABLE_PCT
    return UNCERTAINTY_EXIF_DERIVED_PCT


# ─── COMPOSITE DETECTION ────────────────────────────────────────────────────

def modal_geometry(geometries):
    """Return {(model, f35_int): (width, height)} — the modal frame shape.

    A DJI in-camera panorama carries the FULL source-camera EXIF (same model,
    same FocalLength, same f35) with the mosaic's pixel dimensions, so naive
    maths reports 0.026 cm/px on it. Rather than hard-code an aspect ratio,
    take the most common frame shape within each camera family as that
    camera's native frame and flag deviations. Self-corrects for cameras that
    do not exist yet.
    """
    families = {}
    for geom in geometries:
        if not geom:
            continue
        model = geom.get("model")
        f35 = geom.get("focal_equiv_mm")
        width = geom.get("width_px")
        height = geom.get("height_px")
        if not model or not f35 or not width or not height:
            continue
        key = (model, int(round(f35)))
        families.setdefault(key, {})
        shape = (width, height)
        families[key][shape] = families[key].get(shape, 0) + 1

    modes = {}
    for key, shapes in families.items():
        if sum(shapes.values()) < MIN_FAMILY_FRAMES_FOR_MODE:
            continue
        # Deterministic: highest count, then largest pixel count.
        best = sorted(shapes.items(), key=lambda kv: (kv[1], kv[0][0] * kv[0][1]))[-1]
        modes[key] = best[0]
    return modes


def is_composite(geom, modes=None):
    """Whether this frame is a stitched composite rather than a capture."""
    width = geom.get("width_px")
    height = geom.get("height_px")
    if not width or not height:
        return False
    aspect = width / height

    model = geom.get("model")
    f35 = geom.get("focal_equiv_mm")
    if modes and model and f35:
        native = modes.get((model, int(round(f35))))
        if native:
            native_aspect = native[0] / native[1]
            return abs(aspect - native_aspect) / native_aspect > MODAL_ASPECT_TOLERANCE

    return aspect >= FALLBACK_COMPOSITE_ASPECT


# ─── PER-FRAME MEASUREMENT ──────────────────────────────────────────────────

def frame_gsd(path, *, pitch=None, xmp_fields=None, geometry=None, modes=None):
    """Measure one frame. Never raises, never guesses.

    Args:
        path: photo path.
        pitch: gimbal pitch if already known (avoids an XMP read).
        xmp_fields: pre-read drone-dji XMP fields, if the caller has them.
        geometry: pre-read read_camera_geometry() dict, if the caller has it.
        modes: modal_geometry() map for data-derived composite detection.
    """
    geom = geometry if geometry is not None else read_camera_geometry(path)
    if geom is None:
        return _unmeasurable(path, REASON_UNREADABLE)

    if not geom.get("model") and not geom.get("focal_mm"):
        return _unmeasurable(path, REASON_NO_CAMERA_EXIF)

    if not geom.get("width_px") or not geom.get("height_px"):
        return _unmeasurable(path, REASON_NO_PIXEL_DIMENSIONS,
                             model=geom.get("model"))

    if is_composite(geom, modes):
        return _unmeasurable(path, REASON_COMPOSITE_IMAGE,
                             model=geom.get("model"),
                             width_px=geom.get("width_px"),
                             height_px=geom.get("height_px"))

    if not geom.get("focal_mm"):
        return _unmeasurable(path, REASON_NO_FOCAL, model=geom.get("model"))

    sensor = resolve_sensor(geom)
    if sensor is None:
        return _unmeasurable(path, REASON_NO_FOCAL_EQUIV, model=geom.get("model"))
    sensor_width_mm, native_px, method, flags = sensor

    agl, agl_reason = read_agl(path, xmp_fields)
    if agl_reason:
        return _unmeasurable(path, agl_reason, model=geom.get("model"),
                             agl_m=agl, width_px=geom.get("width_px"),
                             height_px=geom.get("height_px"))

    if pitch is None:
        pitch = read_pitch(path, xmp_fields)
    if pitch is None:
        return _unmeasurable(path, REASON_NO_GIMBAL_PITCH, model=geom.get("model"),
                             agl_m=agl, width_px=geom.get("width_px"),
                             height_px=geom.get("height_px"))

    width_px = geom["width_px"]
    height_px = geom["height_px"]
    focal_mm = geom["focal_mm"]
    zoom = geom.get("zoom") or 1.0
    if zoom <= 0:
        zoom = 1.0

    swath_m = (sensor_width_mm / focal_mm) * agl / zoom
    gsd_output = swath_m * 100.0 / width_px

    # Digital zoom crops the sensor and upsamples back to full frame width,
    # so the ground swath really does shrink but the number of REAL samples
    # across it does not. Only the optical value is claimable.
    effective_px = min(width_px, native_px / zoom)
    gsd_optical = swath_m * 100.0 / effective_px

    if zoom != 1.0:
        flags = tuple(flags) + (FLAG_DIGITAL_ZOOM,)

    uncertainty = _uncertainty_for(method, flags)

    # Half field of view in the tilt (along-track) plane.
    sensor_height_mm = sensor_width_mm * height_px / width_px
    half_fov = math.degrees(math.atan((sensor_height_mm / zoom) / (2.0 * focal_mm)))

    common = dict(
        filename=os.path.basename(str(path)),
        path=str(path),
        status=STATUS_OK,
        reason=REASON_MEASURED,
        method=method,
        model=geom.get("model"),
        focal_mm=focal_mm,
        focal_equiv_mm=geom.get("focal_equiv_mm"),
        sensor_width_mm=sensor_width_mm,
        zoom=zoom,
        width_px=width_px,
        height_px=height_px,
        agl_m=agl,
        pitch_deg=pitch,
        half_fov_deg=half_fov,
        gsd_optical_cm=gsd_optical,
        gsd_output_cm=gsd_output,
        uncertainty_pct=uncertainty,
        flags=tuple(flags),
    )

    if abs(pitch) >= NADIR_METROLOGY_PITCH_DEG:
        return FrameGSD(geometry=GEOMETRY_NADIR, gsd_cm=gsd_optical, **common)

    # Oblique: report the band, never a scalar. Depression angle a from
    # horizontal; along-track sampling distance is gsd_nadir / sin^2(a),
    # unbounded as the far edge approaches the horizon.
    tilt = abs(pitch)
    near = _oblique_gsd(gsd_optical, tilt + half_fov)
    center = _oblique_gsd(gsd_optical, tilt)
    far = _oblique_gsd(gsd_optical, tilt - half_fov)
    return FrameGSD(geometry=GEOMETRY_OBLIQUE, gsd_cm=None,
                    gsd_near_cm=near, gsd_center_cm=center, gsd_far_cm=far,
                    **common)


def _oblique_gsd(gsd_nadir_cm, depression_deg):
    """Along-track sampling distance at a depression angle, or None."""
    if depression_deg <= 0 or depression_deg > 180:
        return None
    s = math.sin(math.radians(min(depression_deg, 90.0)))
    if s <= 0:
        return None
    return gsd_nadir_cm / (s * s)


def gsd_for_meta(meta, *, geometry=None, modes=None):
    """Measure the frame behind a PhotoMeta-like object."""
    return frame_gsd(meta.path, pitch=getattr(meta, "pitch", None),
                     geometry=geometry, modes=modes)


def measure_photos(metas):
    """Measure a list of PhotoMeta-like objects.

    Two passes: read every frame's geometry, derive each camera family's
    modal frame shape, then measure. Finally, pair RAW sidecars against their
    measured JPEG siblings so the coverage ratio is not dragged down by files
    that are the same capture already counted.
    """
    metas = list(metas)
    geometries = [read_camera_geometry(m.path) for m in metas]
    modes = modal_geometry(geometries)
    frames = [
        frame_gsd(m.path, pitch=getattr(m, "pitch", None),
                  geometry=geom, modes=modes)
        for m, geom in zip(metas, geometries)
    ]
    return pair_raw_sidecars(frames)


RAW_SUFFIXES = {".dng", ".raw", ".arw", ".nef", ".cr2", ".cr3", ".raf", ".rw2"}


def pair_raw_sidecars(frames):
    """Reclassify RAW files that have a measured sibling as ``excluded``.

    A DJI card holds DJI_..._D.DNG beside DJI_..._D.JPG — one capture, two
    files. The JPEG carries the frame geometry; the DNG does not. Counting
    the DNG as an unmeasurable failure makes a textbook grid read "112 of
    224 measured", which looks like a bug. Removing it from BOTH sides of
    the ratio is honest and infers nothing: the DNG still gets no GSD.
    """
    measured_keys = set()
    for f in frames:
        if f.status == STATUS_OK:
            p = Path(f.path)
            measured_keys.add((str(p.parent).lower(), p.stem.lower()))

    out = []
    for f in frames:
        p = Path(f.path)
        if (f.status == STATUS_UNMEASURABLE
                and p.suffix.lower() in RAW_SUFFIXES
                and (str(p.parent).lower(), p.stem.lower()) in measured_keys):
            out.append(FrameGSD(filename=f.filename, path=f.path,
                                status=STATUS_EXCLUDED,
                                reason=REASON_RAW_SIDECAR,
                                model=f.model))
        else:
            out.append(f)
    return out


# ─── MISSION SUMMARY ────────────────────────────────────────────────────────

@dataclass
class GSDSummary:
    """A mission-level GSD answer, or an honest refusal.

    This is a pure function of a photo list. It is deliberately NOT stored on
    ClassificationResult: the working set that goes to ODM is a filtered
    subset, and a stored summary would silently describe the wrong set.
    """
    measured: int = 0
    unmeasurable: int = 0
    excluded: int = 0
    oblique_excluded: int = 0
    considered: int = 0
    population_considered: int = 0
    coverage: float = 0.0
    sufficient: bool = False

    reasons: dict = field(default_factory=dict)
    method_mix: dict = field(default_factory=dict)

    min_cm: float = None
    p05_cm: float = None
    median_cm: float = None
    p95_cm: float = None
    max_cm: float = None

    uniformity_ratio: float = None
    consistency_class: str = CONSISTENCY_UNKNOWN
    resolution_tier: str = TIER_UNCLASSIFIED

    agl_min_m: float = None
    agl_max_m: float = None
    capture_modes: list = field(default_factory=list)
    uncertainty_pct: float = None
    warnings: list = field(default_factory=list)

    @property
    def dominant_reason(self):
        unmet = {k: v for k, v in self.reasons.items()
                 if k not in (REASON_MEASURED, REASON_RAW_SIDECAR)}
        if not unmet:
            return None
        return max(sorted(unmet.items()), key=lambda kv: kv[1])[0]

    def reason_histogram(self):
        """'115 no EXIF frame size (RAW/DNG), 3 no gimbal pitch'."""
        parts = []
        for code, count in sorted(self.reasons.items(),
                                  key=lambda kv: (-kv[1], kv[0])):
            # RAW sidecars are not failures — they left both sides of the
            # coverage ratio and are named separately in coverage_line().
            if code in (REASON_MEASURED, REASON_RAW_SIDECAR):
                continue
            parts.append(f"{count} {REASON_TEXT.get(code, code)}")
        return ", ".join(parts)

    def headline(self):
        """One honest line — a number with its uncertainty, or a refusal."""
        if not self.sufficient:
            hist = self.reason_histogram()
            denom = self.population_considered or self.considered
            if not denom:
                return "GSD: not measured — no photos"
            tail = f" — {hist}" if hist else ""
            # Denominator is the scanned card, matching the gate that refused.
            # Reporting the filtered count here read "14 of 14 measurable"
            # directly beneath a refusal, which looks like a bug.
            return (f"GSD: not measured ({self.measured} of {denom} "
                    f"frames measurable){tail}")
        return (f"GSD: {self.median_cm:.2f} cm/px median "
                f"({self.min_cm:.2f}-{self.max_cm:.2f}, p95 {self.p95_cm:.2f}) "
                f"\u00b1{self.uncertainty_pct:.0f}% — {self.resolution_tier}, "
                f"{self.consistency_class} ({self.uniformity_ratio:.2f}x)")

    def coverage_line(self):
        """Coverage never travels without its reason histogram."""
        denom = self.population_considered or self.considered
        base = f"Measured: {self.measured} of {denom} photos"
        bits = []
        hist = self.reason_histogram()
        if hist:
            bits.append(hist)
        if self.oblique_excluded:
            bits.append(f"{self.oblique_excluded} oblique (no scalar GSD)")
        if self.excluded:
            bits.append(f"{self.excluded} RAW sidecars paired out")
        return base + (" — " + "; ".join(bits) if bits else "")

    def as_dict(self):
        return {
            "measured": self.measured,
            "unmeasurable": self.unmeasurable,
            "excluded": self.excluded,
            "oblique_excluded": self.oblique_excluded,
            "considered": self.considered,
            "coverage": self.coverage,
            "sufficient": self.sufficient,
            "reasons": dict(self.reasons),
            "method_mix": dict(self.method_mix),
            "min_cm": self.min_cm,
            "p05_cm": self.p05_cm,
            "median_cm": self.median_cm,
            "p95_cm": self.p95_cm,
            "max_cm": self.max_cm,
            "uniformity_ratio": self.uniformity_ratio,
            "consistency_class": self.consistency_class,
            "resolution_tier": self.resolution_tier,
            "agl_min_m": self.agl_min_m,
            "agl_max_m": self.agl_max_m,
            "capture_modes": list(self.capture_modes),
            "uncertainty_pct": self.uncertainty_pct,
            "warnings": list(self.warnings),
            "basis": "predicted_from_exif",
            "accuracy_note": ACCURACY_CLAIM_BAR,
            "headline": self.headline(),
        }


def _percentile(values, q):
    """Linear-interpolated percentile on a sorted copy. q in [0, 1]."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def tier_for(p95_cm, measured_count):
    if p95_cm is None or measured_count < MIN_MEASURED_FRAMES:
        return TIER_UNCLASSIFIED
    for bound, name in TIER_BOUNDS:
        if p95_cm < bound:
            return name
    return TIER_COARSEST


def consistency_for(ratio, measured_count):
    if ratio is None or measured_count < MIN_MEASURED_FRAMES:
        return CONSISTENCY_UNKNOWN
    if ratio <= 1.15:
        return CONSISTENCY_UNIFORM
    if ratio <= 1.50:
        return CONSISTENCY_MIXED
    return CONSISTENCY_STRATIFIED


def summarize(frames, population=None):
    """Reduce per-frame results to one mission answer. Pure, total, safe.

    Statistics run over measured NADIR frames only. Obliques are counted and
    named but never contribute — a scalar GSD is not defined for them.

    ``population`` is the frame list the mission was scanned from, before any
    working-set filtering. The coverage GATE is evaluated against it; the
    statistics still describe ``frames``. Without it the gate is near-inert on
    the only path that ships: portfolio_service and sortie both summarize the
    working set, which filter_photos has already reduced to nadir, so the
    denominator excludes exactly the frames that would fail the gate. Measured
    on Training1: the same 14 frames read coverage 1.000 / claim permitted off
    the working set, and 14-of-101 / no claim off the card. Pass the card.
    """
    frames = [f for f in frames if f is not None]
    summary = GSDSummary()

    reasons = {}
    for f in frames:
        reasons[f.reason] = reasons.get(f.reason, 0) + 1
    summary.reasons = reasons

    summary.excluded = sum(1 for f in frames if f.status == STATUS_EXCLUDED)
    scored = [f for f in frames if f.status != STATUS_EXCLUDED]
    summary.considered = len(scored)

    ok = [f for f in scored if f.status == STATUS_OK]
    nadir = [f for f in ok if f.geometry == GEOMETRY_NADIR and f.gsd_cm]
    summary.oblique_excluded = sum(1 for f in ok if f.geometry == GEOMETRY_OBLIQUE)
    summary.measured = len(nadir)
    summary.unmeasurable = sum(1 for f in scored if f.status == STATUS_UNMEASURABLE)

    # Coverage gate denominator: the scanned population when the caller supplies
    # it, otherwise this list. Statistics below always describe `frames`.
    if population is not None:
        pop = [f for f in population if f is not None]
        pop_scored = [f for f in pop if f.status != STATUS_EXCLUDED]
        summary.population_considered = len(pop_scored)
    else:
        summary.population_considered = summary.considered
    denom = summary.population_considered
    summary.coverage = (summary.measured / denom) if denom else 0.0

    if not nadir:
        return summary

    values = [f.gsd_cm for f in nadir]
    summary.min_cm = min(values)
    summary.max_cm = max(values)
    summary.median_cm = statistics.median(values)
    summary.p05_cm = _percentile(values, 0.05)
    summary.p95_cm = _percentile(values, 0.95)
    if summary.p05_cm and summary.p05_cm > 0:
        summary.uniformity_ratio = summary.p95_cm / summary.p05_cm

    agls = [f.agl_m for f in nadir if f.agl_m is not None]
    if agls:
        summary.agl_min_m = min(agls)
        summary.agl_max_m = max(agls)

    method_mix = {}
    for f in nadir:
        method_mix[f.method] = method_mix.get(f.method, 0) + 1
    summary.method_mix = method_mix
    summary.uncertainty_pct = max(f.uncertainty_pct for f in nadir)

    modes = {}
    for f in nadir:
        modes.setdefault(f.capture_mode, []).append(f.gsd_cm)
    summary.capture_modes = [
        {
            "model": key[0],
            "focal_equiv_mm": key[1],
            "width_px": key[2],
            "count": len(vals),
            "median_cm": statistics.median(vals),
        }
        for key, vals in sorted(modes.items(), key=lambda kv: str(kv[0]))
    ]

    summary.resolution_tier = tier_for(summary.p95_cm, summary.measured)
    summary.consistency_class = consistency_for(summary.uniformity_ratio,
                                                summary.measured)
    summary.sufficient = (summary.measured >= MIN_MEASURED_FRAMES
                          and summary.coverage >= MIN_COVERAGE)

    warnings = []
    if not summary.sufficient:
        if summary.measured < MIN_MEASURED_FRAMES:
            warnings.append(
                f"only {summary.measured} measured nadir frames "
                f"(need {MIN_MEASURED_FRAMES}) — no GSD claim permitted")
        if summary.coverage < MIN_COVERAGE:
            warnings.append(
                f"measurement coverage {summary.coverage:.0%} below "
                f"{MIN_COVERAGE:.0%} — no GSD claim permitted")
    if len(summary.capture_modes) > 1:
        warnings.append(
            f"multi_sensor: {len(summary.capture_modes)} camera configurations "
            f"in one set")
    if METHOD_EXIF_DERIVED in method_mix:
        warnings.append(
            f"{method_mix[METHOD_EXIF_DERIVED]} frame(s) used the EXIF-derived "
            f"sensor fallback — no verified sensor entry for that camera")
    if summary.consistency_class == CONSISTENCY_STRATIFIED:
        warnings.append(
            "stratified: distinct altitude or capture blocks — the mosaic is "
            "not a single-resolution product")
    summary.warnings = warnings
    return summary


def summarize_photos(photos, population_photos=None):
    """Summarize from PhotoMeta-like objects carrying a ``gsd`` FrameGSD.

    This is the ONLY way to get a mission number. It is computed on demand
    from whatever list the caller holds, so a filtered working set and the
    whole card can never disagree about which one the number describes.
    """
    frames = [getattr(p, "gsd", None) for p in photos]
    pop = None
    if population_photos is not None:
        pop = [getattr(p, "gsd", None) for p in population_photos]
        pop = [f for f in pop if f is not None]
    return summarize([f for f in frames if f is not None], population=pop)


# ─── PRE-FLIGHT INVERSE ─────────────────────────────────────────────────────

def altitude_for_target_gsd_m(target_gsd_cm, *, focal_mm, width_px,
                              sensor_width_mm):
    """AGL that would achieve target_gsd_cm with this camera geometry."""
    if not target_gsd_cm or not focal_mm or not width_px or not sensor_width_mm:
        return None
    return target_gsd_cm * focal_mm * width_px / (sensor_width_mm * 100.0)


def recommended_agl_m(summary, target_gsd_cm):
    """Re-fly altitude for a target GSD, scaled off the measured flight.

    GSD is linear in AGL for a fixed capture mode, so scaling the highest
    measured altitude by target/coarsest needs no camera geometry beyond what
    was already measured. When several capture modes are present the highest
    altitude and the coarsest GSD may belong to different frames and this
    becomes approximate — which is why a multi-mode set also raises the
    multi_sensor warning. It is a warning string, never a claim.

    Both terms MUST come from the same percentile. Pairing agl_max_m (p100)
    with p95_cm understated the required descent by exactly agl_max/agl_p95 —
    14.5% on the real FoodLion set, and in the permissive direction: the
    operator re-flies too high and misses the target again. Use max_cm (p100)
    so the recommendation is the conservative end of the measured spread.
    """
    if not summary.sufficient or not summary.max_cm or not summary.agl_max_m:
        return None
    if not target_gsd_cm or target_gsd_cm <= 0:
        return None
    return summary.agl_max_m * (target_gsd_cm / summary.max_cm)


# ─── PRESET INTEGRATION ─────────────────────────────────────────────────────

def orthophoto_resolution_of(preset):
    """The preset's requested orthophoto resolution in cm/px, or None."""
    for opt in preset.get("odm_options", ()):
        if opt.get("name") == "orthophoto-resolution":
            try:
                return float(opt["value"])
            except (TypeError, ValueError, KeyError):
                return None
    return None


def preset_resolution_conflict(summary, preset):
    """LAYER 1 — default ON, warning only, changes nothing.

    Returns a warning string when the preset asks for finer detail than the
    flight can support, ending with a re-fly altitude so the operator gets an
    instruction rather than a complaint. Returns None when there is no
    conflict or no claim is permitted.
    """
    if not summary.sufficient:
        return None
    requested = orthophoto_resolution_of(preset)
    if requested is None or summary.p95_cm is None:
        return None
    if summary.p95_cm <= requested:
        return None

    agl = recommended_agl_m(summary, requested)
    label = preset.get("label", "this preset")
    msg = (f"{label} requests {requested:g} cm/px, but this flight supports "
           f"{summary.p95_cm:.2f} cm/px (p95 of {summary.measured} measured "
           f"nadir frames, \u00b1{summary.uncertainty_pct:.0f}%). Asking for "
           f"finer than the capture fabricates detail that is not in the "
           f"pixels.")
    if agl:
        msg += (f" Re-fly at or below {agl:.0f} m AGL "
                f"({agl * 3.28084:.0f} ft) or accept a coarser ortho.")
    return msg


def _ceil_to(value, step):
    return math.ceil(value / step - 1e-9) * step


def apply_measured_resolution(preset, summary):
    """LAYER 2a — coarsen-only, DEFAULT ON. Mutates preset in place.

    Refusing to request finer than measured is the half of the recommender
    that must never be opt-in: ODM will happily emit a raster labelled 1 cm
    that carries 2.4 cm of information, and the report would then state a
    resolution the deliverable does not have.

    Refining coarser -> finer is NOT done here. 2 cm is a deliberate product
    decision in some presets (construction wants date-to-date comparability),
    so that direction stays with recommended_orthophoto_resolution().

    Only fires when the shortfall clears COARSEN_TRIGGER_RATIO, which sits
    outside the measured predicted-vs-delivered systematic (see the constant).

    Returns (applied_value_or_None, reason).
    """
    requested = orthophoto_resolution_of(preset)
    if requested is None:
        return None, "preset has no orthophoto-resolution"
    if not summary.sufficient:
        return None, "unmeasured — preset unchanged"
    if summary.measured < MIN_RECOMMEND_FRAMES:
        return None, (f"only {summary.measured} measured nadir frames "
                      f"(need {MIN_RECOMMEND_FRAMES}) — preset unchanged")
    if summary.p95_cm is None or summary.p95_cm <= requested * COARSEN_TRIGGER_RATIO:
        return None, "measured resolution supports the preset — unchanged"

    value = _ceil_to(summary.p95_cm, 0.1)
    for opt in preset.get("odm_options", ()):
        if opt.get("name") == "orthophoto-resolution":
            opt["value"] = value
            break
    return value, (f"coarsened {requested:g} -> {value:g} cm/px to match "
                   f"measured p95 {summary.p95_cm:.2f} cm/px "
                   f"({summary.measured} nadir frames)")


def recommended_orthophoto_resolution(summary, preset):
    """LAYER 2b — OPT-IN, advisory only. Never mutates anything.

    Reports the resolution the capture would actually support, including the
    finer-than-preset direction. Throwing away real pixels is wasteful rather
    than dishonest, and coarser output is a deliberate product decision in
    some presets, so this one is Adam's call and not automatic.
    """
    if not summary.sufficient:
        return None, f"unmeasured — {summary.headline()}"
    if summary.measured < MIN_RECOMMEND_FRAMES:
        return None, (f"only {summary.measured} measured nadir frames "
                      f"(need {MIN_RECOMMEND_FRAMES})")
    if summary.consistency_class == CONSISTENCY_STRATIFIED:
        return None, ("stratified capture — one resolution does not describe "
                      "this mosaic")
    value = _ceil_to(summary.p95_cm, 0.5)
    requested = orthophoto_resolution_of(preset)
    if requested is not None and value < requested:
        return value, (f"capture supports {value:g} cm/px; preset requests "
                       f"{requested:g} cm/px — {(1 - value / requested):.0%} of "
                       f"the linear resolution would be discarded")
    return value, f"measured p95 {summary.p95_cm:.2f} cm/px, rounded up to 0.5 cm"


# ─── DELIVERED RASTER ───────────────────────────────────────────────────────

def achieved_gsd_from_raster(path):
    """Measure the pixel size of a delivered GeoTIFF, or None.

    This is the only number that is a property of the thing the client
    received; a predicted value must never occupy the report's resolution row
    when this exists. Returns None on a geographic CRS — degrees are not
    converted to centimetres.
    """
    if not path or not os.path.exists(str(path)):
        return None
    try:
        import rasterio
    except ImportError:
        return None
    try:
        with rasterio.open(str(path)) as src:
            crs = src.crs
            if crs is None or not crs.is_projected:
                return None
            units = None
            try:
                units = crs.linear_units
            except Exception:
                units = None
            transform = src.transform
            if transform is None or transform.a == 0:
                return None
            per_px = abs(transform.a)
            factor = {"metre": 100.0, "meter": 100.0, "m": 100.0,
                      "us survey foot": 30.48006, "foot": 30.48,
                      "ft": 30.48}.get(str(units).lower())
            if factor is None:
                return None
            return {
                "gsd_cm": per_px * factor,
                "crs": str(crs),
                "units": str(units),
                "width": src.width,
                "height": src.height,
                "source": os.path.basename(str(path)),
                "basis": "delivered_orthophoto",
            }
    except Exception:
        return None


# ─── SURFACE TEXT ───────────────────────────────────────────────────────────

def summary_lines(summary, preset=None, achieved=None):
    """The operator-facing block. One builder so GUI, CLI and log cannot drift."""
    lines = [summary.headline(), summary.coverage_line()]
    # These lines carry a median cm/px. When the claim gates refused, they were
    # still emitted unconditionally — 806Mead printed "GSD: not measured (2 of
    # 31)" and then "median 2.26 cm/px" two lines below it. A refusal that is
    # immediately followed by the number it refused to state is not a refusal.
    # Mark them as the unclaimable sample they are.
    tag = "" if summary.sufficient else "  [below claim threshold — not a mission GSD]"
    if summary.capture_modes:
        for mode in summary.capture_modes:
            lines.append(
                f"Camera:    {mode['model']} {mode['width_px']} px @ "
                f"{mode['focal_equiv_mm']:g} mm-eq, n={mode['count']}, "
                f"median {mode['median_cm']:.2f} cm/px{tag}")
    if summary.agl_min_m is not None:
        lines.append(f"Altitude:  {summary.agl_min_m:.1f}-{summary.agl_max_m:.1f} m AGL "
                     f"(barometric, relative to takeoff)")
    for warning in summary.warnings:
        lines.append(f"Warning:   {warning}")
    if preset is not None:
        conflict = preset_resolution_conflict(summary, preset)
        if conflict:
            lines.append(f"Warning:   {conflict}")
    if achieved:
        lines.append(f"Delivered: {achieved['gsd_cm']:.2f} cm/px "
                     f"(measured from {achieved['source']})")
    return lines


def report_resolution_row(summary=None, achieved=None):
    """The client report's resolution row, strictly ranked.

    1. Measured on the delivered raster — the only number that is a property
       of what the client received.
    2. Predicted from flight metadata, with the qualifier fused into the
       string so it cannot be separated from the number.
    3. Not measured.
    """
    if achieved and achieved.get("gsd_cm"):
        # A raster's pixel size is a PROCESSING PARAMETER, not information
        # content: ODM will happily emit a 1.00 cm/px raster from a flight that
        # only sampled the ground every 1.25 cm. Returning early here hid that
        # entirely — on the real FoodLion pavement set the report said
        # "1.00 cm/pixel (measured from the delivered GeoTIFF)" and the measured
        # 1.2494 appeared nowhere. State both; never let the raster stand alone.
        # The delivered raster stays the headline — that ranking is correct and
        # deliberate. The ONLY case that must not stand alone is the over-claim:
        # a raster resampled FINER than the capture sampled the ground.
        text = (f"{achieved['gsd_cm']:.2f} cm/pixel "
                f"(measured from the delivered GeoTIFF)")
        if (summary is not None and summary.sufficient and summary.p95_cm
                and summary.p95_cm > achieved["gsd_cm"]):
            text += (f"; flight metadata predicts "
                     f"{summary.median_cm:.2f} cm/pixel median "
                     f"(p95 {summary.p95_cm:.2f}, "
                     f"±{summary.uncertainty_pct:.0f}%), so the raster is "
                     f"resampled finer than the capture sampled the ground "
                     f"— the extra pixels are not extra detail")
        return ["Orthomosaic Resolution", text]
    if summary is not None and summary.sufficient:
        return ["Predicted Ground Sample Distance",
                f"{summary.median_cm:.2f} cm/pixel median "
                f"(p95 {summary.p95_cm:.2f}, \u00b1{summary.uncertainty_pct:.0f}%) "
                f"\u2014 predicted from flight metadata, not measured on a "
                f"delivered raster"]
    return ["Ground Sample Distance", "Not measured"]


def methodology_sentence(summary=None, achieved=None):
    """One sentence naming the basis actually used. Never claims accuracy."""
    if achieved and achieved.get("gsd_cm"):
        base = ("Orthomosaic resolution was measured from the delivered "
                "GeoTIFF's own geotransform.")
    elif summary is not None and summary.sufficient:
        base = ("Ground sample distance was computed per frame from the "
                "recorded barometric altitude above the takeoff point and the "
                "camera's own focal length and frame size, then reduced over "
                f"{summary.measured} nadir frames; it is predicted from flight "
                "metadata rather than measured on a delivered raster.")
        if summary.resolution_tier in NOT_A_MEASUREMENT_PRODUCT:
            base += (" At this sampling distance the imagery is a visual "
                     "product and not a measurement product.")
    else:
        return None
    return base + " " + ACCURACY_CLAIM_BAR
