# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

import os
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort


class ModelHandler:
    def __init__(self, labels):
        self.labels = labels
        self.input_size = int(os.environ.get("DINO_INPUT_SIZE", 800))
        self.output_layout = os.environ.get("DINO_OUTPUT_LAYOUT", "logits_boxes")
        self.model = self.load_network(os.environ.get("DINO_MODEL_PATH", "/opt/nuclio/dino.onnx"))
        self.input_details = [input_.name for input_ in self.model.get_inputs()]
        self.output_details = [output.name for output in self.model.get_outputs()]

    @staticmethod
    def load_network(model):
        model_path = Path(model)
        if not model_path.is_file():
            raise FileNotFoundError(f"Cannot find DINO ONNX model: {model}")

        device = os.environ.get("DINO_DEVICE", "cpu").lower()
        providers = ["CPUExecutionProvider"]
        if device in {"cuda", "gpu"}:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        options = ort.SessionOptions()
        options.log_severity_level = 3
        return ort.InferenceSession(str(model_path), providers=providers, sess_options=options)

    def preprocess(self, image):
        rgb = np.asarray(image.convert("RGB"))
        height, width = rgb.shape[:2]
        scale = min(self.input_size / width, self.input_size / height)
        resized_width = int(round(width * scale))
        resized_height = int(round(height * scale))

        resized = cv2.resize(rgb, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
        canvas = np.zeros((self.input_size, self.input_size, 3), dtype=np.uint8)
        canvas[:resized_height, :resized_width] = resized

        tensor = canvas.astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        tensor = (tensor - mean) / std
        tensor = tensor.transpose(2, 0, 1)[None, ...]
        return np.ascontiguousarray(tensor), scale, width, height

    @staticmethod
    def softmax(values):
        values = values - np.max(values, axis=-1, keepdims=True)
        exp = np.exp(values)
        return exp / np.sum(exp, axis=-1, keepdims=True)

    @staticmethod
    def cxcywh_to_xyxy(boxes, width, height):
        cx, cy, box_width, box_height = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        x1 = (cx - box_width / 2) * width
        y1 = (cy - box_height / 2) * height
        x2 = (cx + box_width / 2) * width
        y2 = (cy + box_height / 2) * height
        return np.stack([x1, y1, x2, y2], axis=1)

    def postprocess_logits_boxes(self, outputs, width, height):
        logits = np.squeeze(outputs[0], axis=0)
        boxes = np.squeeze(outputs[1], axis=0)

        scores = self.softmax(logits)
        if scores.shape[-1] == len(self.labels) + 1:
            scores = scores[:, :-1]

        class_ids = np.argmax(scores, axis=1)
        confidences = scores[np.arange(scores.shape[0]), class_ids]
        boxes = self.cxcywh_to_xyxy(boxes, width, height)
        return boxes, confidences, class_ids

    @staticmethod
    def postprocess_boxes_scores_labels(outputs, scale, width, height):
        boxes = np.squeeze(outputs[0])
        scores = np.squeeze(outputs[1])
        class_ids = np.squeeze(outputs[2]).astype(np.int64)

        if boxes.ndim == 1:
            boxes = boxes[None, :]
        if scores.ndim == 0:
            scores = scores[None]
        if class_ids.ndim == 0:
            class_ids = class_ids[None]

        if np.max(boxes) <= 1.0:
            boxes[:, [0, 2]] *= width
            boxes[:, [1, 3]] *= height
        else:
            boxes /= scale

        return boxes, scores, class_ids

    def infer(self, image, threshold):
        tensor, scale, width, height = self.preprocess(image)
        outputs = self.model.run(self.output_details, {self.input_details[0]: tensor})

        if self.output_layout == "logits_boxes":
            boxes, scores, class_ids = self.postprocess_logits_boxes(outputs, width, height)
        elif self.output_layout == "boxes_scores_labels":
            boxes, scores, class_ids = self.postprocess_boxes_scores_labels(
                outputs, scale, width, height
            )
        else:
            raise ValueError(f"Unsupported DINO_OUTPUT_LAYOUT: {self.output_layout}")

        results = []
        for box, score, class_id in zip(boxes, scores, class_ids):
            class_id = int(class_id)
            if float(score) < threshold or class_id not in self.labels:
                continue

            xtl = max(0, min(width, int(round(box[0]))))
            ytl = max(0, min(height, int(round(box[1]))))
            xbr = max(0, min(width, int(round(box[2]))))
            ybr = max(0, min(height, int(round(box[3]))))
            if xbr <= xtl or ybr <= ytl:
                continue

            results.append(
                {
                    "confidence": str(float(score)),
                    "label": self.labels[class_id],
                    "points": [xtl, ytl, xbr, ybr],
                    "type": "rectangle",
                }
            )

        return results
