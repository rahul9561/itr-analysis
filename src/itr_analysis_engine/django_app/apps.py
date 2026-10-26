from django.apps import AppConfig


class ITREngineConfig(AppConfig):
    name = "itr_analysis_engine.django_app"
    label = "itr_engine"
    verbose_name = "ITR analysis engine"
    default_auto_field = "django.db.models.BigAutoField"
