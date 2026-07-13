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
        log.warning("CRM update failed for %s: %s", job_id, e)
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
        "photogrammetry_status": "complete",
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


def record_delivery(job_id, share_link):
    """Store the Drive package link — the CRM's delivery email and customer
    portal read delivery_drive_url directly. delivery_status is left alone so
    a re-upload never regresses 'sent'/'delivery_confirmed'."""
    return update_mission(job_id, {"delivery_drive_url": share_link})
