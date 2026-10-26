from django.contrib import admin

from .models import ITRAnalysisReport


@admin.register(ITRAnalysisReport)
class ITRAnalysisReportAdmin(admin.ModelAdmin):
    list_display = ("id", "assessment_year", "source_type", "extraction_status", "validation_status", "owner", "created_at")
    list_filter = ("extraction_status", "validation_status", "assessment_year")
    readonly_fields = [f.name for f in ITRAnalysisReport._meta.fields]
