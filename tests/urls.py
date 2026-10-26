from django.urls import include, path

urlpatterns = [path("api/itr/", include("itr_analysis_engine.django_app.urls"))]
