from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/itr/", include("itr_analysis_engine.django_app.urls")),
]
