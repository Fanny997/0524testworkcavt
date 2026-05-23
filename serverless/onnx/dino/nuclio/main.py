# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

import base64
import io
import json

import yaml
from model_handler import ModelHandler
from PIL import Image


def init_context(context):
    context.logger.info("Init DINO ONNX context... 0%")

    with open("/opt/nuclio/function.yaml", "rb") as function_file:
        function_config = yaml.safe_load(function_file)

    labels_spec = function_config["metadata"]["annotations"]["spec"]
    labels = {item["id"]: item["name"] for item in json.loads(labels_spec)}

    context.user_data.model = ModelHandler(labels)
    context.logger.info("Init DINO ONNX context... 100%")


def handler(context, event):
    context.logger.info("Run DINO ONNX model")

    data = event.body
    image_buffer = io.BytesIO(base64.b64decode(data["image"]))
    threshold = float(data.get("threshold", 0.5))
    image = Image.open(image_buffer).convert("RGB")

    results = context.user_data.model.infer(image, threshold)

    return context.Response(
        body=json.dumps(results),
        headers={},
        content_type="application/json",
        status_code=200,
    )
