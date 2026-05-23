# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from django.test import SimpleTestCase

from .models import CustomOperationDefinition, OperationKind
from .serializers import CustomOperationDefinitionSerializer


class CustomOperationDefinitionSerializerTestCase(SimpleTestCase):
    def test_model_definition_requires_artifact_on_create(self):
        serializer = CustomOperationDefinitionSerializer(data={
            "name": "Detector",
            "kind": OperationKind.MODEL.value,
            "description": "",
            "nuclio_function": "detector",
            "input_schema": [],
            "output_schema": {},
            "is_active": True,
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn("artifact", serializer.errors)

    def test_model_definition_requires_artifact_when_updating_kind(self):
        instance = CustomOperationDefinition(
            name="Augmentation",
            kind=OperationKind.AUGMENTATION.value,
            description="",
            nuclio_function="augment",
            input_schema=[],
            output_schema={},
            is_active=True,
        )
        serializer = CustomOperationDefinitionSerializer(
            instance=instance,
            data={"kind": OperationKind.MODEL.value},
            partial=True,
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("artifact", serializer.errors)

    def test_file_collection_schema_is_validated(self):
        serializer = CustomOperationDefinitionSerializer(data={
            "name": "Batch augmentation",
            "kind": OperationKind.AUGMENTATION.value,
            "description": "",
            "nuclio_function": "augment",
            "input_schema": [
                {
                    "name": "image",
                    "type": "file_collection",
                    "required": True,
                    "accept": ["image/png", "image/jpeg"],
                    "min_count": 1,
                    "max_count": 10,
                }
            ],
            "output_schema": {},
            "is_active": True,
        })

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_registry_can_register_nuclio_model_without_artifact(self):
        serializer = CustomOperationDefinitionSerializer(
            data={
                "name": "Nuclio Detector",
                "kind": OperationKind.MODEL.value,
                "description": "",
                "nuclio_function": "nuclio-detector",
                "input_schema": [],
                "output_schema": {},
                "is_active": True,
            },
            context={"allow_model_without_artifact": True},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
