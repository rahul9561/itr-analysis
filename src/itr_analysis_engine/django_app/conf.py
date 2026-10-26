"""App settings, read from ``settings.ITR_ENGINE`` at request time."""

from __future__ import annotations

from typing import Any

from django.conf import settings

DEFAULTS: dict[str, Any] = {
    "MAX_UPLOAD_MB": 10,
    "MAX_BATCH_FILES": 10,
    # Persist analysis JSON (never the source document) so reports can be fetched later.
    "STORE_REPORTS": False,
    "PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    # None -> use REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"].
    "AUTHENTICATION_CLASSES": None,
    # DRF rate string such as "30/min"; None disables throttling.
    "THROTTLE_RATE": "30/min",
    "ALLOW_UNVERIFIED_SCHEMAS": True,
    "RULES": {},
    "OCR_BACKEND": "none",
}


def engine_settings() -> dict[str, Any]:
    return {**DEFAULTS, **getattr(settings, "ITR_ENGINE", {})}


def max_upload_bytes() -> int:
    return int(float(engine_settings()["MAX_UPLOAD_MB"]) * 1024 * 1024)
