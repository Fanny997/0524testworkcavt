# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import uuid

import cvat.apps.custom_operations.models
import django.core.files.storage
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="CustomOperationDefinition",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_date", models.DateTimeField(auto_now_add=True)),
                ("updated_date", models.DateTimeField(auto_now=True)),
                (
                    "artifact_key",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("name", models.CharField(max_length=128)),
                (
                    "kind",
                    models.CharField(
                        choices=[("model", "MODEL"), ("augmentation", "AUGMENTATION")],
                        max_length=32,
                    ),
                ),
                ("description", models.TextField(blank=True, default="")),
                ("nuclio_function", models.CharField(max_length=256)),
                ("input_schema", models.JSONField(default=list)),
                ("output_schema", models.JSONField(blank=True, default=dict)),
                (
                    "artifact",
                    models.FileField(
                        blank=True,
                        null=True,
                        max_length=1024,
                        storage=django.core.files.storage.FileSystemStorage(
                            location=settings.MEDIA_DATA_ROOT
                        ),
                        upload_to=cvat.apps.custom_operations.models.operation_artifact_upload_to,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "ordering": ("-updated_date", "-id"),
                "default_permissions": (),
            },
        ),
    ]

