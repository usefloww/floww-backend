from typing import Literal

from pydantic import computed_field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from app.utils.encryption import generate_cryptographic_key
from app.utils.settings_utils import DockerSecretsSettingsSource


class GeneralConfig(BaseSettings):
    PUBLIC_API_URL: str = "http://localhost:8000"
    RUNTIME_TYPE: Literal["lambda", "docker", "kubernetes"] = "lambda"
    RUN_MIGRATIONS_ON_STARTUP: bool = True
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str = ""


class LambdaConfig(BaseSettings):
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"

    LAMBDA_EXECUTION_ROLE_ARN: str = "arn:aws:iam::501046919403:role/LambdaRole"


class RegistryConfig(BaseSettings):
    REGISTRY_URL: str = ""
    REGISTRY_URL_RUNTIME: str = ""
    REGISTRY_REPOSITORY_NAME: str = ""
    REGISTRY_AUTH_USER: str = ""
    REGISTRY_AUTH_PASSWORD: str = ""

    REGISTRY_RANDOM_SECRET: str = generate_cryptographic_key(32)
    """Random secret used to secure the registry pull endpoint
    This can be used to securily pull images from the registry
    """

    @model_validator(mode="after")
    def fill_runtime(self):
        if not self.REGISTRY_URL_RUNTIME:
            self.REGISTRY_URL_RUNTIME = self.REGISTRY_URL
        return self


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
    AUTH_CLIENT_SECRET: str = ""
    AUTH_ISSUER_URL: str = ""  # OIDC issuer URL for discovery
    DEVICE_AUTH_AUDIENCE: str | None = None

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


class BillingConfig(BaseSettings):
    IS_CLOUD: bool = False
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    STRIPE_PRICE_ID_HOBBY: str = ""
    STRIPE_PRICE_ID_TEAM: str = ""

    TRIAL_PERIOD_DAYS: int = 0
    GRACE_PERIOD_DAYS: int = 7


class SchedulerConfig(BaseSettings):
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_JOB_STORE_TABLE: str = "apscheduler_jobs"

    @computed_field
    @property
    def SYNC_DATABASE_URL(self) -> str:
        """
        Convert async database URL to sync URL for APScheduler.

        APScheduler 3.x requires synchronous database access, so we convert
        postgresql+asyncpg:// to postgresql+psycopg2://
        """
        # Access DATABASE_URL from Settings through self
        # This will be available when SchedulerConfig is mixed into Settings
        if hasattr(self, "DATABASE_USER"):
            return f"postgresql+psycopg2://{self.DATABASE_USER}:{self.DATABASE_PASSWORD}@{self.DATABASE_HOST}:{self.DATABASE_PORT}/{self.DATABASE_NAME}"
        return ""


class IntegrationsConfig(BaseSettings):
    GOOGLE_OAUTH_CLIENT_ID: str = ""
    GOOGLE_OAUTH_CLIENT_SECRET: str = ""


class AIConfig(BaseSettings):
    AI_MODEL_REQUIREMENTS: str = "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    AI_MODEL_PLANNING: str = "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    AI_MODEL_CODEGEN: str = "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    AI_MODEL_VERIFICATION: str = "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"


class Settings(
    AuthConfig,
    CentrifugoConfig,
    DatabaseConfig,
    GeneralConfig,
    RegistryConfig,
    LambdaConfig,
    SingleOrgConfig,
    BillingConfig,
    SchedulerConfig,
    IntegrationsConfig,
    AIConfig,
    BaseSettings,
):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.prod", ".env.test"),
        extra="allow",
    )

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
