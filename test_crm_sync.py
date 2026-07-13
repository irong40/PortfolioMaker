"""Tests for crm_sync — CRM mission fetch, field allowlist, stage helpers."""

import pytest

import crm_sync
from crm_sync import CrmMission, _parse_mission


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-key")


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    # Keep load_dotenv from re-injecting real creds from a local .env
    monkeypatch.setattr(crm_sync, "_load_env", lambda: None)


SAMPLE_ROW = {
    "id": "d940f61d-e8fa-40a6-a4b9-a59cb4f0341d",
    "job_number": "SAI-SPEC-012",
    "property_address": "2237 Shillelagh Rd, Chesapeake, VA 23323",
    "site_address": "2237 Shillelagh Rd, Chesapeake, VA 23323",
    "property_city": "Chesapeake",
    "property_state": "VA",
    "property_type": "land",
    "status": "scheduled",
    "scheduled_date": "2026-07-15",
    "scheduled_time": "10:30:00",
    "pilot_notes": None,
    "admin_notes": "portfolio test",
    "clients": {"name": "Jane Doe", "company": "Acme Land"},
    "processing_templates": {"preset_name": "mapping", "path_code": "C",
                             "display_name": "Mapping/WebODM"},
}


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload = payload or []
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


# ── configuration ──────────────────────────────────────────────────────────

def test_is_configured_false_without_env(unconfigured):
    assert not crm_sync.is_configured()


def test_is_configured_true_with_env(configured):
    assert crm_sync.is_configured()


# ── fetch ───────────────────────────────────────────────────────────────────

def test_fetch_parses_missions(configured, monkeypatch):
    monkeypatch.setattr(crm_sync.requests, "get",
                        lambda *a, **k: FakeResponse([SAMPLE_ROW]))
    missions = crm_sync.fetch_open_missions()
    assert len(missions) == 1
    m = missions[0]
    assert m.job_number == "SAI-SPEC-012"
    assert m.client_name == "Jane Doe"
    assert m.preset_name == "mapping"
    assert m.status == "scheduled"


def test_fetch_returns_empty_when_unconfigured(unconfigured):
    assert crm_sync.fetch_open_missions() == []


def test_fetch_returns_empty_on_network_error(configured, monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(crm_sync.requests, "get", boom)
    assert crm_sync.fetch_open_missions() == []


# ── mission helpers ─────────────────────────────────────────────────────────

def test_label_contains_key_facts():
    m = _parse_mission(SAMPLE_ROW)
    assert "SAI-SPEC-012" in m.label
    assert "2237 Shillelagh Rd" in m.label
    assert "scheduled" in m.label


def test_suggested_job_type_mapping():
    m = _parse_mission(SAMPLE_ROW)
    assert m.suggested_job_type() == "property_survey"


def test_suggested_job_type_none_for_video():
    row = dict(SAMPLE_ROW)
    row["processing_templates"] = {"preset_name": "video", "path_code": "V",
                                   "display_name": "Video"}
    assert _parse_mission(row).suggested_job_type() is None


def test_suggested_site_name_is_street():
    m = _parse_mission(SAMPLE_ROW)
    assert m.suggested_site_name() == "2237 Shillelagh Rd"


def test_parse_handles_missing_joins():
    row = {"id": "x", "job_number": "DJ-2026-0001", "clients": None,
           "processing_templates": None}
    m = _parse_mission(row)
    assert m.client_name == ""
    assert m.suggested_job_type() is None
    assert m.suggested_site_name() == "DJ-2026-0001"


# ── update allowlist ────────────────────────────────────────────────────────

def test_update_strips_non_writable_fields(configured, monkeypatch):
    captured = {}

    def fake_patch(url, headers=None, params=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(crm_sync.requests, "patch", fake_patch)
    ok = crm_sync.update_mission("job-1", {
        "status": "complete",
        "job_number": "HACKED",       # not writable — CRM owns naming
        "admin_notes": "nope",        # not writable
    })
    assert ok
    assert captured["json"] == {"status": "complete"}


def test_update_all_dropped_returns_false(configured, monkeypatch):
    monkeypatch.setattr(crm_sync.requests, "patch",
                        lambda *a, **k: FakeResponse())
    assert not crm_sync.update_mission("job-1", {"job_number": "X"})


def test_update_never_raises_on_error(configured, monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(crm_sync.requests, "patch", boom)
    assert crm_sync.update_mission("job-1", {"status": "failed"}) is False


# ── stage helpers ───────────────────────────────────────────────────────────

def _capture_patch(monkeypatch):
    calls = []

    def fake_patch(url, headers=None, params=None, json=None, timeout=None):
        calls.append({"params": params, "json": json})
        return FakeResponse()

    monkeypatch.setattr(crm_sync.requests, "patch", fake_patch)
    return calls


def test_mark_processing(configured, monkeypatch):
    calls = _capture_patch(monkeypatch)
    assert crm_sync.mark_processing("job-1", photo_count=42)
    body = calls[0]["json"]
    assert body["status"] == "processing"
    assert body["photogrammetry_status"] == "processing"
    assert body["photo_count"] == 42
    assert "processing_started_at" in body


def test_mark_complete_maps_outputs(configured, monkeypatch):
    calls = _capture_patch(monkeypatch)
    result = {
        "output_dir": r"E:\Portfolio\Site\job",
        "task_uuid": "odm-task-9",
        "downloaded": {
            "odm_orthophoto.tif": r"E:\Portfolio\Site\job\odm_orthophoto.tif",
            "odm_georeferenced_model.laz": r"E:\Portfolio\Site\job\model.laz",
            "odm_textured_model.glb": r"E:\Portfolio\Site\job\model.glb",
        },
    }
    assert crm_sync.mark_complete("job-1", result)
    body = calls[0]["json"]
    assert body["status"] == "complete"
    assert body["delivery_status"] == "ready"
    assert body["nodeodm_task_id"] == "odm-task-9"
    assert body["output_path"] == r"E:\Portfolio\Site\job"
    assert body["orthophoto_path"].endswith("odm_orthophoto.tif")
    assert body["pointcloud_path"].endswith("model.laz")
    assert body["model_file_path"].endswith("model.glb")


def test_mark_failed_truncates_error(configured, monkeypatch):
    calls = _capture_patch(monkeypatch)
    assert crm_sync.mark_failed("job-1", "x" * 5000)
    body = calls[0]["json"]
    assert body["status"] == "failed"
    assert len(body["processing_error"]) == 1000


def test_record_delivery_sets_drive_url_only(configured, monkeypatch):
    calls = _capture_patch(monkeypatch)
    assert crm_sync.record_delivery("job-1", "https://drive.google.com/x")
    body = calls[0]["json"]
    assert body == {"delivery_drive_url": "https://drive.google.com/x"}
