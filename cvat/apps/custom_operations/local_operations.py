from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Any

from django.conf import settings


def _load_image(file_payload: dict[str, Any]):
    from PIL import Image, ImageOps, ImageFilter

    raw = base64.b64decode(file_payload["data"])
    image = Image.open(BytesIO(raw)).convert("RGB")
    return image, ImageOps, ImageFilter


def _save_image(image, payload: dict[str, Any], fallback_prefix: str) -> str:
    context = payload.get("cvat_context") or {}
    output_dir = Path(context.get("output_dir") or Path(settings.MEDIA_DATA_ROOT) / "custom-operations" / "results")
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{context.get('output_prefix') or fallback_prefix}.png"
    output_path = output_dir / filename
    counter = 1
    while output_path.exists():
        output_path = output_dir / f"{output_path.stem}-{counter}{output_path.suffix}"
        counter += 1
    image.save(output_path, format="PNG")
    return str(output_path)


def _demo_augmentation(payload: dict[str, Any]) -> dict[str, Any]:
    image, image_ops, image_filter = _load_image(payload["image"])
    operation = payload.get("operation") or "grayscale"
    intensity = float(payload.get("intensity") or 1.0)

    if operation == "grayscale":
        processed = image_ops.grayscale(image).convert("RGB")
    elif operation == "flip_horizontal":
        processed = image_ops.mirror(image)
    elif operation == "invert":
        processed = image_ops.invert(image)
    elif operation == "blur":
        processed = image.filter(image_filter.GaussianBlur(radius=max(0.1, intensity * 4)))
    else:
        raise ValueError(f"Unsupported augmentation operation: {operation}")

    output_path = _save_image(processed, payload, "augmentation")
    return {
        "type": "augmentation",
        "operation": operation,
        "intensity": intensity,
        "input": {
            "name": payload["image"].get("name"),
            "width": image.width,
            "height": image.height,
        },
        "output": {
            "name": Path(output_path).name,
            "format": "png",
            "path": output_path,
            "content_type": "image/png",
            "kind": "image",
        },
    }


def _demo_onnx_detector(payload: dict[str, Any]) -> dict[str, Any]:
    image, _, _ = _load_image(payload["image"])
    threshold = float(payload.get("threshold") or 0.5)
    score = 0.9
    detections = []

    if score >= threshold:
        detections.append(
            {
                "label": payload.get("label") or "object",
                "confidence": score,
                "box": {
                    "x1": round(image.width * 0.25, 2),
                    "y1": round(image.height * 0.25, 2),
                    "x2": round(image.width * 0.75, 2),
                    "y2": round(image.height * 0.75, 2),
                },
            }
        )

    return {
        "type": "object_detection",
        "runtime": "local-demo",
        "model_artifact": payload.get("cvat_context", {}).get("artifact_name"),
        "model_artifact_url": payload.get("cvat_context", {}).get("artifact_url"),
        "input": {
            "name": payload["image"].get("name"),
            "width": image.width,
            "height": image.height,
        },
        "threshold": threshold,
        "detections": detections,
    }


LOCAL_OPERATIONS = {
    "local.demo_augmentation": _demo_augmentation,
    "local.demo_onnx_detector": _demo_onnx_detector,
}


def register_local_operation(function_name: str, handler) -> None:
    LOCAL_OPERATIONS[function_name] = handler


def execute_local_operation(function_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    from .registry_loader import load_registry_handlers

    load_registry_handlers()

    try:
        handler = LOCAL_OPERATIONS[function_name]
    except KeyError as exc:
        raise ValueError(f"Unknown local custom operation: {function_name}") from exc

    return handler(payload)
