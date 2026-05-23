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


EXECUTION_META_FIELDS = {
    "_output_path",
    "_save_outputs",
    "_step_index",
}


def return_response(success_code=status.HTTP_200_OK):
    def wrap_response(func):
        @wraps(func)
        def func_wrapper(*args, **kwargs):
            data = None
            status_code = success_code
            try:
                data = func(*args, **kwargs)
            except requests.ConnectionError as err:
                status_code = status.HTTP_503_SERVICE_UNAVAILABLE
                data = str(err)
            except requests.HTTPError as err:
                status_code = err.response.status_code
                data = str(err)
            except requests.Timeout as err:
                status_code = status.HTTP_504_GATEWAY_TIMEOUT
                data = str(err)
            except requests.RequestException as err:
                status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
                data = str(err)
            except ValidationError as err:
                status_code = err.code or status.HTTP_400_BAD_REQUEST
                data = err.message
            except ObjectDoesNotExist as err:
                status_code = status.HTTP_400_BAD_REQUEST
                data = str(err)

            if status_code == status.HTTP_204_NO_CONTENT:
                return Response(status=status_code)

            return Response(data=data, status=status_code)

        return func_wrapper

    return wrap_response


def normalize_schema_value(field_schema: dict[str, Any], raw_value: Any) -> Any:
    field_type = field_schema["type"]

    if raw_value in (None, ""):
        if "default" in field_schema:
            return field_schema["default"]
        if field_schema.get("required"):
            raise ValidationError(f'Field "{field_schema["name"]}" is required')
        return None

    if field_type == OperationFieldType.STRING.value:
        return smart_str(raw_value)
    if field_type == OperationFieldType.TEXT.value:
        return smart_str(raw_value)
    if field_type == OperationFieldType.INTEGER.value:
        try:
            return int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f'Field "{field_schema["name"]}" expects an integer value') from exc
    if field_type == OperationFieldType.NUMBER.value:
        try:
            return float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f'Field "{field_schema["name"]}" expects a number value') from exc
    if field_type == OperationFieldType.BOOLEAN.value:
        if isinstance(raw_value, bool):
            return raw_value
        value = smart_str(raw_value).strip().lower()
        if value in {"true", "1", "yes", "on"}:
            return True
        if value in {"false", "0", "no", "off"}:
            return False
        raise ValidationError(f'Field "{field_schema["name"]}" expects a boolean value')
    if field_type == OperationFieldType.JSON.value:
        if isinstance(raw_value, (dict, list)):
            return raw_value
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise ValidationError(f'Field "{field_schema["name"]}" expects valid JSON') from exc
    if field_type == OperationFieldType.SELECT.value:
        options = field_schema.get("options", [])
        for option in options:
            option_value = option["value"]
            if raw_value == option_value or smart_str(raw_value) == smart_str(option_value):
                return option_value

        raise ValidationError(
            f'Field "{field_schema["name"]}" has an invalid option "{raw_value}"'
        )
    if field_type == OperationFieldType.FILE.value:
        if not hasattr(raw_value, "read"):
            raise ValidationError(f'Field "{field_schema["name"]}" expects a file upload')

        return encode_uploaded_file(field_schema, raw_value)
    if field_type == OperationFieldType.FILE_COLLECTION.value:
        if isinstance(raw_value, str):
            try:
                raw_value = json.loads(raw_value)
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    f'Field "{field_schema["name"]}" expects files or a source reference'
                ) from exc

        if isinstance(raw_value, dict) and raw_value.get("source") == "run":
            if not raw_value.get("run_id"):
                raise ValidationError(f'Field "{field_schema["name"]}" source requires run_id')
            return {"source": "run", "run_id": raw_value["run_id"]}

        if not isinstance(raw_value, list):
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

        return [encode_uploaded_file(field_schema, item) for item in raw_value]

    raise ValidationError(f'Unsupported input type "{field_type}"')


def parse_bool(raw_value: Any, default: bool = True) -> bool:
    if raw_value in (None, ""):
        return default
    if isinstance(raw_value, bool):
        return raw_value
    value = smart_str(raw_value).strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    raise ValidationError("Expected a boolean value")


def accepts_file(field_schema: dict[str, Any], file_name: str, content_type: str) -> bool:
    accept = field_schema.get("accept") or []
    if not accept:
        return True

    lowered_name = file_name.lower()
    lowered_content_type = (content_type or "").lower()
    for rule in accept:
        lowered_rule = rule.lower().strip()
        if not lowered_rule:
            continue
        if lowered_rule in {"*", "*/*"}:
            return True
        if lowered_rule.startswith(".") and lowered_name.endswith(lowered_rule):
            return True
        if lowered_rule.endswith("/*") and lowered_content_type.startswith(lowered_rule[:-1]):
            return True
        if lowered_rule == lowered_content_type:
            return True
    return False


def encode_uploaded_file(field_schema: dict[str, Any], raw_value: Any) -> dict[str, Any]:
    if not hasattr(raw_value, "read"):
        raise ValidationError(f'Field "{field_schema["name"]}" expects a file upload')

    name = getattr(raw_value, "name", field_schema["name"])
    content_type = getattr(raw_value, "content_type", None) or mimetypes.guess_type(name)[0]
    content_type = content_type or "application/octet-stream"
    if not accepts_file(field_schema, name, content_type):
        raise ValidationError(
            f'Field "{field_schema["name"]}" does not accept file "{name}" ({content_type})'
        )

    raw_bytes = raw_value.read()
    if hasattr(raw_value, "seek"):
        raw_value.seek(0)

    return {
        "name": name,
        "content_type": content_type,
        "encoding": "base64",
        "data": base64.b64encode(raw_bytes).decode("ascii"),
    }


def get_field_files(request, field_name: str) -> list[Any]:
    files = list(request.FILES.getlist(field_name))
    indexed_prefix = f"{field_name}["
    indexed_files = []
    for key in request.FILES.keys():
        if key.startswith(indexed_prefix) and key.endswith("]"):
            indexed_files.extend(request.FILES.getlist(key))
    return files or indexed_files


def get_raw_field_value(field_schema: dict[str, Any], request) -> Any:
    field_name = field_schema["name"]
    if field_schema["type"] == OperationFieldType.FILE_COLLECTION.value:
        files = get_field_files(request, field_name)
        if files:
            return files
        return request.data.get(field_name)
    return request.FILES.get(field_name, request.data.get(field_name))


def is_allowed_request_key(key: str, allowed_names: set[str]) -> bool:
    if key in allowed_names or key in EXECUTION_META_FIELDS or key.startswith("_"):
        return True
    return any(key.startswith(f"{name}[") and key.endswith("]") for name in allowed_names)


def build_execution_payload(operation: CustomOperationDefinition, request) -> dict[str, Any]:
    schema = operation.input_schema or []
    schema_by_name = {item["name"]: item for item in schema}
    allowed_names = set(schema_by_name)

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
        raw_value = get_raw_field_value(field, request)
        payload[field["name"]] = normalize_schema_value(field, raw_value)

    if operation.artifact:
        token = TimestampSigner(salt="custom-operation-artifact").sign(str(operation.artifact_key))
        artifact_url = request.build_absolute_uri(
            f"{reverse('custom_operation-artifact', args=[operation.pk])}?signature={token}"
        )
        context = payload.get("cvat_context", {})
        if not isinstance(context, dict):
            context = {}
        context.update(
            {
                "artifact_url": artifact_url,
                "artifact_name": operation.artifact.name.rsplit("/", 1)[-1],
            }
        )
    else:
        context = payload.get("cvat_context", {})
        if not isinstance(context, dict):
            context = {}

    context["operation"] = {
        "id": operation.id,
        "name": operation.name,
        "kind": operation.kind,
        "nuclio_function": operation.nuclio_function,
    }
    payload["cvat_context"] = context
    return payload


def execution_options_from_request(request) -> dict[str, Any]:
    return {
        "output_path": smart_str(request.data.get("_output_path", "")).strip(),
        "save_outputs": parse_bool(request.data.get("_save_outputs"), default=True),
        "step_index": int(request.data.get("_step_index") or 1),
    }


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._")
    return slug[:64] or "operation"


def resolve_output_dir(output_path: str, run: CustomOperationRun) -> Path:
    if output_path:
        candidate = Path(output_path).expanduser()
        if not candidate.is_absolute():
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
            / str(run.run_key)
        )

    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def scrub_payload(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("encoding") == "base64" and "data" in value:
            cleaned = dict(value)
            cleaned["data"] = f"<base64:{len(value['data'])}>"
            return cleaned
        return {key: scrub_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_payload(item) for item in value]
    return value


def load_file_payload_from_path(field_schema: dict[str, Any], path: str, name: str | None = None) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise ValidationError(f'Previous output file "{path}" is not available')

    file_name = name or file_path.name
    content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    if not accepts_file(field_schema, file_name, content_type):
        raise ValidationError(
            f'Previous output file "{file_name}" is not accepted by field "{field_schema["name"]}"'
        )

    return {
        "name": file_name,
        "content_type": content_type,
        "encoding": "base64",
        "data": base64.b64encode(file_path.read_bytes()).decode("ascii"),
        "source_path": str(file_path),
    }


def resolve_run_source(field_schema: dict[str, Any], source: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        run = CustomOperationRun.objects.get(pk=source["run_id"])
    except CustomOperationRun.DoesNotExist as exc:
        raise ValidationError(f'Field "{field_schema["name"]}" references an unknown run') from exc

    items = (run.output_collection or {}).get("items") or []
    payloads: list[dict[str, Any]] = []
    for item in items:
        files = item.get("files") or item.get("outputs") or []
        selected_file = None
        for file_info in files:
            path = file_info.get("path")
            name = file_info.get("name")
            content_type = file_info.get("content_type") or mimetypes.guess_type(name or path or "")[0]
            if path and accepts_file(field_schema, name or Path(path).name, content_type or ""):
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
            raise ValidationError(
                f'Run {run.id} has no output file accepted by field "{field_schema["name"]}"'
            )
        else:
            raise ValidationError(f"Run {run.id} item {item.get('index')} has no output files")

    return payloads


def build_iteration_payloads(
    operation: CustomOperationDefinition,
    base_payload: dict[str, Any],
    run: CustomOperationRun,
    options: dict[str, Any],
) -> tuple[list[dict[str, Any]], Path]:
    output_dir = resolve_output_dir(options["output_path"], run)
    collection_fields = [
        field
        for field in (operation.input_schema or [])
        if field["type"] == OperationFieldType.FILE_COLLECTION.value
    ]
    operation_slug = slugify(operation.name)

    if not collection_fields:
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
            items = resolve_run_source(field, value)
        else:
            items = value

        if not items:
            raise ValidationError(f'Field "{field["name"]}" must contain at least one file')
        collections.append((field, items))

    counts = {len(items) for _, items in collections}
    if len(counts) != 1:
        details = ", ".join(f'{field["name"]}={len(items)}' for field, items in collections)
        raise ValidationError(f"Input collections must have the same length: {details}")

    count = counts.pop()
    payloads = []
    for index in range(count):
        payload = dict(base_payload)
        first_file = None
        for field, items in collections:
            payload[field["name"]] = items[index]
            if first_file is None:
                first_file = items[index]

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


def normalize_output_file(file_info: dict[str, Any]) -> dict[str, Any]:
    path = file_info.get("path")
    name = file_info.get("name") or (Path(path).name if path else "output")
    content_type = file_info.get("content_type") or mimetypes.guess_type(name)[0] or "application/octet-stream"
    kind = file_info.get("kind")
    if not kind:
        if content_type.startswith("image/"):
            kind = "image"
        elif content_type.startswith("text/") or content_type == "application/json":
            kind = "text"
        else:
            kind = "file"

    return {
        "name": name,
        "path": path,
        "content_type": content_type,
        "kind": kind,
    }


def save_inline_output_file(
    file_info: dict[str, Any],
    output_dir: Path,
    default_name: str,
) -> dict[str, Any]:
    name = Path(file_info.get("name") or default_name).name
    content_type = file_info.get("content_type") or mimetypes.guess_type(name)[0]
    content_type = content_type or "application/octet-stream"

    if "." not in name:
        name = f"{name}{mimetypes.guess_extension(content_type) or '.bin'}"

    try:
        raw_bytes = base64.b64decode(file_info["data"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError(f'Output file "{name}" is not valid base64 data') from exc

    output_path = output_dir / name
    output_path.write_bytes(raw_bytes)
    return normalize_output_file(
        {
            **file_info,
            "name": output_path.name,
            "path": str(output_path),
            "content_type": content_type,
        }
    )


def extract_result_files(result: dict[str, Any], output_dir: Path, output_prefix: str) -> list[dict[str, Any]]:
    files = []
    output = result.get("output")
    if isinstance(output, dict):
        if output.get("path"):
            files.append(normalize_output_file(output))
        elif output.get("encoding") == "base64" and output.get("data"):
            files.append(save_inline_output_file(output, output_dir, output_prefix))

    outputs = result.get("outputs")
    if isinstance(outputs, list):
        for index, item in enumerate(outputs):
            if not isinstance(item, dict):
                continue
            if item.get("path"):
                files.append(normalize_output_file(item))
            elif item.get("encoding") == "base64" and item.get("data"):
                files.append(save_inline_output_file(item, output_dir, f"{output_prefix}_{index + 1}"))

    return files


def save_result_json(result: dict[str, Any], output_dir: Path, output_prefix: str) -> dict[str, Any]:
    output_path = output_dir / f"{output_prefix}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "name": output_path.name,
        "path": str(output_path),
        "content_type": "application/json",
        "kind": "text",
    }


def build_output_collection(
    run: CustomOperationRun,
    operation: CustomOperationDefinition,
    item_payloads: list[dict[str, Any]],
    results: list[dict[str, Any]],
    output_dir: Path,
    save_outputs: bool,
) -> dict[str, Any]:
    collection_items = []
    for index, (payload, result) in enumerate(zip(item_payloads, results)):
        context = payload.get("cvat_context") or {}
        output_prefix = context.get("output_prefix") or f"result_{index + 1:04d}"
        files = extract_result_files(result, output_dir, output_prefix)
        if save_outputs and not files:
            files = [save_result_json(result, output_dir, output_prefix)]

        collection_items.append(
            {
                "index": index,
                "files": files,
                "result": scrub_payload(result),
            }
        )

    return {
        "id": str(run.run_key),
        "run_id": run.id,
        "operation_id": operation.id,
        "operation_name": operation.name,
        "count": len(collection_items),
        "items": collection_items,
    }


def invoke_via_nuclio(function_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    if function_name.startswith("local."):
        from .local_operations import execute_local_operation

        return execute_local_operation(function_name, payload)

    gateway = LambdaGateway()
    return gateway._http(  # pylint: disable=protected-access
        method="post",
        url="/api/function_invocations",
        data=payload,
        headers={"x-nuclio-function-name": function_name, "x-nuclio-path": "/"},
    )


@extend_schema(tags=["custom-operations"])
@extend_schema_view(
    list=extend_schema(operation_id="custom_operation_list", summary="List custom definitions"),
    retrieve=extend_schema(
        operation_id="custom_operation_retrieve",
        summary="Retrieve a custom definition",
    ),
)
class CustomOperationViewSet(viewsets.ModelViewSet):
    queryset = CustomOperationDefinition.objects.all()
    serializer_class = CustomOperationDefinitionSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    iam_supports_organization_params = False
    filter_fields = ["kind", "is_active"]
    search_fields = ["name", "description", "nuclio_function"]
    ordering_fields = ["id", "name", "kind", "created_date", "updated_date"]
    ordering = ["-updated_date", "-id"]
    lookup_value_regex = "[0-9]+"

    def get_queryset(self):
        queryset = super().get_queryset()
        kind = self.request.query_params.get("kind")
        search = self.request.query_params.get("search")
        is_active = self.request.query_params.get("is_active")

        if kind:
            queryset = queryset.filter(kind=kind)
        if search:
            queryset = queryset.filter(
                models.Q(name__icontains=search)
                | models.Q(description__icontains=search)
                | models.Q(nuclio_function__icontains=search)
            )
        if is_active in {"true", "false"}:
            queryset = queryset.filter(is_active=(is_active == "true"))

        return queryset

    def get_permissions(self):
        if self.action == "artifact":
            return [permissions.AllowAny()]
        return super().get_permissions()

    @return_response()
    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return serializer.data

    @return_response()
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return serializer.data

    @return_response(status.HTTP_201_CREATED)
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return self.get_serializer(instance).data

    @return_response()
    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return self.get_serializer(instance).data

    @return_response(status.HTTP_204_NO_CONTENT)
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return None

    @action(detail=True, methods=["get"], url_path="artifact")
    def artifact(self, request, pk=None):
        operation = self.get_object()
        if not operation.artifact:
            raise Http404("Artifact not found")

        token = request.query_params.get("signature")
        if not token:
            raise Http404("Artifact not found")

        signer = TimestampSigner(salt="custom-operation-artifact")
        try:
            decoded = signer.unsign(token, max_age=24 * 60 * 60)
        except BadSignature as exc:
            raise Http404("Artifact not found") from exc

        if decoded != str(operation.artifact_key):
            raise Http404("Artifact not found")

        operation.artifact.open("rb")
        response = FileResponse(operation.artifact, as_attachment=True, filename=operation.artifact.name.rsplit("/", 1)[-1])
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
        operation = self.get_object()
        options = execution_options_from_request(request)
        payload = build_execution_payload(operation, request)
        run = CustomOperationRun.objects.create(
            operation=operation,
            created_by=request.user if request.user and request.user.is_authenticated else None,
            output_path=options["output_path"],
            request_payload=scrub_payload(payload),
        )

        try:
            item_payloads, output_dir = build_iteration_payloads(operation, payload, run, options)
            run.output_path = str(output_dir)
            run.save(update_fields=["output_path", "updated_date"])

            results = [
                invoke_via_nuclio(operation.nuclio_function, item_payload)
                for item_payload in item_payloads
            ]
            output_collection = build_output_collection(
                run,
                operation,
                item_payloads,
                results,
                output_dir,
                options["save_outputs"],
            )
            run.status = CustomOperationRunStatus.SUCCEEDED.value
            run.result = {"items": scrub_payload(results)}
            run.output_collection = output_collection
            run.save(update_fields=["status", "result", "output_collection", "updated_date"])
        except Exception as exc:
            run.status = CustomOperationRunStatus.FAILED.value
            run.error = str(exc)
            run.save(update_fields=["status", "error", "updated_date"])
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
