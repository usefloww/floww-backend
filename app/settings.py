from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class GeneralConfig(BaseSettings):
    PUBLIC_API_URL: str = "http://localhost:8000"


class LambdaConfig(BaseSettings):
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"

    LAMBDA_EXECUTION_ROLE_ARN: str = "arn:aws:iam::501046919403:role/LambdaRole"
    ECR_REGISTRY_URL: str = (
        "501046919403.dkr.ecr.us-east-1.amazonaws.com/trigger-lambda"
    )


class DatabaseConfig(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://admin:secret@db:5432/postgres"
    SESSION_SECRET_KEY: str = "floww-session-secret-change-in-production"

    # Secret encryption key (must be a valid Fernet key - 32 url-safe base64-encoded bytes)
    ENCRYPTION_KEY: str = "OTLHgX6E8_3k-c6rHBsbHDKnuPGtmD1ycNip9CgfiFk="


class AuthConfig(BaseSettings):
    ADMIN_PASSWORD: str = ""
    AUTH_PROVIDER: Literal["workos", "auth0", "oidc"] = "workos"
    AUTH_CLIENT_ID: str = ""
    AUTH_CLIENT_SECRET: str = ""
    AUTH_DOMAIN: str = ""
    WORKOS_CLIENT_ID: str = ""
    WORKOS_CLIENT_SECRET: str = ""
    WORKOS_API_URL: str = "https://api.workos.com"
    JWT_ALGORITHM: str = "RS256"


class CentrifugoConfig(BaseSettings):
    CENTRIFUGO_HOST: str = "centrifugo"
    CENTRIFUGO_PORT: int = 8000
    CENTRIFUGO_API_KEY: str = "floww-api-key-dev"
    CENTRIFUGO_JWT_SECRET: str = "floww-dev-jwt-secret-key-change-in-production"


class Settings(
    AuthConfig,
    CentrifugoConfig,
    DatabaseConfig,
    GeneralConfig,
    LambdaConfig,
    BaseSettings,
):
    model_config = SettingsConfigDict(env_file=(".env", ".env.prod"))


settings = Settings()
