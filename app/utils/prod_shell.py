#!/usr/bin/env python3
"""
Production Shell Utility

Creates an SSH tunnel to the production database and launches an IPython shell
with the production environment variables set.

Usage:
    python -m app.utils.prod_shell
"""

import atexit
import json
import os
import subprocess
import sys
import time


class ProdShell:
    def __init__(
        self,
        ssh_host: str = "flow-server",
        ssh_user: str = "ec2-user",
        docker_service: str = "floww-backend_app",
        local_port: int = 5433,
    ):
        self.ssh_host = os.environ.get("SSH_HOST", ssh_host)
        self.ssh_user = os.environ.get("SSH_USER", ssh_user)
        self.docker_service = os.environ.get("DOCKER_SERVICE", docker_service)
        self.local_port = local_port
        self.tunnel_process: subprocess.Popen | None = None

    def _ssh_cmd_base(self) -> list[str]:
        return [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            f"{self.ssh_user}@{self.ssh_host}",
        ]

    def _run_ssh(self, command: str) -> str:
        result = subprocess.run(
            self._ssh_cmd_base() + [command],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"SSH command failed: {result.stderr}")
        return result.stdout.strip()

    def get_prod_config(self) -> dict[str, str]:
        print("Fetching production settings from container...")

        # Single SSH call: find container and dump all settings as JSON
        python_script = """
import json
from app.settings import settings
data = dict()
for field in settings.model_fields:
    val = getattr(settings, field)
    if val is not None:
        data[field.upper()] = str(val)
print(json.dumps(data))
"""
        cmd = (
            f"docker ps --filter name={self.docker_service} --format '{{{{.ID}}}}' | head -n 1 | "
            f"xargs -I CONTAINER docker exec CONTAINER python -c '{python_script}'"
        )

        output = self._run_ssh(cmd)
        config = json.loads(output)
        print(f"  Loaded {len(config)} settings")
        return config

    def start_tunnel(self, db_host: str, db_port: str) -> None:
        print(
            f"Creating SSH tunnel to {db_host}:{db_port} via localhost:{self.local_port}..."
        )

        tunnel_cmd = [
            "ssh",
            "-N",
            "-L",
            f"{self.local_port}:{db_host}:{db_port}",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            f"{self.ssh_user}@{self.ssh_host}",
        ]

        self.tunnel_process = subprocess.Popen(
            tunnel_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        atexit.register(self.stop_tunnel)
        time.sleep(2)

        if self.tunnel_process.poll() is not None:
            _, stderr = self.tunnel_process.communicate()
            raise RuntimeError(f"SSH tunnel failed: {stderr.decode()}")

        print("  Tunnel established")

    def stop_tunnel(self) -> None:
        if self.tunnel_process and self.tunnel_process.poll() is None:
            self.tunnel_process.terminate()
            try:
                self.tunnel_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.tunnel_process.kill()

    def run(self) -> None:
        config = self.get_prod_config()
        self.start_tunnel(config["DATABASE_HOST"], config["DATABASE_PORT"])

        # Set all production settings as environment variables
        env = os.environ.copy()
        env.update(config)

        # Override database connection to use the tunnel
        env["DATABASE_HOST"] = "localhost"
        env["DATABASE_PORT"] = str(self.local_port)
        env["DATABASE_URL"] = (
            f"postgresql+asyncpg://{config['DATABASE_USER']}:{config['DATABASE_PASSWORD']}"
            f"@localhost:{self.local_port}/{config['DATABASE_NAME']}"
        )

        print("\nStarting IPython shell with production settings...")
        print("---")

        try:
            subprocess.run(
                ["ipython", "-i", "app/shell.py"],
                env=env,
            )
        finally:
            self.stop_tunnel()


def main() -> None:
    try:
        ProdShell().run()
    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
