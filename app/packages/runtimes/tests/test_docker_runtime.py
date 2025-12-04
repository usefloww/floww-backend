import io
import socket
import tarfile
import time
from unittest.mock import patch

import aiodocker
import pytest

from app.packages.runtimes.tests.runtime_test_utils import EXAMPLE_USER_CODE

from ..implementations.docker_runtime import DockerRuntime
from ..runtime_types import RuntimeConfig
from ..utils.docker import _get_config


def _find_free_port():
    """Find a free port on the host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


async def _get_image_uri(image: str) -> str:
    async with aiodocker.Docker() as docker:
        image_info = await docker.images.inspect(image)
        return image_info["Id"]


async def _get_config_override(
    host_port: int,
):
    async def func(
        runtime_id: str,
        image_hash: str,
        container_name: str,
    ):
        config = await _get_config(runtime_id, image_hash, container_name)
        config["ExposedPorts"] = {"8000/tcp": {}}
        config["HostConfig"] = {}
        config["HostConfig"]["PortBindings"] = {
            "8000/tcp": [{"HostPort": str(host_port)}]
        }
        return config

    return func


@pytest.fixture(scope="function", autouse=True)
async def cleanup_runtimes():
    async with aiodocker.Docker() as docker:
        containers = await docker.containers.list(
            all=True, filters={"label": ["floww.runtime.id"]}
        )
        for container in containers:
            await container.delete(force=True)


@pytest.fixture(scope="session")
async def base_image() -> str:
    image = "ghcr.io/usefloww/docker-runtime:latest"
    async with aiodocker.Docker() as docker:
        await docker.images.pull(from_image=image)
    return image


@pytest.fixture(scope="session")
async def minimal_example_image() -> str:
    dockerfile = """
    FROM ghcr.io/usefloww/docker-runtime:latest

    RUN echo '{"type":"module","dependencies":{"floww":"*","fastify":"^5.2.0"}}' > package.json && \
        npm install
    """

    tag = "ghcr.io/usefloww/docker-runtime:real"

    async with aiodocker.Docker() as docker:
        # aiodocker requires a tar archive for `fileobj` when building images.
        # Docker expects a gzip-compressed tar archive.
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w:gz") as tar:
            dockerfile_info = tarfile.TarInfo(name="Dockerfile")
            dockerfile_bytes = dockerfile.encode("utf-8")
            dockerfile_info.size = len(dockerfile_bytes)
            tar.addfile(dockerfile_info, io.BytesIO(dockerfile_bytes))

        tar_stream.seek(0)
        await docker.images.build(fileobj=tar_stream, tag=tag, encoding="gzip")

    return tag


@pytest.fixture
async def docker_runtime():
    return DockerRuntime(
        repository_name="docker-runtime",
        registry_url="ghcr.io/usefloww",
    )


@pytest.fixture(autouse=True)
async def patched_get_config():
    """Fixture that patches _get_config to expose a local port."""

    host_port = _find_free_port()
    _get_config = await _get_config_override(host_port)

    with patch("app.packages.runtimes.utils.docker._get_config", _get_config):
        with patch(
            "app.packages.runtimes.utils.docker._get_container_url",
            return_value=f"http://localhost:{host_port}",
        ):
            yield


class TestDockerRuntime:
    async def test_create_runtime(self, docker_runtime: DockerRuntime, base_image: str):
        image_hash = await _get_image_uri(base_image)
        result = await docker_runtime.create_runtime(
            RuntimeConfig(runtime_id="runtime_id", image_digest=image_hash)
        )
        assert result.status == "IN_PROGRESS"

    async def test_get_runtime_status_failed(
        self, docker_runtime: DockerRuntime, base_image: str
    ):
        """Should fail because the base image does not have the proper dependencies."""
        image_hash = await _get_image_uri(base_image)
        runtime_id = "runtime_id"

        result = await docker_runtime.create_runtime(
            RuntimeConfig(runtime_id=runtime_id, image_digest=image_hash)
        )
        assert result.status == "IN_PROGRESS"

        status_result = await docker_runtime.get_runtime_status(runtime_id=runtime_id)
        assert status_result.status == "FAILED"

    async def test_runtime_creation_and_invocation(
        self, docker_runtime: DockerRuntime, minimal_example_image: str
    ):
        image_hash = await _get_image_uri(minimal_example_image)
        runtime_id = "runtime_idd"

        result = await docker_runtime.create_runtime(
            RuntimeConfig(runtime_id=runtime_id, image_digest=image_hash)
        )
        assert result.status == "IN_PROGRESS"

        time.sleep(5)
        status_result = await docker_runtime.get_runtime_status(runtime_id=runtime_id)
        assert status_result.status == "COMPLETED"

        await docker_runtime.invoke_trigger(
            trigger_id="trigger_id",
            runtime_config=RuntimeConfig(
                runtime_id=runtime_id, image_digest=image_hash
            ),
            user_code={
                "files": {
                    "main.ts": EXAMPLE_USER_CODE,
                },
                "entrypoint": "main.ts",
            },
            payload={
                "trigger": {
                    "provider": {
                        "type": "builtin",
                        "alias": "default",
                    },
                    "triggerType": "onCron",
                    "input": {"expression": "*/10 * * * *"},
                },
            },
        )
