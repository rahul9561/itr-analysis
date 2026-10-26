"""Network isolation, plus optional structural checks against real (authorised) local samples.

The sample test is skipped unless ITR_SAMPLE_DIR points at a folder of real
documents. It asserts only structure and internal consistency - no real names,
PANs or amounts are stored in this repository.
"""

import os
import socket
from pathlib import Path

import pytest

from itr_analysis_engine import analyze_batch, analyze_bytes
from tests import conftest
from tests.fixtures import synthetic as syn


def test_network_guard_is_active():
    with pytest.raises(conftest.OutboundNetworkAttempt):
        socket.create_connection(("example.com", 443))


def test_full_pipeline_makes_no_network_calls():
    before = len(conftest.NETWORK_ATTEMPTS)
    batch = analyze_batch([(syn.text_pdf(syn.business_coi()), "a.pdf"), (syn.itr1_json_bytes(), "b.json")])
    from itr_analysis_engine.report import render_pdf_bytes

    for r in batch.results:
        render_pdf_bytes(r.report)
    assert len(conftest.NETWORK_ATTEMPTS) == before


SAMPLE_DIR = os.environ.get("ITR_SAMPLE_DIR")


@pytest.mark.skipif(not SAMPLE_DIR, reason="set ITR_SAMPLE_DIR to run against real local samples")
def test_real_samples_structure():
    files = sorted(p for p in Path(SAMPLE_DIR).iterdir() if p.suffix.lower() in (".pdf", ".json"))
    assert files
    for path in files:
        report = analyze_bytes(path.read_bytes(), path.name).report
        status = report["document"]["extraction_status"]
        assert status in {"success", "partial", "ocr_required", "unsupported", "failed"}
        if status == "success":
            assert report["assessment_year"]
            assert not [c for c in report["validation"]["checks"] if c["status"] == "fail"], path.name
        pan = report["taxpayer"]["pan_masked"]
        assert pan is None or pan.startswith("XXXXX")
