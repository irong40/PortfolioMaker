"""CRM link — read missions from, and write status back to, the Sentinel CRM.

The CRM (FaithandHarmony admin app) and sortie share one Supabase project.
This module is the only place sortie talks to it, and everything fails soft:
missing credentials or an unreachable network mean is_configured()/fetch
return empty and the GUI stays fully manual. Processing must never break
because the CRM is down.

Credentials come from SUPABASE_URL / SUPABASE_SERVICE_KEY environment
variables (or a .env in the repo root / user home — same lookup report_ai
uses). Never put keys in sortie_settings.json; that file is tracked by git.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10

# CRM statuses that mean "flown or about to fly, not yet processed" —
# these are the missions worth showing in the dropdown.
OPEN_STATUSES = ("intake", "scheduled", "captured", "uploaded", "ingested")

# CRM processing_templates.preset_name -> sortie job type (odm_presets key).
# None means "no ODM preset applies — leave the radio selection alone".
PRESET_TO_JOB_TYPE = {
    "mapping": "property_survey",
    "vegetation": "vegetation",
    "construction": "construction_progress",
    "hybrid_bc": "construction_progress",
    "adiat": "roof_inspection",
    "thermal_inspection": "roof_inspection",
    "re_basic": "real_estate",
    "re_pro": "real_estate",
    "re_standard": "real_estate",
    "basic": "real_estate",
    "standard": "real_estate",
    "premium": "real_estate",
    "luxury": "real_estate",
    "commercial": "real_estate",
    "video": None,
    "wildlife_census_thermal": None,
}

# Columns sortie is allowed to write back. job_number is deliberately
# absent — the CRM owns naming (DB trigger), and historical pipeline code
# clobbered it via upsert.
WRITABLE_FIELDS = frozenset({
    "status",
    "processing_started_at",
    "processing_completed_at",
    "processing_error",
    "photogrammetry_status",
    "nodeodm_task_id",
    "output_path",
    "orthophoto_path",
    "pointcloud_path",
    "model_file_path",
    "photo_count",
    "video_count",
    "delivery_status",
    "delivery_drive_url",
})


def _load_env():
    """Load .env files (repo root, then home) without overwriting existing env."""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / ".env", override=False)
        load_dotenv(Path.home() / ".env", override=False)
    except ImportError:
        pass


def _credentials():
    _load_env()
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    return url, key


def is_configured():
    """True when Supabase credentials are present in the environment."""
    url, key = _credentials()
    return bool(url and key)


def _headers(key):
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CrmMission:
    id: str
    job_number: str
    address: str = ""
    city: str = ""
    state: str = ""
    property_type: str = ""
    status: str = ""
    scheduled_date: str = ""
    scheduled_time: str = ""
    client_name: str = ""
    client_company: str = ""
    preset_name: str = ""
    path_code: str = ""
    template_name: str = ""
    pilot_notes: str = ""
    admin_notes: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def label(self):
        """One-line dropdown label: job number, address, date, status."""
        parts = [self.job_number or self.id[:8]]
        if self.address:
            parts.append(self.address)
        if self.scheduled_date:
            parts.append(self.scheduled_date)
        parts.append(self.status)
        return " — ".join(p for p in parts if p)

    def suggested_job_type(self):
        """Sortie job type for this mission's CRM template, or None."""
        return PRESET_TO_JOB_TYPE.get(self.preset_name)

    def suggested_site_name(self):
        """Street portion of the address ('2237 Shillelagh Rd'), else job number."""
        street = self.address.split(",")[0].strip()
        return street or self.job_number


def _parse_mission(row):
    client = row.get("clients") or {}
    template = row.get("processing_templates") or {}
    return CrmMission(
        id=row.get("id", ""),
        job_number=row.get("job_number") or "",
        address=row.get("site_address") or row.get("property_address") or "",
        city=row.get("property_city") or "",
        state=row.get("property_state") or "",
        property_type=row.get("property_type") or "",
        status=row.get("status") or "",
        scheduled_date=row.get("scheduled_date") or "",
        scheduled_time=row.get("scheduled_time") or "",
        client_name=client.get("name") or "",
        client_company=client.get("company") or "",
        preset_name=template.get("preset_name") or "",
        path_code=template.get("path_code") or "",
        template_name=template.get("display_name") or "",
        pilot_notes=row.get("pilot_notes") or "",
        admin_notes=row.get("admin_notes") or "",
        raw=row,
    )


def fetch_open_missions(timeout=REQUEST_TIMEOUT):
    """Return open CRM missions (newest schedule first). [] on any failure."""
    url, key = _credentials()
    if not (url and key):
        return []

    params = {
        "select": ("id,job_number,property_address,property_city,property_state,"
                   "site_address,property_type,status,scheduled_date,scheduled_time,"
                   "pilot_notes,admin_notes,"
                   "clients(name,company),"
                   "processing_templates(preset_name,path_code,display_name)"),
        "status": f"in.({','.join(OPEN_STATUSES)})",
        "order": "scheduled_date.desc.nullslast",
        "limit": "50",
    }
    try:
        resp = requests.get(f"{url}/rest/v1/drone_jobs", headers=_headers(key),
                            params=params, timeout=timeout)
        resp.raise_for_status()
        return [_parse_mission(row) for row in resp.json()]
    except Exception as e:
        log.warning("CRM mission fetch failed: %s", e)
        return []


def update_mission(job_id, fields, timeout=REQUEST_TIMEOUT):
    """PATCH allowed fields onto a drone_jobs row. Returns True on success.

    Silently drops non-writable fields; never raises — a CRM hiccup must not
    kill a processing run.
    """
    url, key = _credentials()
    if not (url and key and job_id):
        return False

    payload = {k: v for k, v in fields.items() if k in WRITABLE_FIELDS}
    dropped = set(fields) - set(payload)
    if dropped:
        log.warning("CRM update: dropped non-writable fields %s", sorted(dropped))
    if not payload:
        return False

    try:
        resp = requests.patch(f"{url}/rest/v1/drone_jobs", headers=_headers(key),
                              params={"id": f"eq.{job_id}"}, json=payload,
                              timeout=timeout)
        resp.raise_for_status()
        return True
    except Exception as e:
        detail = ""
        body = getattr(getattr(e, "response", None), "text", None)
        if body:
            detail = f" — {body[:300]}"
        log.warning("CRM update failed for %s: %s%s", job_id, e, detail)
        return False


# ─── Stage helpers (called from the GUI worker thread) ─────────────────────

def mark_processing(job_id, photo_count=None):
    fields = {
        "status": "processing",
        "processing_started_at": _utc_now(),
        "processing_error": None,
        "photogrammetry_status": "processing",
    }
    if photo_count:
        fields["photo_count"] = photo_count
    return update_mission(job_id, fields)


def mark_complete(job_id, result):
    """Record a successful process_job() run: outputs + timestamps."""
    downloaded = result.get("downloaded") or {}
    fields = {
        "status": "complete",
        "processing_completed_at": _utc_now(),
        # drone_jobs.status and photogrammetry_status are DIFFERENT enums:
        # status uses 'complete', photogrammetry_status uses 'completed'.
        "photogrammetry_status": "completed",
        "output_path": str(result.get("output_dir") or ""),
        "delivery_status": "ready",
    }
    if result.get("task_uuid"):
        fields["nodeodm_task_id"] = result["task_uuid"]
    for name, path in downloaded.items():
        lowered = name.lower()
        if "orthophoto" in lowered:
            fields["orthophoto_path"] = str(path)
        elif "point_cloud" in lowered or "georeferenced_model" in lowered:
            fields["pointcloud_path"] = str(path)
        elif "textured_model" in lowered or lowered.endswith((".glb", ".gltf")):
            fields["model_file_path"] = str(path)
    return update_mission(job_id, fields)


def mark_failed(job_id, error):
    return update_mission(job_id, {
        "status": "failed",
        "photogrammetry_status": "failed",
        "processing_error": str(error)[:1000],
    })


# ─── Report push (sortie → CRM report builder) ─────────────────────────────

# sortie report_type -> CRM report_templates.code. The CRM template is the
# single source of truth for sections; sortie prefills job_reports.section_data
# and the app renders/edits it. gaussian_splat has no client report template.
REPORT_TEMPLATE_CODES = {
    "construction_progress": "construction_progress",
    "property_survey": "property_survey",
    "roof_inspection": "roof_property_inspection",
    "structures": "structures_inspection",
    "vegetation": "vegetation_analysis",
    "real_estate": "re_aerial_photography",
}

PLATFORM_NAMES = {
    "mini4pro": "DJI Mini 4 Pro",
    "m4e": "DJI Matrice 4E",
    "m3e": "DJI Mavic 3E",
}

MAX_REPORT_IMAGES = 12

_IMAGE_EXTS = (".jpg", ".jpeg", ".png")

_AI_FINDING_FIELDS = {
    "vegetation": [("canopy_health", "Canopy Health"),
                   ("species_assessment", "Species & Stand Composition"),
                   ("decline_indicators", "Decline & Mortality Indicators"),
                   ("invasive_species", "Invasive Species Indicators"),
                   ("ground_conditions", "Ground & Erosion Conditions"),
                   ("water_features", "Water Features")],
    "property_survey": [("boundaries", "Boundary Analysis"),
                        ("encroachments", "Encroachment Assessment"),
                        ("terrain_analysis", "Terrain & Drainage"),
                        ("structures_inventory", "Structures Inventory"),
                        ("easements", "Easements & Access")],
    "structures": [("surface_condition", "Surface Condition"),
                   ("deformation", "Deformation & Movement"),
                   ("corrosion", "Corrosion Assessment"),
                   ("joints_connections", "Joints & Connections"),
                   ("drainage_issues", "Drainage Issues"),
                   ("structural_concern_level", "Overall Concern Level")],
    "construction_progress": [("construction_phase", "Construction Phase"),
                              ("earthwork", "Earthwork & Grading"),
                              ("foundations", "Foundations & Structures"),
                              ("site_logistics", "Site Logistics & Equipment"),
                              ("safety_compliance", "Safety & Environmental")],
    "roof_inspection": [("surface_condition", "Roofing Material"),
                        ("damage", "Damage Findings"),
                        ("drainage_issues", "Drainage & Gutters")],
}


def _iso_date(value):
    """Best-effort ISO date from sortie's date_str ('2026-07-13' or '20260713')."""
    s = str(value or "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s or datetime.now(timezone.utc).date().isoformat()


def _get_template(code, timeout=REQUEST_TIMEOUT):
    url, key = _credentials()
    try:
        resp = requests.get(f"{url}/rest/v1/report_templates",
                            headers=_headers(key),
                            params={"code": f"eq.{code}",
                                    "select": "id,name,sections_manifest",
                                    "limit": "1"},
                            timeout=timeout)
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None
    except Exception as e:
        log.warning("CRM template lookup failed (%s): %s", code, e)
        return None


def _finding(idx, title, description, severity="info"):
    return {
        "id": f"SRT-{idx:02d}",
        "severity": severity,
        "title": title,
        "description": str(description),
        "review_status": "pending",
    }


def _build_findings(job_type, ai):
    """Map sortie's AI analysis onto the CRM findings section."""
    findings = []
    for ai_key, title in _AI_FINDING_FIELDS.get(job_type, []):
        value = (ai or {}).get(ai_key)
        if value:
            findings.append(_finding(len(findings) + 1, title, value))
    for obs in (ai or {}).get("observations") or []:
        text = str(obs)
        findings.append(_finding(len(findings) + 1, text[:60], text))
    return findings


def _file_entry(name, path):
    entry = {"filename": name, "type": _deliverable_type(name)}
    try:
        size = os.path.getsize(path)
        entry["file_size"] = f"{size / (1024 * 1024):.1f} MB"
    except OSError:
        pass
    return entry


def _deliverable_type(name):
    lowered = name.lower()
    if "orthophoto" in lowered or "ortho" in lowered:
        return "Orthomosaic"
    if lowered.endswith((".tif", ".tiff")):
        return "GeoTIFF"
    if lowered.endswith((".laz", ".las", ".ply")):
        return "Point Cloud"
    if lowered.endswith((".glb", ".gltf", ".zip")) and "model" in lowered:
        return "3D Model"
    if lowered.endswith(_IMAGE_EXTS):
        return "Photo"
    if lowered.endswith((".kml", ".kmz", ".geojson", ".csv")):
        return "GIS Export"
    if lowered.endswith(".mp4"):
        return "Video"
    return "File"


def build_report_payload(mission, result, template_name):
    """Assemble (section_data, active_sections, images) from a process_job
    result. images is a list of (section_key, local_path, caption)."""
    rd = result.get("report_data") or {}
    ai = rd.get("ai_analysis") or {}
    imgs = rd.get("images") or {}
    pc = rd.get("pc_results") or {}
    veg = rd.get("veg_results") or {}
    job_type = rd.get("job_type", "")
    date_iso = _iso_date(rd.get("date"))
    site = rd.get("site_name") or mission.suggested_site_name()

    section_data = {}
    images = []

    section_data["cover_page"] = {
        "title": f"{template_name} — {site}",
        "client_name": mission.client_name or "",
        "client_company": mission.client_company or "",
        "property_address": ", ".join(
            p for p in (mission.address, mission.city, mission.state) if p),
        "report_date": date_iso,
        "job_number": mission.job_number,
        "pilot_name": "Adam Pierce",
        "prepared_by": "Adam Pierce, Founder",
        "classification": "CLIENT CONFIDENTIAL",
    }

    metrics = [{"label": "Photos captured", "value": str(rd.get("total_photos", 0))}]
    platform = rd.get("platform") or ""
    if platform:
        metrics.append({"label": "Aircraft", "value": PLATFORM_NAMES.get(platform, platform)})
    if veg.get("veg_pct") is not None:
        metrics.append({"label": "Vegetation cover", "value": f"{veg['veg_pct']:.1f}%"})
        metrics.append({"label": "Flagged areas", "value": str(veg.get("flagged_polygons", 0))})
    section_data["executive_summary"] = {
        "summary": str(ai.get("executive_summary") or
                       f"Aerial {job_type.replace('_', ' ')} mission over {site}, "
                       f"processed by the Sentinel sortie pipeline on {date_iso}."),
        "key_metrics": metrics,
    }

    software = ["Sortie (Sentinel processing pipeline)"]
    if rd.get("engine") == "nodeodm":
        software.append("NodeODM / OpenDroneMap")
    if rd.get("engine") == "mipmap":
        software.append("MipMap (gaussian splat)")
    if veg:
        software.append("QGIS (VARI vegetation index)")
    section_data["methodology"] = {
        "description": ("Photogrammetric processing of aerial imagery with "
                        "automated classification, quality filtering, and "
                        "report-grade output generation."),
        "software": software,
    }

    section_data["equipment"] = {
        "aircraft_name": PLATFORM_NAMES.get(platform, platform or "DJI aircraft"),
        "aircraft_model": platform or None,
    }

    section_data["flight_data"] = {
        "flights": [{
            "flight_number": 1,
            "date": date_iso,
            "photo_count": rd.get("total_photos", 0),
        }],
        "total_photos": rd.get("total_photos", 0),
    }

    findings = _build_findings(job_type, ai)
    if findings:
        summary_bits = []
        if ai.get("conditions"):
            summary_bits.append(str(ai["conditions"]))
        section_data["findings"] = {
            "findings": findings,
            "summary": " ".join(summary_bits),
        }

    if veg:
        section_data["detection_heatmap"] = {
            "description": (f"VARI vegetation index over the site orthomosaic. "
                            f"Vegetation cover {veg.get('veg_pct', 0):.1f}%, "
                            f"{veg.get('flagged_polygons', 0)} area(s) flagged for decline."),
            "legend": "Green = healthy vegetation · Yellow/red = stressed or bare ground",
        }
        for name, path in (veg.get("outputs") or {}).items():
            if str(path).lower().endswith(_IMAGE_EXTS):
                images.append(("detection_heatmap", path, name))

    dsm_cmp = pc.get("dsm_comparison")
    if dsm_cmp:
        section_data["volumetrics"] = {
            "measurements": [
                {"name": "Fill since prior visit", "type": "fill",
                 "value": round(dsm_cmp.get("fill_volume_m3", 0), 1), "unit": "cu m"},
                {"name": "Cut since prior visit", "type": "cut",
                 "value": round(dsm_cmp.get("cut_volume_m3", 0), 1), "unit": "cu m"},
            ],
            "reference_surface": f"Prior survey DSM ({pc.get('previous_date', 'unknown')})",
        }
        section_data["change_detection"] = {
            "description": "DSM difference versus previous site visit.",
            "comparison_date": pc.get("previous_date"),
        }
        if pc.get("change_map_image"):
            images.append(("change_detection", pc["change_map_image"], "Elevation change map"))

    section_data["annotated_imagery"] = {
        "description": "Representative imagery selected by the sortie pipeline.",
    }
    if imgs.get("ortho_preview"):
        images.append(("annotated_imagery", imgs["ortho_preview"], "Site orthomosaic"))
    if imgs.get("dsm_preview"):
        images.append(("annotated_imagery", imgs["dsm_preview"], "Digital surface model"))
    for thumb, caption in imgs.get("photo_thumbs") or []:
        images.append(("annotated_imagery", thumb, caption))

    downloads = rd.get("downloads") or {}
    if downloads:
        section_data["deliverables_manifest"] = {
            "deliverables": [_file_entry(n, p) for n, p in downloads.items()],
            "delivery_method": "Google Drive share link",
        }

    active_sections = [k for k in (
        "cover_page", "executive_summary", "methodology", "equipment",
        "flight_data", "detection_heatmap", "findings", "volumetrics",
        "change_detection", "annotated_imagery", "deliverables_manifest",
    ) if k in section_data]

    return section_data, active_sections, images[:MAX_REPORT_IMAGES]


def _upload_report_image(report_id, local_path, timeout=30):
    """Upload one image to the public media bucket; return its public URL."""
    url, key = _credentials()
    name = os.path.basename(str(local_path))
    object_path = f"report-images/{report_id}/{name}"
    content_type = "image/png" if name.lower().endswith(".png") else "image/jpeg"
    with open(local_path, "rb") as f:
        data = f.read()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    resp = requests.post(f"{url}/storage/v1/object/media/{object_path}",
                         headers=headers, data=data, timeout=timeout)
    resp.raise_for_status()
    return f"{url}/storage/v1/object/public/media/{object_path}"


def push_report(mission, result, timeout=REQUEST_TIMEOUT):
    """Create a prefilled job_reports draft in the CRM after a linked run.

    Returns the new report id, or None (never raises — report push failing
    must not mark the processing run as failed).
    """
    url, key = _credentials()
    if not (url and key and mission and result.get("report_data")):
        return None

    job_type = (result.get("report_data") or {}).get("job_type", "")
    code = REPORT_TEMPLATE_CODES.get(job_type)
    if not code:
        log.info("No CRM report template for job type %s — skipping push", job_type)
        return None

    try:
        template = _get_template(code, timeout=timeout)
        if not template:
            return None

        section_data, active_sections, images = build_report_payload(
            mission, result, template["name"])

        resp = requests.post(
            f"{url}/rest/v1/job_reports",
            headers={**_headers(key), "Prefer": "return=representation"},
            json={
                "job_id": mission.id,
                "template_id": template["id"],
                "title": f"{template['name']} — {mission.job_number}",
                "status": "draft",
                "section_data": section_data,
                "active_sections": active_sections,
                "report_date": section_data["cover_page"]["report_date"],
            },
            timeout=timeout)
        resp.raise_for_status()
        report_id = resp.json()[0]["id"]

        image_rows = []
        for sort_order, (section_key, local_path, caption) in enumerate(images):
            if not os.path.exists(str(local_path)):
                continue
            try:
                public_url = _upload_report_image(report_id, local_path)
                image_rows.append({
                    "report_id": report_id,
                    "section_key": section_key,
                    "image_url": public_url,
                    "caption": caption,
                    "sort_order": sort_order,
                })
            except Exception as e:
                log.warning("Report image upload failed (%s): %s", local_path, e)
        if image_rows:
            resp = requests.post(f"{url}/rest/v1/report_images",
                                 headers=_headers(key), json=image_rows,
                                 timeout=timeout)
            resp.raise_for_status()

        log.info("CRM report draft created: %s (%d images)", report_id, len(image_rows))
        return report_id
    except Exception as e:
        log.warning("CRM report push failed: %s", e)
        return None


def record_delivery(job_id, share_link):
    """Store the Drive package link — the CRM's delivery email and customer
    portal read delivery_drive_url directly. delivery_status is left alone so
    a re-upload never regresses 'sent'/'delivery_confirmed'."""
    return update_mission(job_id, {"delivery_drive_url": share_link})
