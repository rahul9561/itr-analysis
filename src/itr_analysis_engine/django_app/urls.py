from django.urls import path

from . import views

app_name = "itr_engine"

urlpatterns = [
    path("health/", views.HealthView.as_view(), name="health"),
    path("analyze/", views.AnalyzeView.as_view(), name="analyze"),
    path("analyze/batch/", views.BatchAnalyzeView.as_view(), name="analyze-batch"),
    path("reports/<uuid:report_id>/", views.ReportDetailView.as_view(), name="report-detail"),
    path("reports/<uuid:report_id>/pdf/", views.ReportPDFView.as_view(), name="report-pdf"),
]
