import base64
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import onnxruntime as ort
except ImportError:
    ort = None


MODEL_PATH = Path("/opt/nuclio/models/foreground_detector.onnx")


def init_context(context):
    """Nuclio 冷启动时只加载一次 ONNX 模型，后续请求复用同一个 session。"""
    context.logger.info("加载本地 ONNX 目标检测示例模型: %s", MODEL_PATH)
    context.user_data.input_name = "input"
    context.user_data.output_name = "detections"
    if ort is None:
        context.logger.warn("onnxruntime not installed; using the Python fallback for this demo.")
        context.user_data.session = None
        return

    session_options = ort.SessionOptions()
    session_options.log_severity_level = 3
    context.user_data.session = ort.InferenceSession(
        str(MODEL_PATH),
        sess_options=session_options,
        providers=["CPUExecutionProvider"],
    )
    context.user_data.input_name = context.user_data.session.get_inputs()[0].name
    context.user_data.output_name = context.user_data.session.get_outputs()[0].name


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


def build_candidate_feature(image, threshold, min_area_ratio):
    """从图片中提取一个候选检测框，并整理成 ONNX 输入。

    这里为了让示例模型足够小，候选框提取放在 Python 前处理里完成。
    ONNX 模型接收 `[x1, y1, x2, y2, score]`，输出同样结构。替换真实模型时，
    可以把这里改成 YOLO/RT-DETR 等模型需要的 resize、normalize、CHW 转换。
    """
    gray = image.convert("L")
    width, height = gray.size
    pixels = gray.load()

    min_x, min_y = width, height
    max_x, max_y = -1, -1
    foreground_count = 0

    for y in range(height):
        for x in range(width):
            if pixels[x, y] >= threshold:
                foreground_count += 1
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

    if max_x < min_x or max_y < min_y:
        return np.array([[0, 0, 0, 0, 0]], dtype=np.float32)

    box_area = float((max_x - min_x + 1) * (max_y - min_y + 1))
    image_area = float(width * height)
    if box_area < image_area * min_area_ratio:
        return np.array([[0, 0, 0, 0, 0]], dtype=np.float32)

    # score 用前景像素占候选框面积的比例表示，范围 0..1。
    score = min(1.0, foreground_count / max(box_area, 1.0))
    return np.array([[min_x, min_y, max_x + 1, max_y + 1, score]], dtype=np.float32)


def run_onnx_detector(context, feature):
    """执行本地 ONNX 推理。"""
    if context.user_data.session is None:
        return feature[0].astype(float).tolist()

    outputs = context.user_data.session.run(
        [context.user_data.output_name],
        {context.user_data.input_name: feature},
    )
    return outputs[0][0].astype(float).tolist()


def handler(context, event):
    data = _read_json_body(event)

    image_payload = data["image"]
    threshold = int(data.get("threshold") or 180)
    min_area_ratio = float(data.get("min_area") or 0.002)
    cvat_context = data.get("cvat_context") or {}

    image = _decode_image(image_payload)
    feature = build_candidate_feature(image, threshold, min_area_ratio)
    x1, y1, x2, y2, score = run_onnx_detector(context, feature)

    detections = []
    if score > 0 and x2 > x1 and y2 > y1:
        detections.append(
            {
                "label": "foreground",
                "label_id": 0,
                "type": "rectangle",
                "points": [x1, y1, x2, y2],
                "confidence": score,
            }
        )

    result = {
        "type": "object_detection",
        "model": {
            "format": "onnx",
            "path": str(MODEL_PATH),
            "input": context.user_data.input_name,
            "output": context.user_data.output_name,
        },
        "input": {
            "name": image_payload["name"],
            "width": image.width,
            "height": image.height,
        },
        "parameters": {
            "threshold": threshold,
            "min_area": min_area_ratio,
        },
        "batch": cvat_context.get("batch"),
        "detections": detections,
    }

    return context.Response(
        body=json.dumps(result, ensure_ascii=False),
        content_type="application/json",
        status_code=200,
    )
