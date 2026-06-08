# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT
# 数据校验文件
from __future__ import annotations

from urllib.parse import urlencode

from django.core.signing import BadSignature, TimestampSigner
from django.urls import reverse
from rest_framework import serializers

from .models import CustomOperationDefinition, OperationFieldType, OperationKind


class CustomOperationOptionSerializer(serializers.Serializer):
    label = serializers.CharField()
    value = serializers.JSONField()
    description = serializers.CharField(required=False, allow_blank=True, default="")


class CustomOperationInputFieldSerializer(serializers.Serializer):
    """
    SELECT 类型字段的单个选项，格式如：
    {"label": "显示文字", "value": <任意JSON值>, "description": "可选说明"}
    """
    name = serializers.CharField()# 字段唯一标识
    label = serializers.CharField(required=False, allow_blank=True, default="")   # 前端展示名
    type = serializers.ChoiceField(choices=OperationFieldType.choices())           # 字段类型
    required = serializers.BooleanField(default=False)                             # 是否必填
    description = serializers.CharField(required=False, allow_blank=True, default="")
    placeholder = serializers.CharField(required=False, allow_blank=True, default="")
    default = serializers.JSONField(required=False)                                # 默认值

    # 仅 SELECT 类型使用
    options = CustomOperationOptionSerializer(many=True, required=False)

    # 仅 FILE / FILE_COLLECTION 类型使用：限制可接受的 MIME 类型或扩展名
    accept = serializers.ListField(child=serializers.CharField(), required=False)

    # 仅 FILE_COLLECTION 类型使用：限制文件数量范围
    min_count = serializers.IntegerField(required=False, min_value=1)
    max_count = serializers.IntegerField(required=False, min_value=1)

    # 仅数值类型（INTEGER / NUMBER）使用：限制取值范围和步长
    minimum = serializers.FloatField(required=False)
    maximum = serializers.FloatField(required=False)
    step = serializers.FloatField(required=False)

    def validate(self, attrs):
        # SELECT 类型必须提供至少一个选项
        if attrs["type"] == OperationFieldType.SELECT.value and not attrs.get("options"):
            raise serializers.ValidationError("Select fields require options")
        # FILE / FILE_COLLECTION 类型不允许定义options
        if attrs["type"] in {
            OperationFieldType.FILE.value,
            OperationFieldType.FILE_COLLECTION.value,
        } and attrs.get("options"):
            raise serializers.ValidationError("File fields cannot define options")
        # FILE_COLLECTION 类型：max_count 不能小于 min_count
        if attrs["type"] == OperationFieldType.FILE_COLLECTION.value:
            min_count = attrs.get("min_count", 1)
            max_count = attrs.get("max_count")
            if max_count is not None and max_count < min_count:
                raise serializers.ValidationError("max_count cannot be smaller than min_count")

        return attrs


class CustomOperationDefinitionSerializer(serializers.ModelSerializer):
    """
    CustomOperationDefinition 的完整序列化。

    artifact：上传时写入,不在响应中返回原始文件对象
    artifact_url：动态生成带签名的下载链接（只读）
    artifact_name：从存储路径中提取的文件名（只读）
    """
    artifact = serializers.FileField(required=False, allow_null=True, write_only=True)
    artifact_url = serializers.SerializerMethodField()
    artifact_name = serializers.SerializerMethodField()

    class Meta:
        model = CustomOperationDefinition
        fields = (
            "id",
            "artifact_key",
            "name",
            "kind",
            "description",
            "nuclio_function",
            "input_schema",
            "output_schema",
            "artifact",
            "artifact_url",
            "artifact_name",
            "is_active",
            "created_date",
            "updated_date",
        )
        read_only_fields = ("artifact_key", "artifact_url", "artifact_name", "created_date", "updated_date")

    def validate_input_schema(self, value):
        """
        校验 input_schema 字段：
        1. 空值统一转为空列表
        2. 必须是列表类型
        3. 每个元素通过 CustomOperationInputFieldSerializer 校验
        4. 字段 name 不允许重复
        """
        if value in (None, ""):
            value = []

        if not isinstance(value, list):
            raise serializers.ValidationError("input_schema must be a list")

        schema_serializer = CustomOperationInputFieldSerializer(data=value, many=True)
        schema_serializer.is_valid(raise_exception=True)
        validated = schema_serializer.validated_data

        names = [item["name"] for item in validated]
        if len(names) != len(set(names)):
            raise serializers.ValidationError("input_schema field names must be unique")

        return validated

    def validate_output_schema(self, value):
        if value in (None, ""):
            return {}
        return value

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        kind = attrs.get("kind", getattr(instance, "kind", None))
        artifact = attrs.get("artifact", getattr(instance, "artifact", None))
        allow_model_without_artifact = self.context.get("allow_model_without_artifact", False)

        if kind == OperationKind.MODEL.value and not artifact and not allow_model_without_artifact:
            raise serializers.ValidationError(
                {"artifact": "Model definitions require an uploaded artifact"}
            )

        return attrs

    def update(self, instance, validated_data):
        old_artifact_name = instance.artifact.name if instance.artifact else None
        instance = super().update(instance, validated_data)

        if old_artifact_name and instance.artifact and instance.artifact.name != old_artifact_name:
            instance.artifact.storage.delete(old_artifact_name)

        return instance

    def get_artifact_url(self, obj):
        if not obj.artifact:
            return None

        token = TimestampSigner(salt="custom-operation-artifact").sign(str(obj.artifact_key))
        request = self.context.get("request")
        path = reverse("custom_operation-artifact", args=[obj.pk])
        url = f"{path}?{urlencode({'signature': token})}"
        return request.build_absolute_uri(url) if request else url

    def get_artifact_name(self, obj):
        if not obj.artifact:
            return None
        return obj.artifact.name.rsplit("/", 1)[-1]
