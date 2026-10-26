import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ITRAnalysisReport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("schema_version", models.CharField(max_length=16)),
                ("assessment_year", models.CharField(blank=True, default="", max_length=7)),
                ("source_type", models.CharField(blank=True, default="", max_length=16)),
                ("document_sha256", models.CharField(blank=True, default="", max_length=64)),
                ("extraction_status", models.CharField(max_length=32)),
                ("validation_status", models.CharField(max_length=32)),
                ("result", models.JSONField()),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="itr_reports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["owner", "-created_at"], name="itr_report_owner_created_idx")],
            },
        ),
    ]
