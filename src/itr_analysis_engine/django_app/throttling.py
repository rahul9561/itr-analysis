from rest_framework.throttling import UserRateThrottle

from .conf import engine_settings


class ITRAnalysisThrottle(UserRateThrottle):
    """Per-user (or per-IP for anonymous callers) rate limit for analysis endpoints."""

    scope = "itr_analysis"

    def get_rate(self):
        return engine_settings()["THROTTLE_RATE"]
