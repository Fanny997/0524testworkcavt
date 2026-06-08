from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from cvat.apps.custom_operations.registry_loader import iter_registry_manifests


class Command(BaseCommand):
    """部署registry manifest中声明的Nuclio函数。
    负责把 custom_operations_registry 中声明的Nuclio 函数发布到 Nuclio 平台，并在部署后同步数据库。

    1. registry_loader.iter_registry_manifests() 找到所有 manifest.json。
    2. 对包含 "nuclio" 配置的 manifest 拼接 nuctl deploy 命令。
    3. 执行 nuctl，把函数部署到 Nuclio。
    4. 调用 synccustomoperations，把同一份 manifest 写入数据库。
    """

    help = "Deploy Nuclio functions declared by custom operation manifests."

    def add_arguments(self, parser):
        """声明命令行参数。

        Django 在执行 handle() 前会解析这些参数，并以 options 字典形式传入。
        参数作用：
        - --only：只部署指定 nuclio_function 的 manifest。
        - --dry-run：只打印 nuctl 命令，不实际部署。
        - --no-sync：部署完成后不执行数据库同步。
        - --nuctl：指定 nuctl 可执行文件路径。
        """

        parser.add_argument(
            "--only",
            dest="only",
            default="",
            help="Deploy only the manifest with this nuclio_function value.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print nuctl commands without executing them.",
        )
        parser.add_argument(
            "--no-sync",
            action="store_true",
            help="Do not run synccustomoperations after deployment.",
        )
        parser.add_argument(
            "--nuctl",
            default=os.environ.get("NUCTL_BIN", "nuctl"),
            help="Path to the nuctl executable. Defaults to NUCTL_BIN or nuctl.",
        )

    @staticmethod
    def _resolve_path(manifest_path: Path, value: str | None, default: str) -> Path:
        """解析 manifest 中的文件路径。

        manifest.json 通常位于 custom_operations_registry/<operation>/manifest.json。
        其中的 nuclio.path 和 nuclio.file 多数使用相对路径，例如：

            "path": "nuclio"
            "file": "nuclio/function.yaml"

        相对路径需要以 manifest.json 所在目录为基准解析。绝对路径则保持不变。
        """

        raw_path = Path(value or default)
        if raw_path.is_absolute():
            return raw_path
        return (manifest_path.parent / raw_path).resolve()

    @staticmethod
    def _build_platform_config(nuclio_config: dict[str, Any]) -> str | None:
        """构造 nuctl 的 --platform-config 参数。

        Nuclio 在 local 平台运行时，本质上会创建 Docker 容器。函数容器需要与
        CVAT 后端处于同一网络，才能访问 CVAT API。manifest 可以直接提供完整的
        platform_config，也可以提供简化字段 network。nuctl 命令需要 JSON 字符串，
        因此该函数负责把 Python dict 序列化为 JSON。
        """

        if "platform_config" in nuclio_config:
            return json.dumps(nuclio_config["platform_config"])

        network = nuclio_config.get("network")
        if network:
            return json.dumps({"attributes": {"network": network}})

        return None

    @staticmethod
    def _build_nuctl_env() -> dict[str, str]:
        """构造运行 nuctl 子进程时使用的环境变量。

        Linux 环境通常直接继承 os.environ 即可。Windows 环境需要额外处理路径：
        - MSYS_NO_PATHCONV 和 MSYS2_ARG_CONV_EXCL 用于避免 Git Bash/MSYS 自动改写路径。
        - TMP/TEMP 固定到 C:/tmp/nuclio，避免 Nuclio 使用不兼容的临时目录路径。
        - tools/nuclio-windows 加入 PATH 后，nuctl 调用 docker 时可使用项目提供的包装脚本。
        """

        env = os.environ.copy()
        if os.name == "nt":
            env.setdefault("MSYS_NO_PATHCONV", "1")
            env.setdefault("MSYS2_ARG_CONV_EXCL", "*")
            nuclio_tmp = Path(r"C:\tmp\nuclio")
            nuclio_tmp.mkdir(parents=True, exist_ok=True)
            env["TMP"] = "C:/tmp/nuclio"
            env["TEMP"] = "C:/tmp/nuclio"
            docker_wrapper_dir = Path(settings.BASE_DIR) / "tools" / "nuclio-windows"
            if docker_wrapper_dir.exists():
                env["PATH"] = str(docker_wrapper_dir) + os.pathsep + env.get("PATH", "")
        return env

    def _run(self, cmd: list[str], dry_run: bool, check: bool = True) -> subprocess.CompletedProcess | None:
        """执行一条外部命令。

        cmd 使用 list[str] 而不是字符串，避免 shell 拼接带来的转义问题。
        dry_run=True 时仅输出命令文本，不启动子进程。
        check=True 时，非 0 退出码会转换为 CommandError，Django 会将其视为命令失败。
        """

        printable = " ".join(cmd)
        if dry_run:
            self.stdout.write(printable)
            return None

        self.stdout.write(printable)
        result = subprocess.run(cmd, check=False, env=self._build_nuctl_env())
        if check and result.returncode:
            raise CommandError(f"Command failed with exit code {result.returncode}: {printable}")
        return result

    def _deploy_manifest(self, manifest_path: Path, manifest: dict[str, Any], options) -> bool:
        """部署单个 manifest 对应的 Nuclio 函数。

        返回 True 表示该 manifest 含有 nuclio 配置并已进入部署流程。
        返回 False 表示该 manifest 不声明 Nuclio 部署信息，例如只用于数据库同步。

        部署前会校验两个名称必须一致：
        - manifest["nuclio_function"]
        - function.yaml 中的 metadata.name

        CVAT 后端执行时使用 nuclio_function 作为调用名。如果两个名称不一致，
        部署可能成功，但执行阶段会找不到函数。
        """

        nuclio_config = manifest.get("nuclio")
        if not nuclio_config:
            return False

        function_name = manifest["nuclio_function"]
        function_path = self._resolve_path(manifest_path, nuclio_config.get("path"), "nuclio")
        function_file = self._resolve_path(
            manifest_path,
            nuclio_config.get("file"),
            str(function_path / "function.yaml"),
        )

        if not function_path.exists():
            raise CommandError(f"{manifest_path}: Nuclio path does not exist: {function_path}")
        if not function_file.exists():
            raise CommandError(f"{manifest_path}: Nuclio function file does not exist: {function_file}")

        with function_file.open("r", encoding="utf-8") as stream:
            function_config = yaml.safe_load(stream) or {}
        declared_name = (function_config.get("metadata") or {}).get("name")
        if declared_name != function_name:
            raise CommandError(
                f"{manifest_path}: nuclio_function '{function_name}' must match "
                f"{function_file} metadata.name '{declared_name}'"
            )

        nuctl = options["nuctl"]
        platform = nuclio_config.get("platform", "local")
        project_name = nuclio_config.get("project_name", "cvat")

        # Nuclio 函数归属于 project。create_project 默认开启，且 check=False，
        # 因此 project 已存在时不会中断后续部署。
        if nuclio_config.get("create_project", True):
            self._run(
                [nuctl, "create", "project", project_name, "--platform", platform],
                dry_run=options["dry_run"],
                check=False,
            )

        cmd = [
            nuctl,
            "deploy",
            "--project-name",
            project_name,
            "--path",
            str(function_path),
            "--file",
            str(function_file),
            "--platform",
            platform,
        ]

        # 默认使用本地已有镜像或本地构建。
        if nuclio_config.get("offline", True):
            cmd.append("--offline")
        if nuclio_config.get("no_pull", True):
            cmd.append("--no-pull")

        platform_config = self._build_platform_config(nuclio_config)
        if platform_config:
            cmd.extend(["--platform-config", platform_config])

        for key, value in sorted((nuclio_config.get("env") or {}).items()):
            cmd.extend(["--env", f"{key}={value}"])

        cmd.extend(str(item) for item in nuclio_config.get("deploy_args", []))

        self.stdout.write(self.style.NOTICE(f"Deploying {function_name} from {manifest_path}"))
        self._run(cmd, dry_run=options["dry_run"])
        return True

    def handle(self, *args, **options):
        """命令执行入口。

        该方法扫描所有 registry manifest，并部署其中声明 nuclio 配置的条目。
        --only 参数存在时，只处理指定函数名。部署完成后默认调用
        synccustomoperations，使数据库定义与文件注册目录保持一致。
        """

        deployed = []
        only = options["only"]

        for manifest_path, manifest in iter_registry_manifests():
            if only and manifest.get("nuclio_function") != only:
                continue

            if self._deploy_manifest(manifest_path, manifest, options):
                deployed.append(manifest["nuclio_function"])

        if not deployed:
            self.stdout.write("No manifests with a nuclio deployment section were found.")
            return

        self.stdout.write(self.style.SUCCESS("Deployed Nuclio functions: " + ", ".join(deployed)))

        # 部署 Nuclio 只使函数可被调用；同步数据库才使函数出现在 Workflows 页面中。
        # dry-run 不执行真实部署，因此也不进行同步。
        if not options["no_sync"] and not options["dry_run"]:
            call_command("synccustomoperations")
