"""Django REST API: uploads, validation, permissions, storage and errors."""

import base64
import json

import pytest

pytest.importorskip("rest_framework")

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from itr_analysis_engine.django_app.models import ITRAnalysisReport  # noqa: E402
from tests.fixtures import synthetic as syn  # noqa: E402

BASE = "/api/itr/"


@pytest.fixture(scope="session", autouse=True)
def database():
    call_command("migrate", verbosity=0, interactive=False)


@pytest.fixture(autouse=True)
def clean():
    cache.clear()
    ITRAnalysisReport.objects.all().delete()
    get_user_model().objects.all().delete()
    yield


@pytest.fixture
def user():
    return get_user_model().objects.create_user("analyst", password="pw-not-used")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def pdf_upload(name="return.pdf", data=None):
    return SimpleUploadedFile(name, data or syn.text_pdf(syn.business_coi()), content_type="application/pdf")


def test_health_is_public():
    r = APIClient().get(BASE + "health/")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["storage_enabled"] is False
    assert body["supported"]["json"][0]["verified"] is False


def test_analyze_requires_authentication():
    r = APIClient().post(BASE + "analyze/", {"file": pdf_upload()}, format="multipart")
    assert r.status_code == 403


def test_analyze_pdf_upload(client):
    r = client.post(BASE + "analyze/", {"file": pdf_upload()}, format="multipart")
    assert r.status_code == 200
    body = r.json()
    assert body["assessment_year"] == "2025-26"
    assert body["income"]["taxable_income_reported"] == "217000.00"
    assert body["storage"] == {"stored": False, "retrievable": False,
                               "reason": "Report storage is disabled (ITR_ENGINE['STORE_REPORTS'] is False)."}
    assert syn.PAN_A not in r.content.decode()
    assert ITRAnalysisReport.objects.count() == 0


def test_analyze_json_body(client):
    r = client.post(BASE + "analyze/", {"itr_json": syn.itr1_json(), "assessment_year": "2025-26"}, format="json")
    assert r.status_code == 200
    assert r.json()["document"]["itr_form"] == "ITR-1"


def test_analyze_json_file_upload(client):
    f = SimpleUploadedFile("return.json", syn.itr1_json_bytes(), content_type="application/json")
    r = client.post(BASE + "analyze/", {"file": f}, format="multipart")
    assert r.status_code == 200


def test_analyze_base64_body(client):
    payload = {"document_base64": base64.b64encode(syn.text_pdf(syn.salary_coi())).decode(), "filename": "x.pdf"}
    r = client.post(BASE + "analyze/", payload, format="json")
    assert r.status_code == 200
    assert r.json()["income"]["salary"] == "550000.00"


def test_analyze_returns_pdf(client):
    r = client.post(BASE + "analyze/?output=pdf", {"file": pdf_upload()}, format="multipart")
    assert r.status_code == 200
    assert r["Content-Type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


def test_unanalyzable_document_is_422(client):
    r = client.post(BASE + "analyze/", {"file": pdf_upload("scan.pdf", syn.scanned_pdf())}, format="multipart")
    assert r.status_code == 422
    assert r.json()["document"]["extraction_status"] == "ocr_required"


@pytest.mark.parametrize(
    "upload,status,code",
    [
        (lambda: SimpleUploadedFile("x.exe", b"MZ..."), 415, "unsupported_file_type"),
        (lambda: SimpleUploadedFile("x.pdf", b"hello"), 415, "content_mismatch"),
        (lambda: SimpleUploadedFile("x.json", b"[1,2]"), 415, "content_mismatch"),
        (lambda: SimpleUploadedFile("x.pdf", b""), 400, "empty_file"),
    ],
)
def test_upload_validation(client, upload, status, code):
    r = client.post(BASE + "analyze/", {"file": upload()}, format="multipart")
    assert r.status_code == status
    assert r.json()["error"]["code"] == code


def test_missing_document(client):
    r = client.post(BASE + "analyze/", {}, format="json")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "missing_document"


def test_bad_base64_and_bad_ay(client):
    r = client.post(BASE + "analyze/", {"document_base64": "!!!", "filename": "x.pdf"}, format="json")
    assert r.status_code == 400
    r = client.post(BASE + "analyze/", {"itr_json": syn.itr1_json(), "assessment_year": "soon"}, format="json")
    assert r.json()["error"]["code"] == "invalid_assessment_year"


def test_malformed_json_body(client):
    r = client.post(BASE + "analyze/", data="{bad", content_type="application/json")
    assert r.status_code == 400


@override_settings(ITR_ENGINE={"THROTTLE_RATE": None, "MAX_UPLOAD_MB": 0.01})
def test_upload_too_large(client):
    r = client.post(BASE + "analyze/", {"file": pdf_upload(data=b"%PDF-" + b"0" * 20000)}, format="multipart")
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "too_large"


def test_batch(client):
    files = [
        pdf_upload("a.pdf", syn.text_pdf(syn.salary_coi(ay="2024-2025", year_end="31/03/2024", salary_a=100000))),
        pdf_upload("b.pdf", syn.text_pdf(syn.salary_coi())),
    ]
    r = client.post(BASE + "analyze/batch/", {"files": files}, format="multipart")
    assert r.status_code == 200
    body = r.json()
    assert body["document_count"] == 2 and len(body["results"]) == 2
    assert body["year_over_year"][0]["assessment_years"] == ["2024-25", "2025-26"]


@override_settings(ITR_ENGINE={"THROTTLE_RATE": None, "MAX_BATCH_FILES": 1})
def test_batch_limit(client):
    r = client.post(BASE + "analyze/batch/", {"files": [pdf_upload("a.pdf"), pdf_upload("b.pdf")]}, format="multipart")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "too_many_files"


def test_batch_json_documents(client):
    r = client.post(BASE + "analyze/batch/", {"documents": [{"itr_json": syn.itr1_json()}]}, format="json")
    assert r.status_code == 200 and r.json()["document_count"] == 1


def test_reports_unavailable_when_storage_disabled(client):
    r = client.get(BASE + "reports/00000000-0000-0000-0000-000000000000/")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "storage_disabled"


STORE = {"THROTTLE_RATE": None, "STORE_REPORTS": True}


@override_settings(ITR_ENGINE=STORE)
def test_storage_and_retrieval(client):
    r = client.post(BASE + "analyze/", {"file": pdf_upload()}, format="multipart")
    body = r.json()
    assert body["storage"]["stored"] is True
    rid = body["report_id"]
    stored = ITRAnalysisReport.objects.get(id=rid)
    assert stored.assessment_year == "2025-26"
    assert syn.PAN_A not in json.dumps(stored.result)

    detail = client.get(body["storage"]["url"])
    assert detail.status_code == 200 and detail.json()["report_id"] == rid
    pdf = client.get(body["storage"]["pdf_url"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert f"itr-report-{rid}.pdf" in pdf["Content-Disposition"]


@override_settings(ITR_ENGINE=STORE)
def test_other_users_cannot_read_report(client):
    rid = client.post(BASE + "analyze/", {"file": pdf_upload()}, format="multipart").json()["report_id"]
    other = APIClient()
    other.force_authenticate(get_user_model().objects.create_user("other"))
    assert other.get(f"{BASE}reports/{rid}/").status_code == 404
    assert other.get(f"{BASE}reports/{rid}/pdf/").status_code == 404
    staff = APIClient()
    staff.force_authenticate(get_user_model().objects.create_user("boss", is_staff=True))
    assert staff.get(f"{BASE}reports/{rid}/").status_code == 200
    assert APIClient().get(f"{BASE}reports/{rid}/").status_code == 403


@override_settings(ITR_ENGINE=STORE)
def test_unknown_report_404(client):
    r = client.get(BASE + "reports/00000000-0000-0000-0000-000000000000/")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


@override_settings(ITR_ENGINE={"THROTTLE_RATE": "2/min"})
def test_rate_limiting(client):
    for _ in range(2):
        assert client.post(BASE + "analyze/", {"itr_json": syn.itr1_json()}, format="json").status_code == 200
    assert client.post(BASE + "analyze/", {"itr_json": syn.itr1_json()}, format="json").status_code == 429


@override_settings(ITR_ENGINE={"THROTTLE_RATE": None, "PERMISSION_CLASSES": ["rest_framework.permissions.IsAdminUser"]})
def test_configurable_permissions(client):
    r = client.post(BASE + "analyze/", {"itr_json": syn.itr1_json()}, format="json")
    assert r.status_code == 403


@override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=0)  # would force Django's default handlers to spool to disk
def test_source_document_not_written_to_disk(client, monkeypatch):
    from django.core.files.uploadhandler import TemporaryFileUploadHandler

    spooled = []
    monkeypatch.setattr(TemporaryFileUploadHandler, "new_file", lambda self, *a, **k: spooled.append(a))
    r = client.post(BASE + "analyze/", {"file": pdf_upload()}, format="multipart")
    assert r.status_code == 200
    assert spooled == []


def test_logs_do_not_contain_pan(client, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        client.post(BASE + "analyze/", {"file": pdf_upload()}, format="multipart")
    assert syn.PAN_A not in caplog.text
    assert "TEST PERSON ALPHA" not in caplog.text
