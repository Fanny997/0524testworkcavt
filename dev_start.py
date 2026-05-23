#!/usr/bin/env python3
"""One-click CVAT development launcher for Windows.

This script starts the Docker-backed infrastructure, applies local Django
migrations, and launches the local backend and frontend dev servers.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable
DEFAULT_NUCTL_PATH = Path(r"C:\tools\nuclio\nuctl.exe")


def format_cmd(cmd: list[str]) -> str:
    return " ".join(cmd)


def normalize_command(cmd: list[str]) -> list[str]:
    if os.name != "nt" or not cmd:
        return cmd

    resolved = shutil.which(cmd[0])
    if resolved and Path(resolved).suffix.lower() in {".cmd", ".bat"}:
        return ["cmd", "/c", *cmd]

    return cmd


def require(command: str) -> None:
    if shutil.which(command) is None:
        raise SystemExit(f"Missing required command: {command}")


def find_nuctl() -> str | None:
    configured = os.environ.get("NUCTL_BIN")
    if configured:
        return configured

    discovered = shutil.which("nuctl")
    if discovered:
        return discovered

    if DEFAULT_NUCTL_PATH.exists():
        return str(DEFAULT_NUCTL_PATH)

    return None


def discover_nuclio_manifests() -> list[Path]:
    registry_dir = ROOT / "custom_operations_registry"
    if not registry_dir.exists():
        return []

    manifests: list[Path] = []
    for operation_dir in sorted(item for item in registry_dir.iterdir() if item.is_dir()):
        manifest_path = operation_dir / "manifest.json"
        if not manifest_path.exists():
            continue

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid custom operation manifest JSON: {manifest_path}: {exc}") from exc

        if manifest.get("nuclio"):
            manifests.append(manifest_path)

    return manifests


def container_is_running(name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def container_has_port_bindings(name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{json .HostConfig.PortBindings}}", name],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return result.stdout.strip() not in ("{}", "null", "")


def missing_services(services: list[str]) -> list[str]:
    return [service for service in services if not container_is_running(service)]


def run_step(title: str, cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    cmd = normalize_command(cmd)
    print(f"\n==> {title}")
    print(f"    {format_cmd(cmd)}")
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)


def spawn_window(title: str, cmd: list[str], *, env: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
    cmd = normalize_command(cmd)
    print(f"\n==> {title}")
    print(f"    {format_cmd(cmd)}")

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_CONSOLE

    return subprocess.Popen(  # noqa: S603,S607
        cmd,
        cwd=ROOT,
        env=env,
        creationflags=creationflags,
    )


def build_backend_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("CVAT_SERVERLESS", "1")
    env.setdefault("ALLOWED_HOSTS", "*")
    env.setdefault("CVAT_POSTGRES_HOST", "localhost")
    env.setdefault("CVAT_REDIS_INMEM_HOST", "localhost")
    env.setdefault("CVAT_REDIS_ONDISK_HOST", "localhost")
    env.setdefault("CVAT_POSTGRES_PORT", "5432")
    env.setdefault("CVAT_REDIS_INMEM_PORT", "6379")
    env.setdefault("CVAT_REDIS_ONDISK_PORT", "6666")
    env.setdefault("CVAT_ANALYTICS", "1")
    env.setdefault("DJANGO_LOG_SERVER_HOST", "localhost")
    env.setdefault("DJANGO_LOG_SERVER_PORT", "8282")

    return env


def build_nuclio_env() -> dict[str, str]:
    env = build_backend_env()
    if os.name == "nt":
        # Git for Windows/MSYS may otherwise rewrite Nuclio's Linux paths like
        # /bin/sh into C:/Program Files/Git/usr/bin/sh before Docker sees them.
        env.setdefault("MSYS_NO_PATHCONV", "1")
        env.setdefault("MSYS2_ARG_CONV_EXCL", "*")
        # nuctl builds functions in the system temp directory and then passes
        # that path through a shell before Docker sees it. Backslash paths such
        # as C:\Users\... can be mangled into C:Users..., so force a short
        # forward-slash temp path.
        nuclio_tmp = Path(r"C:\tmp\nuclio")
        nuclio_tmp.mkdir(parents=True, exist_ok=True)
        env["TMP"] = "C:/tmp/nuclio"
        env["TEMP"] = "C:/tmp/nuclio"
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the CVAT development stack.")
    parser.add_argument(
        "--skip-ui-install",
        action="store_true",
        help="Skip `yarn --immutable` before starting the frontend.",
    )
    parser.add_argument(
        "--skip-custom-operations-deploy",
        action="store_true",
        help="Skip Nuclio deployment and database sync for custom_operations_registry.",
    )
    parser.add_argument(
        "--sync-custom-operations-only",
        action="store_true",
        help="Only sync custom operation manifests into the database without deploying Nuclio functions.",
    )
    parser.add_argument(
        "--nuctl",
        default=None,
        help=r"Path to nuctl. Defaults to NUCTL_BIN, PATH, or C:\tools\nuclio\nuctl.exe.",
    )
    args = parser.parse_args()

    require("docker")
    require("corepack")

    docker_services = [
        "cvat_db",
        "cvat_redis_inmem",
        "cvat_redis_ondisk",
        "cvat_opa",
    ]
    docker_services.extend(["cvat_clickhouse", "cvat_vector"])

    to_start = missing_services(docker_services)
    need_recreate = to_start == []
    if not need_recreate:
        # Some services may already be running but without host port bindings
        # because they were started from the base compose file. In that case the
        # local backend cannot reach them, so recreate the stack with dev ports.
        need_recreate = any(not container_has_port_bindings(service) for service in docker_services)

    if to_start or need_recreate:
        services_to_start = docker_services if need_recreate else to_start
        compose_cmd = [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.dev.yml",
            "up",
            "-d",
            "--force-recreate",
            "--no-build",
            "--pull",
            "never",
            *services_to_start,
        ]
        run_step("Start or recreate Docker services", compose_cmd)
    else:
        print("\n==> Docker infrastructure already running; skipping compose startup.")

    if not container_is_running("cvat_server") or container_has_port_bindings("cvat_server"):
        run_step(
            "Start auxiliary CVAT server without host port binding",
            [
                "docker",
                "compose",
                "-f",
                "docker-compose.yml",
                "up",
                "-d",
                "--no-deps",
                "--force-recreate",
                "--no-build",
                "--pull",
                "never",
                "cvat_server",
            ],
        )
    else:
        print("\n==> Auxiliary cvat_server already running without host ports; skipping.")

    run_step("Apply Django migrations", [PYTHON, "manage.py", "migrate"])
    run_step("Apply Redis migrations", [PYTHON, "manage.py", "migrateredis"])
    run_step("Sync periodic jobs", [PYTHON, "manage.py", "syncperiodicjobs"])
    run_step("Collect static assets", [PYTHON, "manage.py", "collectstatic", "--noinput"])

    nuclio_manifests = discover_nuclio_manifests()
    if args.skip_custom_operations_deploy:
        print("\n==> Custom operation Nuclio deployment skipped by argument.")
    elif args.sync_custom_operations_only:
        run_step("Sync custom operation registry", [PYTHON, "manage.py", "synccustomoperations"])
    elif nuclio_manifests:
        nuctl = args.nuctl or find_nuctl()
        if not nuctl:
            raise SystemExit(
                "Found custom operation Nuclio manifests, but nuctl is not available. "
                r"Install it or rerun with --nuctl C:\tools\nuclio\nuctl.exe."
            )

        run_step(
            "Start Nuclio dashboard",
            [
                "docker",
                "compose",
                "-f",
                "docker-compose.yml",
                "-f",
                "components/serverless/docker-compose.serverless.yml",
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                "nuclio",
            ],
        )
        run_step(
            "Deploy and sync custom Nuclio operations",
            [PYTHON, "manage.py", "deploycustomoperations", "--nuctl", nuctl],
            env=build_nuclio_env(),
        )
    else:
        run_step("Sync custom operation registry", [PYTHON, "manage.py", "synccustomoperations"])

    run_step("Enable Yarn via Corepack", ["corepack", "enable", "yarn"])
    if not args.skip_ui_install:
        run_step("Install frontend dependencies", ["yarn", "--immutable"])

    backend_env = build_backend_env()

    backend_proc = spawn_window(
        "Launch local Django backend on 127.0.0.1:7000",
        [PYTHON, "manage.py", "runserver", "--noreload", "--insecure", "127.0.0.1:7000"],
        env=backend_env,
    )

    ui_proc = spawn_window(
        "Launch frontend dev server on 127.0.0.1:3000",
        ["yarn", "run", "start:cvat-ui"],
    )

    print("\nDevelopment stack is starting.")
    print("Backend: http://localhost:7000")
    print("Frontend: http://localhost:3000")
    print("\nKeep the spawned windows open. Close them to stop backend/UI.")
    print(f"Backend PID: {backend_proc.pid}")
    print(f"Frontend PID: {ui_proc.pid}")
    print("\nCommand map:")
    print("  docker compose ... cvat_db / cvat_redis_inmem / cvat_redis_ondisk / cvat_opa")
    print("    -> PostgreSQL, in-memory Redis, on-disk Kvrocks, and OPA.")
    print("  docker compose ... cvat_server (base compose only)")
    print("    -> Auxiliary CVAT server for OPA rules, without exposing host port 9090.")
    print("  docker compose ... cvat_clickhouse / cvat_vector")
    print("    -> Analytics event store and Django log pipeline.")
    print("  python manage.py migrate")
    print("    -> Django database migrations.")
    print("  python manage.py migrateredis")
    print("    -> Redis schema/state migrations used by CVAT.")
    print("  python manage.py syncperiodicjobs")
    print("    -> Registers periodic RQ jobs.")
    print("  python manage.py collectstatic --noinput")
    print("    -> Collects static files for the Django backend.")
    print("  python manage.py deploycustomoperations")
    print("    -> Scans custom_operations_registry, deploys Nuclio functions, and syncs Workflows.")
    print("  yarn --immutable")
    print("    -> Ensures frontend dependencies are installed.")
    print("  python manage.py runserver --noreload --insecure 127.0.0.1:7000")
    print("    -> Local Django backend.")
    print("  yarn run start:cvat-ui")
    print("    -> Frontend webpack dev server.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
