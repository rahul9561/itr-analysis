"""REST endpoints.

Documents are processed in memory and discarded after the request. Only the
analysis JSON is stored, and only when ``ITR_ENGINE["STORE_REPORTS"]`` is on.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any

from django.http import HttpResponse
from django.urls import reverse
from django.utils.module_loading import import_string
from rest_framework import status
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import SCHEMA_VERSION, __version__
from ..analyzer import AnalysisOptions, AnalysisResult, analyze_batch, analyze_bytes
from ..exceptions import MalformedValueError
from ..normalizer import normalize_assessment_year
from ..ocr import get_ocr_backend
from ..pdf_extractor import is_pdf_bytes
from ..json_parser import looks_like_json
from ..report import render_pdf_bytes
from ..risk import RuleConfig
from ..schema_registry import describe_supported
from ..security import PIIRedactingFilter, safe_filename
from ..tax_rules import supported_assessment_years
from .conf import engine_settings, max_upload_bytes
from .models import ITRAnalysisReport
from .throttling import ITRAnalysisThrottle
from .uploads import BoundedMemoryUploadHandler, UploadTooLarge

log = logging.getLogger(__name__)
log.addFilter(PIIRedactingFilter())

ALLOWED_EXTENSIONS = {".pdf": "pdf", ".json": "json"}


class APIError(Exception):
    def __init__(self, http_status: int, code: str, message: str):
        self.http_status, self.code, self.message = http_status, code, message


def _error(http_status: int, code: str, message: str) -> Response:
    return Response({"error": {"code": code, "message": message}}, status=http_status)


class _EngineView(APIView):
    """Common configuration: permissions and authentication come from ITR_ENGINE settings."""

    def get_permissions(self):
        return [import_string(path)() for path in engine_settings()["PERMISSION_CLASSES"]]

    def get_authenticators(self):
        classes = engine_settings()["AUTHENTICATION_CLASSES"]
        if classes is None:
            return super().get_authenticators()
        return [import_string(path)() for path in classes]

    def handle_exception(self, exc):
        if isinstance(exc, APIError):
            return _error(exc.http_status, exc.code, exc.message)
        return super().handle_exception(exc)

    @staticmethod
    def analysis_options(assessment_year: str | None) -> AnalysisOptions:
        cfg = engine_settings()
        return AnalysisOptions(
            assessment_year=assessment_year,
            ocr_backend=get_ocr_backend(cfg["OCR_BACKEND"]),
            rules=RuleConfig(cfg["RULES"]),
            max_bytes=max_upload_bytes(),
            allow_unverified_schemas=cfg["ALLOW_UNVERIFIED_SCHEMAS"],
        )


class _AnalysisView(_EngineView):
    parser_classes = [MultiPartParser, JSONParser]

    def get_throttles(self):
        return [ITRAnalysisThrottle()] if engine_settings()["THROTTLE_RATE"] else []

    # -- request handling -----------------------------------------------------
    def prepare_upload(self, request: Request, max_files: int = 1) -> None:
        limit = max_upload_bytes()
        try:
            content_length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            content_length = 0
        # Allow for multipart overhead / base64 expansion in the envelope.
        if content_length > limit * max_files * 4 // 3 + 64 * 1024:
            raise APIError(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too_large",
                           f"Request exceeds the upload limit of {limit} bytes per document.")
        request._request.upload_handlers = [BoundedMemoryUploadHandler(request._request, max_bytes=limit)]

    def read_data(self, request: Request) -> Any:
        try:
            return request.data
        except UploadTooLarge:
            raise APIError(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too_large",
                           f"Document exceeds the upload limit of {max_upload_bytes()} bytes.")
        except ParseError as exc:
            raise APIError(status.HTTP_400_BAD_REQUEST, "malformed_request", str(exc.detail))

    @staticmethod
    def assessment_year(data: Any) -> str | None:
        raw = data.get("assessment_year") if hasattr(data, "get") else None
        if not raw:
            return None
        try:
            return normalize_assessment_year(raw)
        except MalformedValueError:
            raise APIError(status.HTTP_400_BAD_REQUEST, "invalid_assessment_year",
                           "assessment_year must look like 2025-26.")

    @staticmethod
    def validate_document(content: bytes, filename: str) -> tuple[bytes, str]:
        name = safe_filename(filename)
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED_EXTENSIONS:
            raise APIError(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "unsupported_file_type",
                           "Only .pdf and .json files are accepted.")
        if not content:
            raise APIError(status.HTTP_400_BAD_REQUEST, "empty_file", "The uploaded file is empty.")
        if len(content) > max_upload_bytes():
            raise APIError(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too_large",
                           f"Document exceeds the upload limit of {max_upload_bytes()} bytes.")
        kind = ALLOWED_EXTENSIONS[ext]
        if kind == "pdf" and not is_pdf_bytes(content):
            raise APIError(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "content_mismatch", "File content is not a PDF.")
        if kind == "json" and not looks_like_json(content):
            raise APIError(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "content_mismatch", "File content is not a JSON object.")
        return content, name

    def documents_from(self, data: Any, files: Any, field: str, max_files: int) -> list[tuple[bytes, str]]:
        docs: list[tuple[bytes, str]] = []
        uploaded = files.getlist(field) if hasattr(files, "getlist") else []
        for f in uploaded:
            docs.append(self.validate_document(f.read(), f.name or "document"))
        if not uploaded and isinstance(data, dict):
            # JSON request body: {"itr_json": {...}} or {"document_base64": "...", "filename": "x.pdf"}
            entries = data.get("documents") if field == "files" else [data]
            for entry in entries or []:
                if not isinstance(entry, dict):
                    raise APIError(status.HTTP_400_BAD_REQUEST, "malformed_request", "Each document must be an object.")
                if "itr_json" in entry:
                    if not isinstance(entry["itr_json"], dict):
                        raise APIError(status.HTTP_400_BAD_REQUEST, "malformed_request", "itr_json must be an object.")
                    content = json.dumps(entry["itr_json"]).encode("utf-8")
                    docs.append(self.validate_document(content, entry.get("filename") or "return.json"))
                elif "document_base64" in entry:
                    try:
                        content = base64.b64decode(entry["document_base64"], validate=True)
                    except (binascii.Error, TypeError, ValueError):
                        raise APIError(status.HTTP_400_BAD_REQUEST, "malformed_request", "document_base64 is not valid base64.")
                    docs.append(self.validate_document(content, entry.get("filename") or ""))
        if not docs:
            raise APIError(status.HTTP_400_BAD_REQUEST, "missing_document",
                           f"Upload a file in the '{field}' field, or send JSON with 'itr_json' or 'document_base64'.")
        if len(docs) > max_files:
            raise APIError(status.HTTP_400_BAD_REQUEST, "too_many_files", f"At most {max_files} documents per request.")
        return docs

    # -- storage --------------------------------------------------------------
    def store(self, request: Request, result: AnalysisResult) -> dict[str, Any]:
        if not engine_settings()["STORE_REPORTS"]:
            return {"stored": False, "retrievable": False,
                    "reason": "Report storage is disabled (ITR_ENGINE['STORE_REPORTS'] is False)."}
        r = result.report
        ITRAnalysisReport.objects.create(
            id=r["report_id"],
            owner=request.user if request.user and request.user.is_authenticated else None,
            schema_version=r["schema_version"],
            assessment_year=r["assessment_year"] or "",
            source_type=r["document"]["source_type"] or "",
            document_sha256=r["document"]["sha256"] or "",
            extraction_status=r["document"]["extraction_status"],
            validation_status=r["validation"]["status"],
            result=r,
        )
        return {
            "stored": True,
            "retrievable": True,
            "url": reverse("itr_engine:report-detail", args=[r["report_id"]]),
            "pdf_url": reverse("itr_engine:report-pdf", args=[r["report_id"]]),
        }


class HealthView(_EngineView):
    def get_permissions(self):
        return [AllowAny()]

    def get(self, request: Request) -> Response:
        cfg = engine_settings()
        return Response({
            "status": "ok",
            "engine_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "storage_enabled": bool(cfg["STORE_REPORTS"]),
            "max_upload_bytes": max_upload_bytes(),
            "ocr_available": get_ocr_backend(cfg["OCR_BACKEND"]).is_available(),
            "supported": {
                "pdf": ["Computation of income (text-based PDF)"],
                "json": describe_supported(),
                "rule_set_assessment_years": supported_assessment_years(),
            },
        })


class AnalyzeView(_AnalysisView):
    """POST a single document. ``?output=pdf`` returns the PDF report directly."""

    def post(self, request: Request) -> Response | HttpResponse:
        self.prepare_upload(request)
        data = self.read_data(request)
        ay = self.assessment_year(data)
        (content, name), = self.documents_from(data, request.FILES, "file", max_files=1)
        result = analyze_bytes(content, name, self.analysis_options(ay))
        storage = self.store(request, result)
        if request.query_params.get("output") == "pdf":
            return _pdf_response(result.report)
        body = {**result.report, "storage": storage}
        http_status = status.HTTP_200_OK if result.analyzable else status.HTTP_422_UNPROCESSABLE_ENTITY
        return Response(body, status=http_status)


class BatchAnalyzeView(_AnalysisView):
    """POST several documents (multipart field ``files``, or JSON ``{"documents": [...]}``)."""

    def post(self, request: Request) -> Response:
        max_files = int(engine_settings()["MAX_BATCH_FILES"])
        self.prepare_upload(request, max_files=max_files)
        data = self.read_data(request)
        ay = self.assessment_year(data)
        docs = self.documents_from(data, request.FILES, "files", max_files=max_files)
        batch = analyze_batch(docs, self.analysis_options(ay))
        reports = []
        for result in batch.results:
            reports.append({**result.report, "storage": self.store(request, result)})
        return Response({**batch.summary(), "results": reports}, status=status.HTTP_200_OK)


class _StoredReportView(_EngineView):
    def get_report(self, request: Request, report_id) -> ITRAnalysisReport:
        if not engine_settings()["STORE_REPORTS"]:
            raise APIError(status.HTTP_404_NOT_FOUND, "storage_disabled",
                           "Report storage is disabled, so reports cannot be retrieved.")
        qs = ITRAnalysisReport.objects.all()
        if not request.user.is_staff:
            qs = qs.filter(owner=request.user if request.user.is_authenticated else None)
        report = qs.filter(id=report_id).first()
        if report is None:
            raise APIError(status.HTTP_404_NOT_FOUND, "not_found", "Report not found.")
        return report


class ReportDetailView(_StoredReportView):
    def get(self, request: Request, report_id) -> Response:
        return Response(self.get_report(request, report_id).result)


class ReportPDFView(_StoredReportView):
    def get(self, request: Request, report_id) -> HttpResponse:
        return _pdf_response(self.get_report(request, report_id).result)


def _pdf_response(report: dict[str, Any]) -> HttpResponse:
    response = HttpResponse(render_pdf_bytes(report), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="itr-report-{report["report_id"]}.pdf"'
    response["Cache-Control"] = "no-store"
    return response
