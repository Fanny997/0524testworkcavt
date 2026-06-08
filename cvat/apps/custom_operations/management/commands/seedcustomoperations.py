from __future__ import annotations

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from cvat.apps.custom_operations.demo_assets import build_demo_detector_onnx
from cvat.apps.custom_operations.models import CustomOperationDefinition, OperationKind


# DEMO_AUGMENTATION_SCHEMA 是一份输入表单声明。
# Workflows 页面读取该结构后，可以动态生成文件上传框、下拉框和数字输入框。
# 后端执行时，views.py 也会使用同一份结构校验请求参数并转换数据类型。
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


# DEMO_DETECTOR_SCHEMA 是本地检测 demo 的输入声明。
# image 使用 file_collection，表示一次运行可以接收多张图片。
# threshold 和 label 是普通参数，用于演示数值字段和字符串字段的校验流程。
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
    """注册两个本地演示用 CustomOperationDefinition 的管理命令。

    本文件对应命令：

        python manage.py seedcustomoperations

    seed 的含义是“填充初始数据”。该命令用于在数据库中创建两个本地 demo：
    - local.demo_augmentation：图片增强示例。
    - local.demo_onnx_detector：ONNX 检测示例。

    local. 前缀表示该操作不经过 Nuclio，而是由 local_operations.py 中的 Python
    函数直接执行。该机制适合开发环境验证 Workflows 页面、参数校验、运行记录和
    输出保存链路。
    """

    help = "Register demo custom operations for the Workflows page."

    def handle(self, *args, **options):
        """创建或更新本地 demo 操作。

        update_or_create 是 Django ORM 的常用方法：
        - 查询条件命中已有记录时，使用 defaults 更新该记录。
        - 查询条件没有命中时，使用查询条件和 defaults 创建新记录。

        本命令以 nuclio_function 作为唯一业务标识，因此重复执行不会创建重复 demo。
        """

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

        # 检测 demo 用于验证“模型类操作”的完整链路。它会返回确定性的中心框，
        # 重点不在真实检测精度，而在验证注册、执行、记录和结果展示是否打通。
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

        # 模型类操作通常带有 artifact。此处生成一个极小的 ONNX 文件并保存到
        # detector.artifact，用于验证 artifact 上传、存储和下载 URL 生成流程。
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
