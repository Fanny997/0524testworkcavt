# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

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
    name = serializers.CharField()
    label = serializers.CharField(required=False, allow_blank=True, default="")
    type = serializers.ChoiceField(choices=OperationFieldType.choices())
    required = serializers.BooleanField(default=False)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    placeholder = serializers.CharField(required=False, allow_blank=True, default="")
    default = serializers.JSONField(required=False)
    options = CustomOperationOptionSerializer(many=True, required=False)
    accept = serializers.ListField(child=serializers.CharField(), required=False)
    min_count = serializers.IntegerField(required=False, min_value=1)
    max_count = serializers.IntegerField(required=False, min_value=1)
    minimum = serializers.FloatField(required=False)
    maximum = serializers.FloatField(required=False)
    step = serializers.FloatField(required=False)

    def validate(self, attrs):
        if attrs["type"] == OperationFieldType.SELECT.value and not attrs.get("options"):
            raise serializers.ValidationError("Select fields require options")

        if attrs["type"] in {
            OperationFieldType.FILE.value,
            OperationFieldType.FILE_COLLECTION.value,
        } and attrs.get("options"):
            raise serializers.ValidationError("File fields cannot define options")

        if attrs["type"] == OperationFieldType.FILE_COLLECTION.value:
            min_count = attrs.get("min_count", 1)
            max_count = attrs.get("max_count")
            if max_count is not None and max_count < min_count:
                raise serializers.ValidationError("max_count cannot be smaller than min_count")

        return attrs


class CustomOperationDefinitionSerializer(serializers.ModelSerializer):
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
