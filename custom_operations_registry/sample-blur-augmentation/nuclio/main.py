import base64
import io
import json
from pathlib import Path

from PIL import Image, ImageFilter


def _read_json_body(event):
    """兼容 Nuclio 在不同版本里把 body 传成 dict、bytes 或 str 的情况。"""
    body = event.body
    if isinstance(body, dict):
        return body
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    return json.loads(body)


def _decode_image(file_payload):
    """把 CVAT 后端传来的 base64 文件载荷还原成 Pillow 图片。"""
    raw = base64.b64decode(file_payload["data"])
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _encode_image(image, output_format):
    """把处理后的图片重新编码为 base64，交给 CVAT 后端保存。"""
    buffer = io.BytesIO()
    if output_format == "jpeg":
        image.save(buffer, format="JPEG", quality=95)
        return "jpg", "image/jpeg", base64.b64encode(buffer.getvalue()).decode("ascii")

    image.save(buffer, format="PNG")
    return "png", "image/png", base64.b64encode(buffer.getvalue()).decode("ascii")


def handler(context, event):
    data = _read_json_body(event)

    image_payload = data["image"]
    radius = float(data.get("radius") or 2.0)
    output_format = data.get("format") or "png"
    cvat_context = data.get("cvat_context") or {}

    image = _decode_image(image_payload)
    result_image = image.filter(ImageFilter.GaussianBlur(radius=radius))

    extension, content_type, encoded = _encode_image(result_image, output_format)
    output_prefix = cvat_context.get("output_prefix") or Path(image_payload["name"]).stem
    output_name = f"{output_prefix}_blur.{extension}"

    result = {
        "type": "augmentation",
        "operation": "gaussian_blur",
        "input": {
            "name": image_payload["name"],
            "width": image.width,
            "height": image.height,
        },
        "parameters": {
            "radius": radius,
            "format": output_format,
        },
        "output": {
            "name": output_name,
            "content_type": content_type,
            "kind": "image",
            "encoding": "base64",
            "data": encoded,
        },
    }

    return context.Response(
        body=json.dumps(result, ensure_ascii=False),
        content_type="application/json",
        status_code=200,
    )
