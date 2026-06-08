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

# 因此默认扫描位置是：<项目根目录>/custom_operations_registry。
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

# 这两个变量用于避免同一进程内重复加载 handler 和重复同步 registry。
_registry_handlers_loaded = False
_registry_synced = False


def get_registry_dirs() -> list[Path]:
    """返回需要扫描的自定义操作注册目录。

    返回值是 Path 列表。后续代码只需要遍历这些目录并寻找manifest.json。
    目录来源优先级：
    1. Django settings.CUSTOM_OPERATIONS_REGISTRY_DIRS。
    2. 环境变量 CVAT_CUSTOM_OPERATIONS_REGISTRY_DIRS。
    3. 默认目录 DEFAULT_REGISTRY_DIR。
    """

    #读取 obj.name；不存在时返回 default。
    configured = getattr(settings, "CUSTOM_OPERATIONS_REGISTRY_DIRS", None)

    # settings 未配置时，再从环境变量读取。部署时常用环境变量覆盖路径。
    if configured is None:
        configured = os.environ.get("CVAT_CUSTOM_OPERATIONS_REGISTRY_DIRS")

    if configured:
        if isinstance(configured, str):
            # 一个字符串可以包含多个目录。os.pathsep 是系统路径分隔符：
            # Windows 通常是 ";"，Linux/macOS 通常是 ":"。
            raw_dirs = [item.strip() for item in configured.split(os.pathsep) if item.strip()]
        else:
            # 如果 settings 里直接配置了 list/tuple，则转换成 list 继续处理。
            raw_dirs = list(configured)

        # Path(...).expanduser() 允许路径中出现 "~" 这种用户目录缩写。
        return [Path(item).expanduser() for item in raw_dirs]

    # 没有任何配置时使用默认目录。
    return [DEFAULT_REGISTRY_DIR]


def import_dotted_path(dotted_path: str) -> Callable[..., Any]:
    """根据字符串形式的 Python 路径导入函数或对象。

    manifest 中无法直接保存 Python 函数对象，只能保存字符串路径。例如：
        "cvat.apps.custom_operations.demo_assets:build_demo_detector_onnx"

    该函数把字符串拆成模块名和属性名，再导入模块并取出属性。
    """

    # 优先支持 "module.submodule:function" 写法。
    module_name, separator, attr_name = dotted_path.partition(":")

    if not separator:
        # 没有冒号时，兼容 "module.submodule.function" 写法。
        # rpartition 从最右边的 "." 分割，左侧是模块名，右侧是属性名。
        module_name, _, attr_name = dotted_path.rpartition(".")

    if not module_name or not attr_name:
        # 模块名或属性名为空时，无法定位 Python 对象。
        raise ValueError(f'Invalid dotted path "{dotted_path}"')

    # 动态导入模块，效果类似 import module_name。
    module = importlib.import_module(module_name)

    # 从模块中取出函数、类或变量。
    return getattr(module, attr_name)


def iter_registry_manifests() -> list[tuple[Path, dict[str, Any]]]:
    """扫描所有 registry 目录，读取其中的 manifest.json。

    每个操作目录的结构通常是：
        custom_operations_registry/<operation-name>/manifest.json

    返回列表中的每一项都是：
        (manifest 文件路径, manifest JSON 解析后的 dict)
    """

    # 用列表保存扫描结果。类型标注说明每项是 (Path, dict)。
    manifests: list[tuple[Path, dict[str, Any]]] = []

    # get_registry_dirs() 可能返回一个或多个 registry 目录。
    for registry_dir in get_registry_dirs():
        if not registry_dir.exists():
            # 配置的目录不存在时跳过，不中断整个扫描流程。
            continue

        # registry 目录下的一级子目录代表一个操作。sorted 使扫描顺序稳定。
        for operation_dir in sorted(item for item in registry_dir.iterdir() if item.is_dir()):
            path = operation_dir / "manifest.json"
            if not path.exists():
                # 没有 manifest.json 的目录不是有效操作目录。
                continue

            # json.load 将 manifest.json 转换为 Python dict。
            with path.open("r", encoding="utf-8") as manifest_file:
                manifest = json.load(manifest_file)

            # setdefault 只在 key 不存在时写入默认值。
            # name 不存在时使用目录名作为操作显示名称。
            manifest.setdefault("name", operation_dir.name)

            # nuclio_function 不存在时使用目录名作为 Nuclio 函数名。
            manifest.setdefault("nuclio_function", operation_dir.name)

            manifests.append((path, manifest))

    return manifests


def load_registry_handlers() -> None:
    """加载 registry 中声明的本地 handler。

    local.* 操作不经过 Nuclio，而是直接调用 Python 函数。manifest 可通过 handler 字段
    指向该 Python 函数。加载后，handler 会注册到 local_operations.py 的本地操作表中。
    """

    global _registry_handlers_loaded

    if _registry_handlers_loaded:
        # 同一进程内已经加载过，本次无需重复处理。
        return

    # 延迟导入可以减少模块循环引用风险。
    from .local_operations import register_local_operation

    for path, manifest in iter_registry_manifests():
        # handler 是 Python 函数字符串路径；没有 handler 表示不是本地操作。
        handler_path = manifest.get("handler")

        # function_name 是执行时用于查找本地 handler 的名字。
        function_name = manifest.get("nuclio_function")

        if not handler_path:
            continue

        if not function_name or not str(function_name).startswith("local."):
            # handler 只允许和 local.* 函数名配合使用。这样可以清楚地区分：
            # local.* 是本地 Python 函数，其他名称是 Nuclio 函数。
            raise ValueError(f"{path} defines handler but does not define a local nuclio_function")

        # import_dotted_path 将字符串路径导入为 Python 函数对象。
        # register_local_operation 将函数对象放入本地操作注册表。
        register_local_operation(function_name, import_dotted_path(handler_path))

    # 标记加载完成，避免重复注册。
    _registry_handlers_loaded = True


def build_artifact_content(manifest_path: Path, manifest: dict[str, Any]) -> tuple[str, bytes] | None:
    """解析 manifest 中声明的 artifact 文件内容。

    artifact 是 CustomOperationDefinition.artifact 对应的文件。它可以来自两种方式：
    - artifact：manifest 中直接写文件路径。
    - artifact_builder：manifest 中写 Python 函数路径，由函数返回 bytes。

    返回值为 (文件名, 文件内容 bytes)。没有 artifact 配置时返回 None。
    """

    artifact = manifest.get("artifact")
    artifact_builder = manifest.get("artifact_builder")

    if artifact and artifact_builder:
        # 两种来源同时存在时无法判断以哪个为准，因此直接报错。
        raise ValueError(f"{manifest_path} cannot define both artifact and artifact_builder")

    if artifact_builder:
        # 导入生成 artifact 的 Python 函数。
        builder = import_dotted_path(artifact_builder)

        # builder() 应返回 bytes。
        artifact_bytes = builder()

        # manifest 可显式指定 artifact_name。没有时使用 manifest 文件名加 .bin。
        artifact_name = manifest.get("artifact_name") or f"{manifest_path.stem}.bin"
        return artifact_name, artifact_bytes

    if artifact:
        artifact_path = Path(artifact)
        if not artifact_path.is_absolute():
            # 相对路径以 manifest.json 所在目录为基准。
            artifact_path = manifest_path.parent / artifact_path

        # read_bytes 读取整个文件内容。后续会包装成 ContentFile 存入 FileField。
        return artifact_path.name, artifact_path.read_bytes()

    return None


def manifest_to_definition_data(manifest: dict[str, Any]) -> dict[str, Any]:
    """把 manifest 转换成数据库模型可保存的数据。

    manifest 中可能包含部署信息、artifact 信息等字段；数据库定义只需要其中一部分：
    name、kind、description、nuclio_function、input_schema、output_schema、is_active。
    """

    function_name = manifest["nuclio_function"]

    if str(function_name).startswith("local.") or manifest.get("handler"):
        # 文件型 registry 在当前设计中只用于 Nuclio 操作。local.* handler 属于本地函数
        # 机制，不在这里写入数据库定义。
        raise ValueError(
            "File-based custom operation registry is Nuclio-only. "
            f"Remove handler/local function from {function_name}."
        )

    # dict 的 key 对应 CustomOperationDefinitionSerializer 支持的输入字段。
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
    """同步 registry manifest 到 CustomOperationDefinition 数据库表。

    同步含义：
    - manifest 对应的 nuclio_function 已存在时，更新现有数据库记录。
    - manifest 对应的 nuclio_function 不存在时，创建新数据库记录。

    返回值是本次同步保存成功的模型对象列表。
    """

    global _registry_synced

    # 先加载本地 handler 注册表。虽然文件型 registry 主要用于 Nuclio，
    # 但该同步入口也承担初始化 local handler 的职责。
    load_registry_handlers()

    synced: list[CustomOperationDefinition] = []

    for path, manifest in iter_registry_manifests():
        # 将 manifest 转成 serializer 可接收的普通字段。
        data = manifest_to_definition_data(manifest)

        # 读取或生成 artifact 文件内容。
        artifact_content = build_artifact_content(path, manifest)

        # nuclio_function 被当作业务唯一标识。first() 返回第一条记录或 None。
        existing = CustomOperationDefinition.objects.filter(
            nuclio_function=data["nuclio_function"],
        ).first()

        artifact_data = {}

        if artifact_content:
            should_update_artifact = True

            if existing and existing.artifact:
                # 已有 artifact 时，比较文件名和文件内容。内容未变化时不重复写文件。
                existing_name = Path(existing.artifact.name).name
                if existing_name == artifact_content[0]:
                    existing.artifact.open("rb")
                    try:
                        should_update_artifact = existing.artifact.read() != artifact_content[1]
                    finally:
                        existing.artifact.close()

            if should_update_artifact:
                # ContentFile 把内存中的 bytes 包装成 Django 文件对象，
                # ModelSerializer 保存时会将其写入 FileField 对应的 storage。
                artifact_data["artifact"] = ContentFile(artifact_content[1], name=artifact_content[0])

        # instance=existing 表示更新现有对象；existing 为 None 时表示创建新对象。
        # partial=True 表示更新时允许只传部分字段。
        serializer = CustomOperationDefinitionSerializer(
            instance=existing,
            data={
                **data,
                **artifact_data,
            },
            partial=existing is not None,
            # 文件 registry 中的模型定义可不带 artifact，因为真实模型也可能打包在
            # Nuclio 函数镜像或函数目录中。
            context={"allow_model_without_artifact": True},
        )

        # 执行 serializers.py 中定义的字段校验。
        serializer.is_valid(raise_exception=True)

        # save() 是 DRF ModelSerializer 的保存入口。
        # existing 为 None 时内部执行 create；否则执行 update；最终通过 Django ORM 写库。
        operation = serializer.save()

        synced.append(operation)
        LOGGER.info("Synced custom operation %s from %s", operation.nuclio_function, path)

    _registry_synced = True
    return synced


def should_sync_during_ready() -> bool:
    """判断 Django app ready() 阶段是否自动同步 registry。

    返回 True 表示 ready() 阶段可以调用 sync_registry_safely()。
    返回 False 表示当前命令不适合自动写数据库。
    """

    # 环境变量可关闭自动同步。lower() 统一大小写。
    if os.environ.get("CVAT_CUSTOM_OPERATIONS_AUTOSYNC", "1").lower() in {"0", "false", "no"}:
        return False

    # sys.argv[1] 通常是 manage.py 子命令名，例如 runserver、migrate、test。
    command = Path(sys.argv[1]).name if len(sys.argv) > 1 else ""

    # 不在跳过列表中的命令才允许自动同步。
    return command not in SKIP_AUTOSYNC_COMMANDS


def sync_registry_safely(*args, **kwargs) -> None:
    """带异常保护的 registry 同步入口。

    该函数既可直接调用，也可作为 Django signal 回调。signal 回调会传入额外参数，
    当前逻辑不需要这些参数。
    """

    # 明确丢弃未使用的 signal 参数。
    del args, kwargs

    global _registry_synced

    if _registry_synced:
        # 同一进程内已经同步过时直接返回。
        return

    try:
        sync_custom_operations_from_registry()
    except (OperationalError, ProgrammingError) as exc:
        # 数据库表尚未创建或连接尚未就绪时，ORM 可能抛出这些异常。
        # 启动阶段记录 debug 日志即可，不阻断 Django 进程。
        LOGGER.debug("Custom operation registry sync skipped because database is not ready: %s", exc)
    except Exception:
        # 其他异常通常说明 manifest、artifact 或 serializer 配置存在问题。
        LOGGER.exception("Could not sync custom operation registry")


def setup_custom_operation_registry(app_config: AppConfig) -> None:
    """在 Django app 启动时接入 registry 自动同步机制。

    apps.py 中的 CustomOperationsConfig.ready() 会调用该函数。
    """

    # post_migrate 是 Django 的迁移完成信号。绑定后，数据库迁移结束会自动同步 registry。
    post_migrate.connect(
        sync_registry_safely,
        sender=app_config,
        # dispatch_uid 防止同一个回调被重复注册。
        dispatch_uid="custom_operations.sync_registry",
    )

    try:
        # 启动阶段先尝试加载本地 handler，使 local.* 操作可以被执行。
        load_registry_handlers()
    except Exception:
        LOGGER.exception("Could not load custom operation handlers")

    if should_sync_during_ready():
        # 当前命令允许自动同步时，启动阶段立即执行一次安全同步。
        sync_registry_safely()
