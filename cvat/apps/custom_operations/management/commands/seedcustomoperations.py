from __future__ import annotations

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from cvat.apps.custom_operations.demo_assets import build_demo_detector_onnx
from cvat.apps.custom_operations.models import CustomOperationDefinition, OperationKind


DEMO_AUGMENTATION_SCHEMA = [
    {
        "name": "image",
        "label": "Images",
        "type": "file_collection",
        "required": True,
        "description": "Upload one or more PNG/JPEG images to augment. Files are processed in upload order.",
        "accept": ["image/png", "image/jpeg"],
        "min_count": 1,
    },
    {
        "name": "operation",
        "label": "Operation",
        "type": "select",
        "required": True,
        "default": "grayscale",
        "options": [
            {"label": "Grayscale", "value": "grayscale"},
            {"label": "Flip horizontal", "value": "flip_horizontal"},
            {"label": "Invert", "value": "invert"},
            {"label": "Blur", "value": "blur"},
        ],
    },
    {
        "name": "intensity",
        "label": "Intensity",
        "type": "number",
        "required": False,
        "default": 1.0,
        "minimum": 0.1,
        "maximum": 2.0,
        "step": 0.1,
        "description": "Used by blur; ignored by other demo operations.",
    },
]


DEMO_DETECTOR_SCHEMA = [
    {
        "name": "image",
        "label": "Images",
        "type": "file_collection",
        "required": True,
        "description": "Upload one or more PNG/JPEG images for demo detection. Files are processed in upload order.",
        "accept": ["image/png", "image/jpeg"],
        "min_count": 1,
    },
    {
        "name": "threshold",
        "label": "Confidence threshold",
        "type": "number",
        "required": False,
        "default": 0.5,
        "minimum": 0.0,
        "maximum": 1.0,
        "step": 0.05,
    },
    {
        "name": "label",
        "label": "Label",
        "type": "string",
        "required": False,
        "default": "object",
        "placeholder": "object",
    },
]


class Command(BaseCommand):
    help = "Register demo custom operations for the Workflows page."

    def handle(self, *args, **options):
        augmentation, aug_created = CustomOperationDefinition.objects.update_or_create(
            nuclio_function="local.demo_augmentation",
            defaults={
                "name": "Demo Image Augmentation",
                "kind": OperationKind.AUGMENTATION.value,
                "description": "Local demo augmentation: grayscale, flip, invert, or blur an uploaded image.",
                "input_schema": DEMO_AUGMENTATION_SCHEMA,
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string"},
                        "output": {"type": "object"},
                    },
                },
                "is_active": True,
            },
        )

        detector, det_created = CustomOperationDefinition.objects.update_or_create(
            nuclio_function="local.demo_onnx_detector",
            defaults={
                "name": "Demo ONNX Object Detector",
                "kind": OperationKind.MODEL.value,
                "description": (
                    "Local demo detector with a generated ONNX artifact. "
                    "It returns a deterministic center bounding box for smoke testing."
                ),
                "input_schema": DEMO_DETECTOR_SCHEMA,
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "detections": {"type": "array"},
                    },
                },
                "is_active": True,
            },
        )

        if detector.artifact:
            detector.artifact.delete(save=False)
        detector.artifact.save(
            "demo_detector.onnx",
            ContentFile(build_demo_detector_onnx()),
            save=True,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Registered demo custom operations: "
                f"{augmentation.name} ({'created' if aug_created else 'updated'}), "
                f"{detector.name} ({'created' if det_created else 'updated'})"
            )
        )
