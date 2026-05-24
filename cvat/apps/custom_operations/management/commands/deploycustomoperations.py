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
    help = "Deploy Nuclio functions declared by custom operation manifests."

    def add_arguments(self, parser):
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
        raw_path = Path(value or default)
        if raw_path.is_absolute():
            return raw_path
        return (manifest_path.parent / raw_path).resolve()

    @staticmethod
    def _build_platform_config(nuclio_config: dict[str, Any]) -> str | None:
        if "platform_config" in nuclio_config:
            return json.dumps(nuclio_config["platform_config"])

        network = nuclio_config.get("network")
        if network:
            return json.dumps({"attributes": {"network": network}})

        return None

    @staticmethod
    def _build_nuctl_env() -> dict[str, str]:
        env = os.environ.copy()
        if os.name == "nt":
            env.setdefault("MSYS_NO_PATHCONV", "1")
            env.setdefault("MSYS2_ARG_CONV_EXCL", "*")
            nuclio_tmp = Path(r"C:\tmp\nuclio")
            nuclio_tmp.mkdir(parents=True, exist_ok=True)
            env["TMP"] = "C:/tmp/nuclio"
            env["TEMP"] = "C:/tmp/nuclio"
            docker_wrapper_dir = Path(settings.BASE_DIR) / "tools" / "nuclio-windows-bak"
            if docker_wrapper_dir.exists():
                env["PATH"] = str(docker_wrapper_dir) + os.pathsep + env.get("PATH", "")
        return env

    def _run(self, cmd: list[str], dry_run: bool, check: bool = True) -> subprocess.CompletedProcess | None:
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

        if nuclio_config.get("create_project", True):
            self._run(
                [nuctl, "create",   "project", project_name, "--platform", platform],
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

        if not options["no_sync"] and not options["dry_run"]:
            call_command("synccustomoperations")
