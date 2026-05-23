from __future__ import annotations

import importlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable

from django.apps import AppConfig
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import OperationalError, ProgrammingError
from django.db.models.signals import post_migrate

from .models import CustomOperationDefinition
from .serializers import CustomOperationDefinitionSerializer

LOGGER = logging.getLogger(__name__)

DEFAULT_REGISTRY_DIR = Path(settings.BASE_DIR) / "custom_operations_registry"
SKIP_AUTOSYNC_COMMANDS = {
    "check",
    "collectstatic",
    "makemigrations",
    "shell",
    "synccustomoperations",
    "deploycustomoperations",
    "test",
}
_registry_handlers_loaded = False
_registry_synced = False


def get_registry_dirs() -> list[Path]:
    configured = getattr(settings, "CUSTOM_OPERATIONS_REGISTRY_DIRS", None)
    if configured is None:
        configured = os.environ.get("CVAT_CUSTOM_OPERATIONS_REGISTRY_DIRS")

    if configured:
        if isinstance(configured, str):
            raw_dirs = [item.strip() for item in configured.split(os.pathsep) if item.strip()]
        else:
            raw_dirs = list(configured)
        return [Path(item).expanduser() for item in raw_dirs]

    return [DEFAULT_REGISTRY_DIR]


def import_dotted_path(dotted_path: str) -> Callable[..., Any]:
    module_name, separator, attr_name = dotted_path.partition(":")
    if not separator:
        module_name, _, attr_name = dotted_path.rpartition(".")

    if not module_name or not attr_name:
        raise ValueError(f'Invalid dotted path "{dotted_path}"')

    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


def iter_registry_manifests() -> list[tuple[Path, dict[str, Any]]]:
    manifests: list[tuple[Path, dict[str, Any]]] = []
    for registry_dir in get_registry_dirs():
        if not registry_dir.exists():
            continue

        for operation_dir in sorted(item for item in registry_dir.iterdir() if item.is_dir()):
            path = operation_dir / "manifest.json"
            if not path.exists():
                continue

            with path.open("r", encoding="utf-8") as manifest_file:
                manifest = json.load(manifest_file)
            manifest.setdefault("name", operation_dir.name)
            manifest.setdefault("nuclio_function", operation_dir.name)
            manifests.append((path, manifest))

    return manifests


def load_registry_handlers() -> None:
    global _registry_handlers_loaded

    if _registry_handlers_loaded:
        return

    from .local_operations import register_local_operation

    for path, manifest in iter_registry_manifests():
        handler_path = manifest.get("handler")
        function_name = manifest.get("nuclio_function")
        if not handler_path:
            continue

        if not function_name or not str(function_name).startswith("local."):
            raise ValueError(f"{path} defines handler but does not define a local nuclio_function")

        register_local_operation(function_name, import_dotted_path(handler_path))

    _registry_handlers_loaded = True


def build_artifact_content(manifest_path: Path, manifest: dict[str, Any]) -> tuple[str, bytes] | None:
    artifact = manifest.get("artifact")
    artifact_builder = manifest.get("artifact_builder")

    if artifact and artifact_builder:
        raise ValueError(f"{manifest_path} cannot define both artifact and artifact_builder")

    if artifact_builder:
        builder = import_dotted_path(artifact_builder)
        artifact_bytes = builder()
        artifact_name = manifest.get("artifact_name") or f"{manifest_path.stem}.bin"
        return artifact_name, artifact_bytes

    if artifact:
        artifact_path = Path(artifact)
        if not artifact_path.is_absolute():
            artifact_path = manifest_path.parent / artifact_path
        return artifact_path.name, artifact_path.read_bytes()

    return None


def manifest_to_definition_data(manifest: dict[str, Any]) -> dict[str, Any]:
    function_name = manifest["nuclio_function"]
    if str(function_name).startswith("local.") or manifest.get("handler"):
        raise ValueError(
            "File-based custom operation registry is Nuclio-only. "
            f"Remove handler/local function from {function_name}."
        )

    return {
        "name": manifest["name"],
        "kind": manifest["kind"],
        "description": manifest.get("description", ""),
        "nuclio_function": manifest["nuclio_function"],
        "input_schema": manifest.get("input_schema", []),
        "output_schema": manifest.get("output_schema", {}),
        "is_active": manifest.get("is_active", True),
    }


def sync_custom_operations_from_registry() -> list[CustomOperationDefinition]:
    global _registry_synced

    load_registry_handlers()

    synced: list[CustomOperationDefinition] = []
    for path, manifest in iter_registry_manifests():
        data = manifest_to_definition_data(manifest)
        artifact_content = build_artifact_content(path, manifest)
        existing = CustomOperationDefinition.objects.filter(
            nuclio_function=data["nuclio_function"],
        ).first()

        artifact_data = {}
        if artifact_content:
            should_update_artifact = True
            if existing and existing.artifact:
                existing_name = Path(existing.artifact.name).name
                if existing_name == artifact_content[0]:
                    existing.artifact.open("rb")
                    try:
                        should_update_artifact = existing.artifact.read() != artifact_content[1]
                    finally:
                        existing.artifact.close()

            if should_update_artifact:
                artifact_data["artifact"] = ContentFile(artifact_content[1], name=artifact_content[0])

        serializer = CustomOperationDefinitionSerializer(
            instance=existing,
            data={
                **data,
                **artifact_data,
            },
            partial=existing is not None,
            context={"allow_model_without_artifact": True},
        )
        serializer.is_valid(raise_exception=True)
        operation = serializer.save()
        synced.append(operation)
        LOGGER.info("Synced custom operation %s from %s", operation.nuclio_function, path)

    _registry_synced = True
    return synced


def should_sync_during_ready() -> bool:
    if os.environ.get("CVAT_CUSTOM_OPERATIONS_AUTOSYNC", "1").lower() in {"0", "false", "no"}:
        return False

    command = Path(sys.argv[1]).name if len(sys.argv) > 1 else ""
    return command not in SKIP_AUTOSYNC_COMMANDS


def sync_registry_safely(*args, **kwargs) -> None:
    del args, kwargs

    global _registry_synced
    if _registry_synced:
        return

    try:
        sync_custom_operations_from_registry()
    except (OperationalError, ProgrammingError) as exc:
        LOGGER.debug("Custom operation registry sync skipped because database is not ready: %s", exc)
    except Exception:
        LOGGER.exception("Could not sync custom operation registry")


def setup_custom_operation_registry(app_config: AppConfig) -> None:
    post_migrate.connect(
        sync_registry_safely,
        sender=app_config,
        dispatch_uid="custom_operations.sync_registry",
    )

    try:
        load_registry_handlers()
    except Exception:
        LOGGER.exception("Could not load custom operation handlers")

    if should_sync_during_ready():
        sync_registry_safely()
