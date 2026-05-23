# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("custom_operations", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomOperationRun",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_date", models.DateTimeField(auto_now_add=True)),
                ("updated_date", models.DateTimeField(auto_now=True)),
                ("run_key", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("running", "RUNNING"),
                            ("succeeded", "SUCCEEDED"),
                            ("failed", "FAILED"),
                        ],
                        default="running",
                        max_length=32,
                    ),
                ),
                ("output_path", models.CharField(blank=True, default="", max_length=2048)),
                ("request_payload", models.JSONField(blank=True, default=dict)),
                ("result", models.JSONField(blank=True, default=dict)),
                ("output_collection", models.JSONField(blank=True, default=dict)),
                ("error", models.TextField(blank=True, default="")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="custom_operation_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "operation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="runs",
                        to="custom_operations.customoperationdefinition",
                    ),
                ),
            ],
            options={
                "ordering": ("-created_date", "-id"),
                "default_permissions": (),
            },
        ),
    ]
