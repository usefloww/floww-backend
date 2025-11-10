import os
from pathlib import Path
from typing import Any, Literal

from pydantic import computed_field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
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


class GeneralConfig(BaseSettings):
    PUBLIC_API_URL: str = "http://localhost:8000"
    RUNTIME_TYPE: Literal["lambda", "docker", "kubernetes"] = "lambda"
    RUN_MIGRATIONS_ON_STARTUP: bool = False


class LambdaConfig(BaseSettings):
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"

    LAMBDA_EXECUTION_ROLE_ARN: str = "arn:aws:iam::501046919403:role/LambdaRole"
    ECR_REGISTRY_URL: str = (
        "501046919403.dkr.ecr.us-east-1.amazonaws.com/trigger-lambda"
    )


class DockerConfig(BaseSettings):
    DOCKER_REGISTRY_URL: str = ""
    DOCKER_REGISTRY_USER: str = ""
    DOCKER_REGISTRY_PASSWORD: str = ""


class KubernetesConfig(BaseSettings):
    DOCKER_REGISTRY_URL: str = ""
    DOCKER_REGISTRY_USER: str = ""
    DOCKER_REGISTRY_PASSWORD: str = ""


class DatabaseConfig(BaseSettings):
    DATABASE_USER: str = "admin"
    DATABASE_PASSWORD: str = "secret"
    DATABASE_HOST: str = "db"
    DATABASE_PORT: int = 5432
    DATABASE_NAME: str = "postgres"
    SESSION_SECRET_KEY: str = "floww-session-secret-change-in-production"

    # Secret encryption key (must be a valid Fernet key - 32 url-safe base64-encoded bytes)
    ENCRYPTION_KEY: str = "OTLHgX6E8_3k-c6rHBsbHDKnuPGtmD1ycNip9CgfiFk="

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.DATABASE_USER}:{self.DATABASE_PASSWORD}@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"


class AuthConfig(BaseSettings):
    AUTH_TYPE: Literal["oidc", "workos", "password"] = "workos"

    # OIDC settings (works with any OIDC-compliant provider: WorkOS, Auth0, Keycloak, etc.)
    AUTH_CLIENT_ID: str = ""
    AUTH_DEVICE_CLIENT_ID: str = ""
    AUTH_CLIENT_SECRET: str = ""
    AUTH_ISSUER_URL: str = ""  # OIDC issuer URL for discovery

    JWT_ALGORITHM: str = "RS256"

    # Workflow JWT settings (for workflow-to-backend authentication)
    WORKFLOW_JWT_SECRET: str = "floww-workflow-jwt-secret-change-in-production"
    WORKFLOW_JWT_ALGORITHM: str = "HS256"
    WORKFLOW_JWT_EXPIRATION_SECONDS: int = 300  # 5 minutes


class CentrifugoConfig(BaseSettings):
    CENTRIFUGO_HOST: str = "centrifugo"
    CENTRIFUGO_PORT: int = 8000
    CENTRIFUGO_API_KEY: str = "floww-api-key-dev"
    CENTRIFUGO_JWT_SECRET: str = "floww-dev-jwt-secret-key-change-in-production"
    CENTRIFUGO_PUBLIC_URL: str = (
        "http://localhost:5001"  # Public URL for WebSocket connections
    )


class SingleOrgConfig(BaseSettings):
    SINGLE_ORG_MODE: bool = False
    SINGLE_ORG_NAME: str = "default"
    SINGLE_ORG_DISPLAY_NAME: str = "Default Organization"
    SINGLE_ORG_DEFAULT_ROLE: Literal["owner", "admin", "member"] = "member"
    SINGLE_ORG_ALLOW_PERSONAL_NAMESPACES: bool = False


class Settings(
    AuthConfig,
    CentrifugoConfig,
    DatabaseConfig,
    GeneralConfig,
    DockerConfig,
    KubernetesConfig,
    LambdaConfig,
    SingleOrgConfig,
    BaseSettings,
):
    model_config = SettingsConfigDict(env_file=(".env", ".env.prod", ".env.test"))

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """
        Define the priority order for settings sources.

        Priority (highest to lowest):
        1. Docker secrets from files (reads *_FILE env vars)
        2. Environment variables
        3. .env files
        4. Default values
        """
        return (
            init_settings,
            DockerSecretsSettingsSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    @model_validator(mode="after")
    def validate_auth_type_none_requires_single_org(self):
        """Validate that AUTH_TYPE='none' requires SINGLE_ORG_MODE=True"""
        if self.AUTH_TYPE == "none" and not self.SINGLE_ORG_MODE:
            raise ValueError(
                "AUTH_TYPE='none' (anonymous authentication) requires SINGLE_ORG_MODE=True. "
                "Anonymous authentication is only supported in single-organization mode."
            )
        return self


settings = Settings()
