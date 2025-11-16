import os
from pathlib import Path
from typing import Any

from pydantic_settings import (
    PydanticBaseSettingsSource,
)


class DockerSecretsSettingsSource(PydanticBaseSettingsSource):
    """
    Custom settings source that reads Docker secrets from files.

    For any setting, if an environment variable <SETTING_NAME>_FILE exists,
    it will read the secret value from that file path.

    Example:
        If AUTH_CLIENT_SECRET_FILE=/run/secrets/backend_auth_client_secret
        Then AUTH_CLIENT_SECRET will be read from that file
    """

    def get_field_value(
        self, field_name: str, field_info: Any
    ) -> tuple[Any, str, bool]:
        # Check if there's a *_FILE env var for this field
        file_env_name = f"{field_name}_FILE"
        file_path = os.getenv(file_env_name)

        if file_path and Path(file_path).exists():
            try:
                # Read the secret from the file
                secret_value = Path(file_path).read_text().strip()
                return secret_value, field_name, False
            except Exception as e:
                # If we can't read the file, log and continue
                print(f"Warning: Could not read secret from {file_path}: {e}")

        return None, field_name, False

    def prepare_field_value(
        self, field_name: str, field: Any, value: Any, value_is_complex: bool
    ) -> Any:
        return value

    def __call__(self) -> dict[str, Any]:
        d: dict[str, Any] = {}

        for field_name in self.settings_cls.model_fields:
            field_value, field_key, value_is_complex = self.get_field_value(
                field_name, self.settings_cls.model_fields[field_name]
            )
            if field_value is not None:
                d[field_key] = field_value

        return d
