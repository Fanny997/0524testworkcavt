# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT
# 与数据库中的字段名进行对应，一个class表示为一个表
from __future__ import annotations

import uuid
from enum import Enum
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models

from cvat.apps.engine.models import TimestampedModel

# 操作类型定义，自定义数据增强和推理两种操作
class OperationKind(str, Enum):
    MODEL = "model"
    AUGMENTATION = "augmentation"

    @classmethod
    def choices(cls):
        # 返回格式：((存储值, 显示名), ...)
        return tuple((x.value, x.name) for x in cls)

    def __str__(self):
        return self.value

#支持的字段类型，对应的操作输入类型
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

#文件上传后存在的位置，格式：custom-operations/{artifact_key}/{filename}
def operation_artifact_upload_to(instance: "CustomOperationDefinition", filename: str) -> Path:
    return Path("custom-operations") / str(instance.artifact_key) / Path(filename).name

# 目录指向 MEDIA_DATA_ROOT 配置
operation_artifact_storage = FileSystemStorage(location=settings.MEDIA_DATA_ROOT)


class CustomOperationDefinition(TimestampedModel):
    """
    自定义操作，记录可被调用的Nuclio函数及其元数据。
    """
    #数据表字段定义
    # URL 的唯一标识
    artifact_key = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    # 操作名称，
    name = models.CharField(max_length=128)
    # 操作类型，值来自上方的OperationKind函数
    kind = models.CharField(max_length=32, choices=OperationKind.choices())
    # 操作描述
    description = models.TextField(blank=True, default="")
    # 关联的Nuclio函数名称，执行时通过该名称调用，也就是文件中的定义函数名
    nuclio_function = models.CharField(max_length=256)
    # 输入输出的字段定义，JSON格式，对应前端的输入输出定义，都在示例文件中
    input_schema = models.JSONField(default=list)
    output_schema = models.JSONField(default=dict, blank=True)

    # 可选的附件文件，存储在独立的文件系统中
    artifact = models.FileField(
        blank=True,
        null=True,
        max_length=1024,
        upload_to=operation_artifact_upload_to,
        storage=operation_artifact_storage,
    )
    # 上方的一条记录代表一个可运行的操作

    # 该操作是否可运行，可自行控制，默认都能调用
    is_active = models.BooleanField(default=True)

    class Meta:
        default_permissions = () # 禁用 Django 默认的 add/change/delete/view 权限
        ordering = ("-updated_date", "-id")# 默认按更新时间倒序排列

    def __str__(self) -> str:
        return self.name

    # 删除某一条记录以及对应的存储文件
    def delete(self, using=None, keep_parents=False):
        artifact_name = self.artifact.name if self.artifact else None
        super().delete(using=using, keep_parents=keep_parents)
        if artifact_name:
            self.artifact.storage.delete(artifact_name)

# 某个操作的运行状态
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
    """
    记录每次自定义操作的执行历史。
    无论成功还是失败，每次调用execute接口都会创建一条记录。
    """
    # 运行的唯一标识id
    run_key = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    # 与对应的操作进行关联，如果对应的操作被删除了，这里也会进行值的修改
    operation = models.ForeignKey(
        CustomOperationDefinition,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="runs",
    )
    # 触发此次运行的用户；用户被删除时置为 NULL
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="custom_operation_runs",
    )
    # 当前运行状态
    status = models.CharField(
        max_length=32,
        choices=CustomOperationRunStatus.choices(),
        default=CustomOperationRunStatus.RUNNING.value,
    )
    # 结果输出文件的存储目录路径
    output_path = models.CharField(max_length=2048, blank=True, default="")
    # 原始请求的数据
    request_payload = models.JSONField(default=dict, blank=True)
    # 执行成功后的结果数据
    result = models.JSONField(default=dict, blank=True)
    # 输出文件集合的数据（文件路径、类型等）
    output_collection = models.JSONField(default=dict, blank=True)
    # 执行失败时的错误信息
    error = models.TextField(blank=True, default="")

    class Meta:
        default_permissions = ()
        ordering = ("-created_date", "-id") # 默认按创建时间倒序

    def __str__(self) -> str:
        operation_name = self.operation.name if self.operation else "deleted operation"
        return f"{operation_name} run {self.run_key}"
