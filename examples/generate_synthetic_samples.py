"""Write synthetic sample documents (fictitious data) to examples/synthetic/.

    python examples/generate_synthetic_samples.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from tests.fixtures import synthetic as syn  # noqa: E402

out = ROOT / "examples" / "synthetic"
out.mkdir(parents=True, exist_ok=True)
(out / "synthetic_coi_business_ay2025-26.pdf").write_bytes(syn.text_pdf(syn.business_coi()))
(out / "synthetic_coi_salary_ay2024-25.pdf").write_bytes(
    syn.text_pdf(syn.salary_coi(ay="2024-2025", year_end="31/03/2024", salary_a=200000, salary_b=100000)))
(out / "synthetic_coi_salary_ay2025-26.pdf").write_bytes(syn.text_pdf(syn.salary_coi()))
(out / "synthetic_scanned.pdf").write_bytes(syn.scanned_pdf())
(out / "synthetic_itr1_ay2025-26.json").write_text(json.dumps(syn.itr1_json(), indent=2), encoding="utf-8")
print(f"wrote samples to {out}")
