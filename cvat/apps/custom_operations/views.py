# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import re
from functools import wraps
from pathlib import Path
from typing import Any

import requests
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.signing import BadSignature, TimestampSigner
from django.http import FileResponse, Http404
from django.utils.encoding import smart_str
from django.urls import reverse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from django.db import models
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from cvat.apps.lambda_manager.views import LambdaGateway

from .models import (
    CustomOperationDefinition,
    CustomOperationRun,
    CustomOperationRunStatus,
    OperationFieldType,
    OperationKind,
)
from .serializers import CustomOperationDefinitionSerializer


# views.py 是自定义操作功能的 HTTP API 层。
#
# 该文件承担两类职责：
# 1. 提供 CustomOperationDefinition 的增删改查接口，使前端能够读取和管理操作定义。
# 2. 提供 execute 接口，把前端提交的表单和文件转换成执行 payload，调用 local/Nuclio
#    执行器，保存 CustomOperationRun 运行记录和输出文件。
#
# 数据流概览：
# 前端 FormData -> DRF request -> input_schema 校验 -> base64 文件载荷 ->
# local/Nuclio 执行 -> 输出文件落盘 -> CustomOperationRun.output_collection -> 前端结果展示。
EXECUTION_META_FIELDS = {
    # 自定义输出目录。该字段控制结果保存位置，不会传给模型当作业务输入。
    "_output_path",
    # 是否保存输出文件或 JSON。False 时仍返回结果，但不强制落盘 JSON。
    "_save_outputs",
    # 当前工作流步骤编号。用于生成类似 01_xxx、02_xxx 的输出文件名前缀。
    "_step_index",
}


def return_response(success_code=status.HTTP_200_OK):
    """将普通 Python 返回值和常见异常统一转换成 DRF Response。
    Django REST Framework Serializer 的保存机制
    DRF ViewSet 方法通常需要返回 Response 对象。该装饰器把业务函数中的 dict/list
    返回值包装成 Response，并把 ValidationError、requests 异常等转换成合适的
    HTTP 状态码。这样 list/create/execute 等方法可以把主要代码集中在业务流程上。
    """
    def wrap_response(func):
        @wraps(func)
        def func_wrapper(*args, **kwargs):
            # data 保存业务函数返回的数据；status_code 保存最终 HTTP 状态码。
            data = None
            status_code = success_code
            try:
                # 执行真正的 ViewSet 方法，例如 list() 或 execute()。
                data = func(*args, **kwargs)
            except requests.ConnectionError as err:
                # 调 Nuclio/Lambda 网关时连接失败，通常表示服务不可达。
                status_code = status.HTTP_503_SERVICE_UNAVAILABLE
                data = str(err)
            except requests.HTTPError as err:
                # 下游 HTTP 服务返回错误状态码时，沿用下游状态码。
                status_code = err.response.status_code
                data = str(err)
            except requests.Timeout as err:
                # 下游服务超时，返回 504 Gateway Timeout。
                status_code = status.HTTP_504_GATEWAY_TIMEOUT
                data = str(err)
            except requests.RequestException as err:
                # requests 的其他异常统一视为服务器内部错误。
                status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
                data = str(err)
            except ValidationError as err:
                # Django ValidationError 表示请求参数不符合 schema。
                status_code = err.code or status.HTTP_400_BAD_REQUEST
                data = err.message
            except ObjectDoesNotExist as err:
                # 查询数据库对象失败，返回 400，提示引用对象不存在。
                status_code = status.HTTP_400_BAD_REQUEST
                data = str(err)

            if status_code == status.HTTP_204_NO_CONTENT:
                # 204 响应没有 body。
                return Response(status=status_code)

            return Response(data=data, status=status_code)

        return func_wrapper

    return wrap_response


def normalize_schema_value(field_schema: dict[str, Any], raw_value: Any) -> Any:
    """按照 input_schema 声明的字段类型转换请求值。

    input_schema 是操作定义中的字段说明。前端会根据它生成表单，后端也使用同一份
    schema 校验和转换数据。转换规则包括：
    - integer -> int。
    - number -> float。
    - boolean -> bool，兼容 true/false、1/0、yes/no。
    - json -> dict/list。
    - file/file_collection -> base64 文件载荷。

    返回值会进入执行 payload，发送给 local/Nuclio 函数。
    """
    field_type = field_schema["type"]

    if raw_value in (None, ""):
        # 空值先尝试使用 schema 中的 default。
        if "default" in field_schema:
            return field_schema["default"]
        # 没有 default 且字段必填时，抛校验错误。
        if field_schema.get("required"):
            raise ValidationError(f'Field "{field_schema["name"]}" is required')
        # 非必填字段允许为空。
        return None

    if field_type == OperationFieldType.STRING.value:
        # smart_str 将输入统一转换为 Python str。
        return smart_str(raw_value)
    if field_type == OperationFieldType.TEXT.value:
        return smart_str(raw_value)
    if field_type == OperationFieldType.INTEGER.value:
        try:
            # integer 字段必须能转成 int。
            return int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f'Field "{field_schema["name"]}" expects an integer value') from exc
    if field_type == OperationFieldType.NUMBER.value:
        try:
            # number 字段使用 float，兼容整数和小数。
            return float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f'Field "{field_schema["name"]}" expects a number value') from exc
    if field_type == OperationFieldType.BOOLEAN.value:
        if isinstance(raw_value, bool):
            return raw_value
        # 表单提交可能把布尔值变成字符串，因此需要手动识别。
        value = smart_str(raw_value).strip().lower()
        if value in {"true", "1", "yes", "on"}:
            return True
        if value in {"false", "0", "no", "off"}:
            return False
        raise ValidationError(f'Field "{field_schema["name"]}" expects a boolean value')
    if field_type == OperationFieldType.JSON.value:
        if isinstance(raw_value, (dict, list)):
            # 前端或测试代码可能已经传入 Python dict/list。
            return raw_value
        try:
            # 字符串形式的 JSON 需要解析。
            return json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise ValidationError(f'Field "{field_schema["name"]}" expects valid JSON') from exc
    if field_type == OperationFieldType.SELECT.value:
        options = field_schema.get("options", [])
        for option in options:
            option_value = option["value"]
            # 下拉框只允许提交 options 中存在的 value。
            if raw_value == option_value or smart_str(raw_value) == smart_str(option_value):
                return option_value

        raise ValidationError(
            f'Field "{field_schema["name"]}" has an invalid option "{raw_value}"'
        )
    if field_type == OperationFieldType.FILE.value:
        if not hasattr(raw_value, "read"):
            # Django 上传文件对象应当具备 read() 方法。
            raise ValidationError(f'Field "{field_schema["name"]}" expects a file upload')

        return encode_uploaded_file(field_schema, raw_value)
    if field_type == OperationFieldType.FILE_COLLECTION.value:
        if isinstance(raw_value, str):
            try:
                # 工作流引用可能以 JSON 字符串形式传入，例如 {"source": "run", "run_id": 1}。
                raw_value = json.loads(raw_value)
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    f'Field "{field_schema["name"]}" expects files or a source reference'
                ) from exc

        if isinstance(raw_value, dict) and raw_value.get("source") == "run":
            # source=run 表示当前输入不是新上传文件，而是来自某次历史运行的输出。
            if not raw_value.get("run_id"):
                raise ValidationError(f'Field "{field_schema["name"]}" source requires run_id')
            return {"source": "run", "run_id": raw_value["run_id"]}

        if not isinstance(raw_value, list):
            # file_collection 正常情况下应是上传文件列表。
            raise ValidationError(f'Field "{field_schema["name"]}" expects one or more files')

        min_count = int(field_schema.get("min_count") or 1)
        max_count = field_schema.get("max_count")
        if len(raw_value) < min_count:
            raise ValidationError(
                f'Field "{field_schema["name"]}" expects at least {min_count} file(s)'
            )
        if max_count is not None and len(raw_value) > int(max_count):
            raise ValidationError(
                f'Field "{field_schema["name"]}" expects at most {max_count} file(s)'
            )

        # 每个上传文件都转换成 base64 文件载荷。
        return [encode_uploaded_file(field_schema, item) for item in raw_value]

    raise ValidationError(f'Unsupported input type "{field_type}"')


def parse_bool(raw_value: Any, default: bool = True) -> bool:
    """解析 FormData 中的布尔值。

    multipart/form-data 中的布尔值经常以字符串形式出现。该函数用于解析执行控制字段，
    例如 `_save_outputs`。
    """
    if raw_value in (None, ""):
        # 字段缺失或为空字符串时使用默认值。
        return default
    if isinstance(raw_value, bool):
        return raw_value
    # FormData 中常见的布尔字符串需要统一转换。
    value = smart_str(raw_value).strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    raise ValidationError("Expected a boolean value")


def accepts_file(field_schema: dict[str, Any], file_name: str, content_type: str) -> bool:
    """判断文件是否符合字段 schema 中的 accept 规则。

    accept 可以写 MIME 类型、扩展名或通配符，例如 image/png、.onnx、image/*。
    没有声明 accept 时表示接受任意文件。
    """
    accept = field_schema.get("accept") or []
    if not accept:
        # schema 没有限制文件类型时，任何文件都可接受。
        return True

    lowered_name = file_name.lower()
    lowered_content_type = (content_type or "").lower()
    for rule in accept:
        lowered_rule = rule.lower().strip()
        if not lowered_rule:
            continue
        if lowered_rule in {"*", "*/*"}:
            # 通配符表示接受任意文件。
            return True
        if lowered_rule.startswith(".") and lowered_name.endswith(lowered_rule):
            # .png/.onnx 这种规则按文件名后缀判断。
            return True
        if lowered_rule.endswith("/*") and lowered_content_type.startswith(lowered_rule[:-1]):
            # image/* 这种规则按 MIME 类型前缀判断。
            return True
        if lowered_rule == lowered_content_type:
            # image/png 这种规则按完整 MIME 类型判断。
            return True
    return False


def encode_uploaded_file(field_schema: dict[str, Any], raw_value: Any) -> dict[str, Any]:
    """将 Django 上传文件对象转换成 JSON 可传输的文件载荷。

    local/Nuclio 执行器接收的是 JSON payload，不能直接接收 Django UploadedFile。
    因此文件会被读取为 bytes，再编码成 base64 字符串。执行函数收到 payload 后
    可根据 encoding=data 进行解码。
    """
    if not hasattr(raw_value, "read"):
        raise ValidationError(f'Field "{field_schema["name"]}" expects a file upload')

    name = getattr(raw_value, "name", field_schema["name"])
    # 优先使用上传对象携带的 content_type；没有时根据文件名猜测 MIME 类型。
    content_type = getattr(raw_value, "content_type", None) or mimetypes.guess_type(name)[0]
    content_type = content_type or "application/octet-stream"
    if not accepts_file(field_schema, name, content_type):
        raise ValidationError(
            f'Field "{field_schema["name"]}" does not accept file "{name}" ({content_type})'
        )

    # read() 会消耗文件指针。
    raw_bytes = raw_value.read()
    if hasattr(raw_value, "seek"):
        # 将文件指针恢复到开头，避免后续代码再次读取时读不到内容。
        raw_value.seek(0)

    return {
        "name": name,
        "content_type": content_type,
        "encoding": "base64",
        "data": base64.b64encode(raw_bytes).decode("ascii"),
    }


def get_field_files(request, field_name: str) -> list[Any]:
    """从 multipart 请求中读取 file_collection 字段的所有文件。

    兼容 image 和 image[0]/image[1] 两种多文件上传形式。
    """
    # getlist(field_name) 读取多个同名文件字段，例如 image=image1、image=image2。
    files = list(request.FILES.getlist(field_name))
    indexed_prefix = f"{field_name}["
    indexed_files = []
    for key in request.FILES.keys():
        # 兼容 image[0]、image[1] 这种带索引的字段名。
        if key.startswith(indexed_prefix) and key.endswith("]"):
            indexed_files.extend(request.FILES.getlist(key))
    # 优先返回同名字段形式；没有时返回索引字段形式。
    return files or indexed_files


def get_raw_field_value(field_schema: dict[str, Any], request) -> Any:
    """根据字段类型，从 request.data 或 request.FILES 中取得原始值。

    普通字段来自 request.data。文件字段来自 request.FILES。file_collection 需要兼容
    多文件上传和工作流引用两种情况。
    """
    field_name = field_schema["name"]
    if field_schema["type"] == OperationFieldType.FILE_COLLECTION.value:
        files = get_field_files(request, field_name)
        if files:
            # 实际上传了文件时，返回文件列表。
            return files
        # 没有上传文件时，可能是工作流引用 {"source": "run", "run_id": ...}。
        return request.data.get(field_name)
    # 单文件字段优先从 request.FILES 取；普通字段从 request.data 取。
    return request.FILES.get(field_name, request.data.get(field_name))


def is_allowed_request_key(key: str, allowed_names: set[str]) -> bool:
    """判断请求字段名是否属于当前操作允许接收的字段。

    allowed_names 来自 operation.input_schema。执行控制字段 `_output_path`、
    `_save_outputs`、`_step_index` 不属于模型输入，但属于执行协议的一部分。
    image[0] 这种索引形式属于多文件字段，也需要放行。
    """
    if key in allowed_names or key in EXECUTION_META_FIELDS or key.startswith("_"):
        return True
    # file_collection 多文件上传可能使用 image[0] 这种字段名。
    return any(key.startswith(f"{name}[") and key.endswith("]") for name in allowed_names)


def build_execution_payload(operation: CustomOperationDefinition, request) -> dict[str, Any]:
    """将一次执行请求转换成 local/Nuclio 函数需要的 payload。

    主要步骤：
    1. 根据 operation.input_schema 校验请求字段，拒绝未知字段。
    2. 将每个字段转换成 schema 声明的类型，文件字段转换成 base64 文件载荷。
    3. 操作存在 artifact 时，生成带签名的 artifact_url，供执行函数下载模型文件。
    4. 写入 cvat_context，保存操作 ID、名称、类型、函数名等运行上下文。
    """
    # operation.input_schema 来自数据库，描述该操作允许接收哪些输入字段。
    schema = operation.input_schema or []
    # 按字段名建立索引，便于判断请求里是否出现未知字段。
    schema_by_name = {item["name"]: item for item in schema}
    allowed_names = set(schema_by_name)

    # request.data 保存普通表单字段，request.FILES 保存上传文件字段。
    # 两者合并后逐个检查，任何 schema 未声明的字段都会被视为未知字段。
    unknown_fields = {
        key
        for key in (set(request.data.keys()) | set(request.FILES.keys()))
        if not is_allowed_request_key(key, allowed_names)
    }
    if unknown_fields:
        raise ValidationError(
            f"Unknown input field(s): {', '.join(sorted(unknown_fields))}"
        )

    payload: dict[str, Any] = {}
    for field in schema:
        # 按 schema 中的每个字段，从 request 中取原始值。
        raw_value = get_raw_field_value(field, request)
        # 原始值转换成执行器可接收的类型。
        payload[field["name"]] = normalize_schema_value(field, raw_value)

    if operation.artifact:
        # artifact 是操作绑定的模型文件或资源文件。
        # TimestampSigner 生成带签名的 token，防止任意人直接访问 artifact。
        token = TimestampSigner(salt="custom-operation-artifact").sign(str(operation.artifact_key))
        # request.build_absolute_uri 会生成完整 URL，例如 http://host/api/...
        artifact_url = request.build_absolute_uri(
            f"{reverse('custom_operation-artifact', args=[operation.pk])}?signature={token}"
        )
        context = payload.get("cvat_context", {})
        if not isinstance(context, dict):
            # cvat_context 必须是 dict；如果外部传了其他类型，则丢弃并重新创建。
            context = {}
        context.update(
            {
                # 执行函数可通过 artifact_url 下载模型文件。
                "artifact_url": artifact_url,
                "artifact_name": operation.artifact.name.rsplit("/", 1)[-1],
            }
        )
    else:
        context = payload.get("cvat_context", {})
        if not isinstance(context, dict):
            context = {}

    # operation 上下文让执行函数知道当前操作定义的基础信息。
    context["operation"] = {
        "id": operation.id,
        "name": operation.name,
        "kind": operation.kind,
        "nuclio_function": operation.nuclio_function,
    }
    # cvat_context 是保留字段，统一放置 CVAT 后端补充的运行上下文。
    payload["cvat_context"] = context
    return payload


def execution_options_from_request(request) -> dict[str, Any]:
    """读取执行控制参数。

    这些参数不属于模型输入，而属于执行协议：
    - `_output_path`：输出目录。
    - `_save_outputs`：是否保存输出文件或 JSON。
    - `_step_index`：当前步骤序号，用于生成输出文件名前缀。
    """
    return {
        # 输出目录为空时，后续 resolve_output_dir 会自动生成目录。
        "output_path": smart_str(request.data.get("_output_path", "")).strip(),
        # _save_outputs 缺失时默认保存输出。
        "save_outputs": parse_bool(request.data.get("_save_outputs"), default=True),
        # step_index 用于输出文件名前缀，默认第 1 步。
        "step_index": int(request.data.get("_step_index") or 1),
    }


def slugify(value: str) -> str:
    """将任意字符串整理为适合作为输出文件名前缀的短字符串。"""
    # 非字母、数字、下划线、点、短横线的字符统一替换为短横线。
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._")
    # 限制长度，避免生成过长文件名。空字符串时使用 operation。
    return slug[:64] or "operation"


def resolve_output_dir(output_path: str, run: CustomOperationRun) -> Path:
    """解析并创建本次运行的输出目录。

    相对路径会放到 MEDIA_DATA_ROOT/custom-operations/results/ 下。
    空路径会使用 run.run_key 作为目录名，使每次运行拥有独立输出目录。
    """
    if output_path:
        candidate = Path(output_path).expanduser()
        if not candidate.is_absolute():
            # 用户传相对路径时，限制在 CVAT 媒体目录的 custom-operations/results 下。
            candidate = (
                Path(settings.MEDIA_DATA_ROOT)
                / "custom-operations"
                / "results"
                / candidate
            )
    else:
        candidate = (
            Path(settings.MEDIA_DATA_ROOT)
            / "custom-operations"
            / "results"
            # run_key 是 UUID，适合作为默认唯一目录名。
            / str(run.run_key)
        )

    # parents=True 会创建所有缺失的父目录；exist_ok=True 表示目录已存在也不报错。
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def scrub_payload(value: Any) -> Any:
    """清理 payload/result 中的大体积 base64 内容，避免数据库记录过大。

    文件 data 字段会替换成 `<base64:长度>`，从而保留摘要信息而不保存完整文件内容。
    """
    if isinstance(value, dict):
        if value.get("encoding") == "base64" and "data" in value:
            # 复制一份 dict，避免直接修改原始对象。
            cleaned = dict(value)
            cleaned["data"] = f"<base64:{len(value['data'])}>"
            return cleaned
        # 递归清理 dict 中的每个 value。
        return {key: scrub_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        # list 中可能包含文件载荷，也需要递归处理。
        return [scrub_payload(item) for item in value]
    return value


def load_file_payload_from_path(field_schema: dict[str, Any], path: str, name: str | None = None) -> dict[str, Any]:
    """将磁盘上的已有输出文件重新读取为 base64 文件载荷。

    工作流串联依赖该函数。后续步骤引用前序 run 时，后端需要从上一次运行的
    output_collection 取得文件路径，再将文件转换成当前步骤可接收的输入格式。
    """
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        # 前序输出文件缺失时，当前工作流步骤无法继续执行。
        raise ValidationError(f'Previous output file "{path}" is not available')

    file_name = name or file_path.name
    content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    if not accepts_file(field_schema, file_name, content_type):
        # 前序输出文件也必须满足当前字段的 accept 限制。
        raise ValidationError(
            f'Previous output file "{file_name}" is not accepted by field "{field_schema["name"]}"'
        )

    return {
        "name": file_name,
        "content_type": content_type,
        "encoding": "base64",
        # 重新读取磁盘文件并编码。这样当前步骤无需知道文件来自上传还是前序输出。
        "data": base64.b64encode(file_path.read_bytes()).decode("ascii"),
        "source_path": str(file_path),
    }


def resolve_run_source(field_schema: dict[str, Any], source: dict[str, Any]) -> list[dict[str, Any]]:
    """根据前序 CustomOperationRun 解析当前步骤的输入文件。

    工作流中，“使用前一步输出”会被前端转换成 {"source": "run", "run_id": 123}。
    该函数读取对应 run 的 output_collection，在其中寻找符合当前字段 accept 规则的
    输出文件，并将文件重新编码为 base64 输入载荷。
    """
    try:
        # source["run_id"] 指向前序 CustomOperationRun 的主键。
        run = CustomOperationRun.objects.get(pk=source["run_id"])
    except CustomOperationRun.DoesNotExist as exc:
        raise ValidationError(f'Field "{field_schema["name"]}" references an unknown run') from exc

    # output_collection 是 build_output_collection 保存的结构。
    # items 中的每一项对应一次批处理输入的输出。
    items = (run.output_collection or {}).get("items") or []
    payloads: list[dict[str, Any]] = []
    for item in items:
        # 兼容 files 和 outputs 两种字段名。
        files = item.get("files") or item.get("outputs") or []
        selected_file = None
        for file_info in files:
            path = file_info.get("path")
            name = file_info.get("name")
            content_type = file_info.get("content_type") or mimetypes.guess_type(name or path or "")[0]
            if path and accepts_file(field_schema, name or Path(path).name, content_type or ""):
                # 每个 item 选择第一个符合当前字段 accept 规则的输出文件。
                selected_file = file_info
                break
        if selected_file:
            payloads.append(
                load_file_payload_from_path(
                    field_schema,
                    selected_file["path"],
                    selected_file.get("name"),
                )
            )
        elif files:
            # 有输出文件，但没有一个符合当前输入字段要求。
            raise ValidationError(
                f'Run {run.id} has no output file accepted by field "{field_schema["name"]}"'
            )
        else:
            # 当前 item 没有任何输出文件。
            raise ValidationError(f"Run {run.id} item {item.get('index')} has no output files")

    return payloads


def build_iteration_payloads(
    operation: CustomOperationDefinition,
    base_payload: dict[str, Any],
    run: CustomOperationRun,
    options: dict[str, Any],
) -> tuple[list[dict[str, Any]], Path]:
    """将一次执行请求拆分为一个或多个实际调用 payload。

    没有 file_collection 字段时，只生成一个 payload。
    存在 file_collection 字段时，每个集合元素对应一次实际执行。例如上传 5 张图片，
    会生成 5 个 payload，后端随后按顺序调用执行器 5 次。

    每个 payload 都会补充 cvat_context：run_id、run_key、output_dir、
    output_prefix 和 batch 信息。这些上下文用于输出命名、文件保存和结果追踪。
    """
    # 先确定本次 run 的输出目录。
    output_dir = resolve_output_dir(options["output_path"], run)
    # 找出所有 file_collection 字段。每个集合字段都可能触发批量执行。
    collection_fields = [
        field
        for field in (operation.input_schema or [])
        if field["type"] == OperationFieldType.FILE_COLLECTION.value
    ]
    operation_slug = slugify(operation.name)

    if not collection_fields:
        # 没有集合输入时，只执行一次。
        payload = dict(base_payload)
        context = dict(payload.get("cvat_context") or {})
        context.update(
            {
                "run_id": run.id,
                "run_key": str(run.run_key),
                "output_dir": str(output_dir),
                "output_prefix": f"{options['step_index']:02d}_{operation_slug}_0001",
                "batch": {"index": 0, "count": 1},
            }
        )
        payload["cvat_context"] = context
        return [payload], output_dir

    collections: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for field in collection_fields:
        value = base_payload[field["name"]]
        if isinstance(value, dict) and value.get("source") == "run":
            # 输入来自前序 run 时，需要把前序输出文件解析成当前输入列表。
            items = resolve_run_source(field, value)
        else:
            # 输入来自用户上传时，value 已经是文件载荷列表。
            items = value

        if not items:
            raise ValidationError(f'Field "{field["name"]}" must contain at least one file')
        collections.append((field, items))

    # 多个 file_collection 字段同时存在时，长度必须一致，才能按相同 index 配对执行。
    counts = {len(items) for _, items in collections}
    if len(counts) != 1:
        details = ", ".join(f'{field["name"]}={len(items)}' for field, items in collections)
        raise ValidationError(f"Input collections must have the same length: {details}")

    count = counts.pop()
    payloads = []
    for index in range(count):
        # 每个 index 生成一个独立 payload。
        payload = dict(base_payload)
        first_file = None
        for field, items in collections:
            # 当前字段取当前 index 对应的文件。
            payload[field["name"]] = items[index]
            if first_file is None:
                first_file = items[index]

        # 使用第一份文件名作为输出文件名的一部分，便于追溯结果来自哪张图。
        source_stem = slugify(Path((first_file or {}).get("name") or "input").stem)
        context = dict(payload.get("cvat_context") or {})
        context.update(
            {
                "run_id": run.id,
                "run_key": str(run.run_key),
                "output_dir": str(output_dir),
                "output_prefix": (
                    f"{options['step_index']:02d}_{operation_slug}_{index + 1:04d}__{source_stem}"
                ),
                "batch": {"index": index, "count": count},
            }
        )
        payload["cvat_context"] = context
        payloads.append(payload)

    return payloads, output_dir


def normalize_output_file(file_info: dict[str, Any], run: CustomOperationRun | None = None) -> dict[str, Any]:
    """将执行结果中的输出文件信息整理成统一格式。

    Nuclio/local 函数可能只返回 path，也可能返回 name/content_type/kind。
    该函数补齐缺失字段，使 output_collection 中的文件结构稳定。
    """
    path = file_info.get("path")
    # name 优先使用函数返回值；没有 name 时从 path 中提取文件名；再没有则使用 output。
    name = file_info.get("name") or (Path(path).name if path else "output")
    # content_type 优先使用函数返回值；没有时按文件名猜测；猜不到则使用通用二进制类型。
    content_type = file_info.get("content_type") or mimetypes.guess_type(name)[0] or "application/octet-stream"
    kind = file_info.get("kind")
    if not kind:
        # kind 是给前端展示用的粗分类。
        if content_type.startswith("image/"):
            kind = "image"
        elif content_type.startswith("text/") or content_type == "application/json":
            kind = "text"
        else:
            kind = "file"

    normalized = {
        "name": name,
        "path": path,
        "content_type": content_type,
        "kind": kind,
    }
    if run is not None and path:
        normalized["url"] = reverse(
            "custom_operation-result",
            kwargs={"run_id": run.id, "filename": name},
        )
    return normalized


def save_inline_output_file(
    file_info: dict[str, Any],
    output_dir: Path,
    default_name: str,
    run: CustomOperationRun | None = None,
) -> dict[str, Any]:
    """保存执行函数以内联 base64 形式返回的输出文件。

    部分执行函数不会自行写入磁盘，而是在响应 JSON 中返回 encoding=base64 的文件。
    该函数负责解码 data、写入 output_dir，并返回标准化后的文件信息。
    """
    name = Path(file_info.get("name") or default_name).name
    content_type = file_info.get("content_type") or mimetypes.guess_type(name)[0]
    content_type = content_type or "application/octet-stream"

    if "." not in name:
        # 没有扩展名时，根据 content_type 尝试补一个扩展名。
        name = f"{name}{mimetypes.guess_extension(content_type) or '.bin'}"

    try:
        # validate=True 会严格检查 base64 格式。
        raw_bytes = base64.b64decode(file_info["data"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError(f'Output file "{name}" is not valid base64 data') from exc

    output_path = output_dir / name
    # 将执行函数返回的内联文件真正写到磁盘。
    output_path.write_bytes(raw_bytes)
    return normalize_output_file(
        {
            **file_info,
            "name": output_path.name,
            "path": str(output_path),
            "content_type": content_type,
        },
        run=run,
    )


def extract_result_files(
    result: dict[str, Any],
    output_dir: Path,
    output_prefix: str,
    run: CustomOperationRun | None = None,
) -> list[dict[str, Any]]:
    """从一次执行结果中提取输出文件信息。

    支持 result["output"] 单文件和 result["outputs"] 多文件两种格式。
    返回 path 时直接记录路径；返回 base64 时保存成文件。
    """
    files = []
    # 约定一：函数返回单个 output。
    output = result.get("output")
    if isinstance(output, dict):
        if output.get("path"):
            # 函数已经把文件写到磁盘，只需标准化文件信息。
            files.append(normalize_output_file(output, run=run))
        elif output.get("encoding") == "base64" and output.get("data"):
            # 函数返回 base64 文件内容，后端负责保存。
            files.append(save_inline_output_file(output, output_dir, output_prefix, run=run))

    # 约定二：函数返回多个 outputs。
    outputs = result.get("outputs")
    if isinstance(outputs, list):
        for index, item in enumerate(outputs):
            if not isinstance(item, dict):
                # 忽略非 dict 项，避免坏数据影响其他输出文件。
                continue
            if item.get("path"):
                files.append(normalize_output_file(item, run=run))
            elif item.get("encoding") == "base64" and item.get("data"):
                files.append(save_inline_output_file(item, output_dir, f"{output_prefix}_{index + 1}", run=run))

    return files


def save_result_json(
    result: dict[str, Any],
    output_dir: Path,
    output_prefix: str,
    run: CustomOperationRun | None = None,
) -> dict[str, Any]:
    """在执行函数没有返回独立文件时，将完整 JSON 结果保存为 .json 文件。"""
    output_path = output_dir / f"{output_prefix}.json"
    # ensure_ascii=False 保留中文等非 ASCII 字符，indent=2 便于人工查看。
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    file_info = {
        "name": output_path.name,
        "path": str(output_path),
        "content_type": "application/json",
        "kind": "text",
    }
    if run is not None:
        file_info["url"] = reverse(
            "custom_operation-result",
            kwargs={"run_id": run.id, "filename": output_path.name},
        )
    return file_info


def build_output_collection(
    run: CustomOperationRun,
    operation: CustomOperationDefinition,
    item_payloads: list[dict[str, Any]],
    results: list[dict[str, Any]],
    output_dir: Path,
    save_outputs: bool,
) -> dict[str, Any]:
    """将所有批处理结果整理成 output_collection。

    output_collection 是工作流串联的关键结构。它记录本次 run 的 ID、操作信息、
    每个输入项对应的输出文件列表和结果摘要。后续步骤引用前序 run 时，
    resolve_run_source() 会读取此处保存的文件路径。
    """
    collection_items = []
    # item_payloads 和 results 一一对应：第 n 个 payload 的输出是第 n 个 result。
    for index, (payload, result) in enumerate(zip(item_payloads, results)):
        context = payload.get("cvat_context") or {}
        output_prefix = context.get("output_prefix") or f"result_{index + 1:04d}"
        files = extract_result_files(result, output_dir, output_prefix, run=run)
        if save_outputs and not files:
            # 函数只返回 JSON、没有返回文件时，将 JSON 保存成结果文件。
            files = [save_result_json(result, output_dir, output_prefix, run=run)]

        collection_items.append(
            {
                "index": index,
                "files": files,
                "result": scrub_payload(result),
            }
        )

    return {
        # id 是对外展示用的 UUID；run_id 是数据库主键，后续步骤引用时使用。
        "id": str(run.run_key),
        "run_id": run.id,
        "operation_id": operation.id,
        "operation_name": operation.name,
        "count": len(collection_items),
        "items": collection_items,
    }


def invoke_via_nuclio(function_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """调用实际执行器：本地函数或 Nuclio 函数。

    function_name 以 local. 开头时，执行 local_operations.py 中注册的 Python 函数。
    其他函数名通过 CVAT 现有 LambdaGateway 发送给 Nuclio。
    """
    if function_name.startswith("local."):
        # local.* 不经过 Nuclio，直接调用本地 Python 函数。
        from .local_operations import execute_local_operation

        return execute_local_operation(function_name, payload)

    # LambdaGateway 是 CVAT 已有的 Nuclio/Lambda 调用封装。
    gateway = LambdaGateway()
    return gateway._http(  # pylint: disable=protected-access
        method="post",
        url="/api/function_invocations",
        data=payload,
        # x-nuclio-function-name 告诉网关要调用哪个 Nuclio 函数。
        headers={"x-nuclio-function-name": function_name, "x-nuclio-path": "/"},
    )

# 为 OpenAPI 文档设置 custom-operations 标签，使相关接口在 Swagger UI 中归为同组。
@extend_schema(tags=["custom-operations"])
# 为 ViewSet 的默认动作补充 OpenAPI operation_id 和 summary。
@extend_schema_view(
    list=extend_schema(operation_id="custom_operation_list", summary="List custom definitions"),
    retrieve=extend_schema(
        operation_id="custom_operation_retrieve",
        summary="Retrieve a custom definition",
    ),
)
class CustomOperationViewSet(viewsets.ModelViewSet):
    """自定义操作定义的 API 控制器。

    该 ViewSet 同时负责两类事情：
    1. 管理 CustomOperationDefinition：列表、详情、创建、更新、删除。
    2. 执行某个定义：POST /api/custom-operations/definitions/{id}/execute。

    前端 Workflows 页面加载操作列表和提交运行请求时，主要访问该类提供的接口。
    """
    # queryset 是 ModelViewSet 查询数据库的基础集合。
    queryset = CustomOperationDefinition.objects.all()
    # serializer_class 决定输入如何校验、模型对象如何转换成 JSON。
    serializer_class = CustomOperationDefinitionSerializer
    # 默认所有接口都要求用户已登录。
    permission_classes = [permissions.IsAuthenticated]
    # 支持 JSON、multipart 文件上传和普通表单三种请求体。
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    # 当前接口不按 organization 参数做权限扩展。
    iam_supports_organization_params = False
    # 这些字段可被 DRF/filter 后端用于过滤。
    filter_fields = ["kind", "is_active"]
    # 这些字段可用于搜索。
    search_fields = ["name", "description", "nuclio_function"]
    # 这些字段可用于排序。
    ordering_fields = ["id", "name", "kind", "created_date", "updated_date"]
    # 默认按更新时间倒序，再按 id 倒序。
    ordering = ["-updated_date", "-id"]
    # URL 中的 pk 只允许数字，避免把非数字路径误识别为对象 ID。
    lookup_value_regex = "[0-9]+"

    def get_queryset(self):
        """按查询参数过滤操作列表。

        支持 kind=model/augmentation、search=关键字、is_active=true/false。
        """
        queryset = super().get_queryset()
        # query_params 对应 URL 问号后的参数，例如 ?kind=model。
        kind = self.request.query_params.get("kind")
        search = self.request.query_params.get("search")
        is_active = self.request.query_params.get("is_active")

        if kind:
            # 按操作类型过滤：model 或 augmentation。
            queryset = queryset.filter(kind=kind)
        if search:
            # Q 对象用于构造 OR 查询：名称、说明、函数名任意命中即可。
            queryset = queryset.filter(
                models.Q(name__icontains=search)
                | models.Q(description__icontains=search)
                | models.Q(nuclio_function__icontains=search)
            )
        if is_active in {"true", "false"}:
            # URL 参数是字符串，需要转换成 bool。
            queryset = queryset.filter(is_active=(is_active == "true"))

        return queryset

    def get_permissions(self):
        """控制接口权限。

        默认接口要求登录用户。artifact 下载接口例外，因为 artifact_url 本身带有
        签名和过期时间，Nuclio 或外部执行环境不一定携带 CVAT 登录态。
        """
        if self.action == "artifact":
            # artifact 使用签名 URL 保护，因此允许匿名请求下载。
            return [permissions.AllowAny()]
        return super().get_permissions()

    @return_response()
    def list(self, request, *args, **kwargs):
        """返回自定义操作列表，供 Workflows 页面左侧模型/增强列表使用。"""
        # many=True 表示序列化一个对象列表。
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return serializer.data

    @return_response()
    def retrieve(self, request, *args, **kwargs):
        """返回单个自定义操作详情。"""
        # get_object() 根据 URL 中的 pk 查询单个模型对象，并执行权限检查。
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return serializer.data

    @return_response(status.HTTP_201_CREATED)
    def create(self, request, *args, **kwargs):
        """创建一个自定义操作定义。

        该接口支持 multipart/form-data，因此可以同时提交 JSON 字段和模型 artifact 文件。
        """
        # data=request.data 表示用请求数据创建 serializer。
        serializer = self.get_serializer(data=request.data)
        # raise_exception=True 表示校验失败时直接抛异常，由 return_response 转成 HTTP 响应。
        serializer.is_valid(raise_exception=True)
        # save() 创建数据库记录。
        instance = serializer.save()
        return self.get_serializer(instance).data

    @return_response()
    def partial_update(self, request, *args, **kwargs):
        """局部更新一个自定义操作定义，例如说明、schema、启用状态或 artifact。"""
        instance = self.get_object()
        # partial=True 表示允许只提交部分字段。
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        # save() 更新已有数据库记录。
        instance = serializer.save()
        return self.get_serializer(instance).data

    @return_response(status.HTTP_204_NO_CONTENT)
    def destroy(self, request, *args, **kwargs):
        """删除一个自定义操作定义。

        models.py 中的 delete() 会同步删除 artifact 文件，避免模型文件残留。
        """
        instance = self.get_object()
        # 模型类重写了 delete()，删除数据库记录时会一并清理 artifact 文件。
        instance.delete()
        return None

    @action(detail=True, methods=["get"], url_path="artifact")
    def artifact(self, request, pk=None):
        """下载操作关联的模型 artifact。(自定义操作附带的文件资源)

        该接口不依赖登录权限，而依赖 URL 中的 signature。签名由 TimestampSigner 生成，
        24 小时有效；签名错误、过期、artifact_key 不匹配都会返回 404。
        """
        operation = self.get_object()
        if not operation.artifact:
            # 没有关联文件时，对外统一表现为 404。
            raise Http404("Artifact not found")

        token = request.query_params.get("signature")
        if not token:
            # 没有签名参数时不允许下载。
            raise Http404("Artifact not found")

        signer = TimestampSigner(salt="custom-operation-artifact")
        try:
            # max_age 限制签名 24 小时有效。
            decoded = signer.unsign(token, max_age=24 * 60 * 60)
        except BadSignature as exc:
            raise Http404("Artifact not found") from exc

        if decoded != str(operation.artifact_key):
            # 签名内容必须与当前操作的 artifact_key 匹配。
            raise Http404("Artifact not found")

        operation.artifact.open("rb")
        # FileResponse 以流式方式返回文件内容，适合下载大文件。
        response = FileResponse(operation.artifact, as_attachment=True, filename=operation.artifact.name.rsplit("/", 1)[-1])
        # no-store 避免浏览器或代理缓存带签名的 artifact 响应。
        response["Cache-Control"] = "no-store"
        return response

    @extend_schema(
        operation_id="custom_operation_execute",
        summary="Execute a custom definition via Nuclio",
        request=OpenApiTypes.OBJECT,
        responses={
            "200": OpenApiResponse(response=OpenApiTypes.OBJECT, description="Execution result"),
        },
    )
    @return_response()
    @action(detail=True, methods=["post"], url_path="execute")
    def execute(self, request, pk=None):
        """执行一个自定义操作。

        该接口是 Workflows 页面“运行当前步骤”和“运行工作流”最终调用的后端入口。
        1. 根据 URL 中的 pk 查询 CustomOperationDefinition。
        2. 读取执行选项，例如输出目录、是否保存输出。
        3. build_execution_payload() 校验输入并将文件转为 base64 文件载荷。
        4. 创建 CustomOperationRun，初始状态为 running。
        5. build_iteration_payloads() 处理批量文件和前序 run 输出引用。
        6. invoke_via_nuclio() 逐项调用 local/Nuclio 函数。
        7. build_output_collection() 保存输出文件并整理 output_collection。
        8. 成功则 run.status=succeeded；失败则 run.status=failed 并保存错误。
        9. 返回操作信息、请求摘要、run 信息、执行结果和输出集合。
        """
        # 根据 URL pk 取得要执行的操作定义。
        operation = self.get_object()
        # 解析 _output_path、_save_outputs、_step_index 等执行控制字段。
        options = execution_options_from_request(request)
        # 将前端请求转换为基础执行 payload。
        payload = build_execution_payload(operation, request)
        # 先创建运行记录。后续无论成功失败，都可以在数据库中留下状态。
        run = CustomOperationRun.objects.create(
            operation=operation,
            created_by=request.user if request.user and request.user.is_authenticated else None,
            output_path=options["output_path"],
            request_payload=scrub_payload(payload),
        )

        try:
            # 将基础 payload 拆分为实际执行列表，并确定输出目录。
            item_payloads, output_dir = build_iteration_payloads(operation, payload, run, options)
            run.output_path = str(output_dir)
            # 只更新 output_path 和 updated_date，减少数据库写入字段。
            run.save(update_fields=["output_path", "updated_date"])

            # 按顺序调用每个 payload。当前实现是同步执行，不是后台异步任务。
            results = [
                invoke_via_nuclio(operation.nuclio_function, item_payload)
                for item_payload in item_payloads
            ]
            # 整理输出文件、保存 JSON 或内联 base64 文件。
            output_collection = build_output_collection(
                run,
                operation,
                item_payloads,
                results,
                output_dir,
                options["save_outputs"],
            )
            # 执行成功后更新运行状态和结果。
            run.status = CustomOperationRunStatus.SUCCEEDED.value
            run.result = {"items": scrub_payload(results)}
            run.output_collection = output_collection
            run.save(update_fields=["status", "result", "output_collection", "updated_date"])
        except Exception as exc:
            # 任意异常都会记录到 run.error，并将状态置为 failed。
            run.status = CustomOperationRunStatus.FAILED.value
            run.error = str(exc)
            run.save(update_fields=["status", "error", "updated_date"])
            # 继续抛出异常，由 return_response 装饰器转换成 HTTP 响应。
            raise

        return {
            "operation": self.get_serializer(operation).data,
            "payload": scrub_payload(payload),
            "run": {
                "id": run.id,
                "run_key": str(run.run_key),
                "status": run.status,
                "output_path": run.output_path,
                "created_date": run.created_date,
                "updated_date": run.updated_date,
            },
            "result": run.result,
            "output_collection": run.output_collection,
        }


def serve_result_file(request, run_id: int, filename: str):
    """按 run_id 和文件名返回某次自定义操作运行生成的结果文件。

    前端可将该接口作为图片 src、下载链接或文本 fetch 地址。接口只允许访问
    CustomOperationRun.output_collection 中登记过的文件，避免通过 filename 任意读取磁盘文件。
    """

    try:
        run = CustomOperationRun.objects.get(pk=run_id)
    except CustomOperationRun.DoesNotExist as exc:
        raise Http404("Result run not found") from exc

    for item in (run.output_collection or {}).get("items") or []:
        for file_info in item.get("files") or []:
            if file_info.get("name") != filename:
                continue

            path = file_info.get("path")
            if not path:
                raise Http404("Result file not found")

            file_path = Path(path)
            if not file_path.exists() or not file_path.is_file():
                raise Http404("Result file not found")

            response = FileResponse(
                file_path.open("rb"),
                as_attachment=False,
                filename=file_info.get("name") or file_path.name,
                content_type=file_info.get("content_type") or mimetypes.guess_type(file_path.name)[0],
            )
            response["Cache-Control"] = "no-store"
            return response

    raise Http404("Result file not found")
