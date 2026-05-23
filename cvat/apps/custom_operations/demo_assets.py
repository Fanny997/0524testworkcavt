from __future__ import annotations

import struct


def _varint(value: int) -> bytes:
    chunks = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            chunks.append(byte | 0x80)
        else:
            chunks.append(byte)
            break
    return bytes(chunks)


def _key(field_number: int, wire_type: int) -> bytes:
    return _varint((field_number << 3) | wire_type)


def _field_varint(field_number: int, value: int) -> bytes:
    return _key(field_number, 0) + _varint(value)


def _field_len(field_number: int, value: bytes) -> bytes:
    return _key(field_number, 2) + _varint(len(value)) + value


def _field_string(field_number: int, value: str) -> bytes:
    return _field_len(field_number, value.encode("utf-8"))


def _dimension(value: int) -> bytes:
    return _field_varint(1, value)


def _shape(dimensions: list[int]) -> bytes:
    return b"".join(_field_len(1, _dimension(value)) for value in dimensions)


def _tensor_type(dimensions: list[int]) -> bytes:
    tensor = _field_varint(1, 1)  # FLOAT
    tensor += _field_len(2, _shape(dimensions))
    return _field_len(1, tensor)


def _value_info(name: str, dimensions: list[int]) -> bytes:
    return _field_string(1, name) + _field_len(2, _tensor_type(dimensions))


def build_demo_detector_onnx() -> bytes:
    """Build a tiny ONNX model artifact for demo registration.

    The model exposes one image input and one constant detection output with
    shape [1, 1, 6]: x1, y1, x2, y2, score, class_id.
    """

    detection_values = [0.25, 0.25, 0.75, 0.75, 0.90, 0.0]
    detection_tensor = b"".join(
        [
            _field_varint(1, 1),
            _field_varint(1, 1),
            _field_varint(1, 6),
            _field_varint(2, 1),  # FLOAT
            _field_string(8, "detections"),
            _field_len(9, struct.pack("<6f", *detection_values)),
        ]
    )

    graph = b"".join(
        [
            _field_string(2, "demo_detector_graph"),
            _field_len(5, detection_tensor),
            _field_len(11, _value_info("image", [1, 3, 224, 224])),
            _field_len(12, _value_info("detections", [1, 1, 6])),
        ]
    )

    opset = _field_varint(2, 13)
    return b"".join(
        [
            _field_varint(1, 8),
            _field_string(2, "cvat-demo"),
            _field_len(7, graph),
            _field_len(8, opset),
        ]
    )

