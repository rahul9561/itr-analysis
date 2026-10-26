import uuid

from django.conf import settings
from django.db import models


class ITRAnalysisReport(models.Model):
    """A stored analysis result.

    Only the analysis JSON is stored - the uploaded source document is never
    persisted. The JSON contains the masked PAN only.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="itr_reports"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    schema_version = models.CharField(max_length=16)
    assessment_year = models.CharField(max_length=7, blank=True, default="")
    source_type = models.CharField(max_length=16, blank=True, default="")
    document_sha256 = models.CharField(max_length=64, blank=True, default="")
    extraction_status = models.CharField(max_length=32)
    validation_status = models.CharField(max_length=32)
    result = models.JSONField()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["owner", "-created_at"], name="itr_report_owner_created_idx")]

    def __str__(self) -> str:
        return f"ITR analysis {self.id} ({self.assessment_year or 'AY n/a'})"
