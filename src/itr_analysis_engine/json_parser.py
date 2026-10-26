"""Loading and dispatching official ITR JSON documents."""

from __future__ import annotations

import json
from typing import Any

from .exceptions import InvalidDocumentError
from .models import ExtractedReturn
from .schema_registry import detect_schema


def load_json_bytes(data: bytes) -> Any:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidDocumentError("JSON file is not valid UTF-8.") from exc
    try:
        return json.loads(text, parse_float=str, parse_int=int)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise InvalidDocumentError("File is not valid JSON.") from exc


def looks_like_json(data: bytes) -> bool:
    head = data[:64].lstrip(b"\xef\xbb\xbf \t\r\n")
    return head.startswith(b"{")


def parse_itr_json(data: Any, source_document: str, allow_unverified: bool = True) -> ExtractedReturn:
    detected = detect_schema(data, allow_unverified=allow_unverified)
    adapter = detected.entry.adapter()
    ret = adapter.parse(data, source_document)
    ret.schema_verified = detected.entry.verified
    return ret
