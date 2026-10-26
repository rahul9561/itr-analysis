"""ITR analysis engine: deterministic, local analysis of Indian income-tax return documents."""

__version__ = "0.1.0"

from .analyzer import (  # noqa: E402
    AnalysisOptions,
    AnalysisResult,
    BatchResult,
    analyze_batch,
    analyze_bytes,
    analyze_file,
    analyze_paths,
)
from .models import SCHEMA_VERSION  # noqa: E402
from .risk import RuleConfig  # noqa: E402

__all__ = [
    "__version__",
    "SCHEMA_VERSION",
    "AnalysisOptions",
    "AnalysisResult",
    "BatchResult",
    "RuleConfig",
    "analyze_batch",
    "analyze_bytes",
    "analyze_file",
    "analyze_paths",
]
