# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import uuid
from enum import Enum
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models

from cvat.apps.engine.models import TimestampedModel


class OperationKind(str, Enum):
    MODEL = "model"
    AUGMENTATION = "augmentation"

    @classmethod
    def choices(cls):
        return tuple((x.value, x.name) for x in cls)

    def __str__(self):
        return self.value


class OperationFieldType(str, Enum):
    STRING = "string"
    TEXT = "text"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    SELECT = "select"
    JSON = "json"
    FILE = "file"
    FILE_COLLECTION = "file_collection"

    @classmethod
    def choices(cls):
        return tuple((x.value, x.name) for x in cls)

    def __str__(self):
        return self.value


def operation_artifact_upload_to(instance: "CustomOperationDefinition", filename: str) -> Path:
    return Path("custom-operations") / str(instance.artifact_key) / Path(filename).name


operation_artifact_storage = FileSystemStorage(location=settings.MEDIA_DATA_ROOT)


class CustomOperationDefinition(TimestampedModel):
    artifact_key = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    name = models.CharField(max_length=128)
    kind = models.CharField(max_length=32, choices=OperationKind.choices())
    description = models.TextField(blank=True, default="")
    nuclio_function = models.CharField(max_length=256)
    input_schema = models.JSONField(default=list)
    output_schema = models.JSONField(default=dict, blank=True)
    artifact = models.FileField(
        blank=True,
        null=True,
        max_length=1024,
        upload_to=operation_artifact_upload_to,
        storage=operation_artifact_storage,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        default_permissions = ()
        ordering = ("-updated_date", "-id")

    def __str__(self) -> str:
        return self.name

    def delete(self, using=None, keep_parents=False):
        artifact_name = self.artifact.name if self.artifact else None
        super().delete(using=using, keep_parents=keep_parents)
        if artifact_name:
            self.artifact.storage.delete(artifact_name)


class CustomOperationRunStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @classmethod
    def choices(cls):
        return tuple((x.value, x.name) for x in cls)

    def __str__(self):
        return self.value


class CustomOperationRun(TimestampedModel):
    run_key = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    operation = models.ForeignKey(
        CustomOperationDefinition,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="runs",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="custom_operation_runs",
    )
    status = models.CharField(
        max_length=32,
        choices=CustomOperationRunStatus.choices(),
        default=CustomOperationRunStatus.RUNNING.value,
    )
    output_path = models.CharField(max_length=2048, blank=True, default="")
    request_payload = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    output_collection = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        default_permissions = ()
        ordering = ("-created_date", "-id")

    def __str__(self) -> str:
        operation_name = self.operation.name if self.operation else "deleted operation"
        return f"{operation_name} run {self.run_key}"
