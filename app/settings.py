from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://admin:secret@db:5432/postgres"

    WORKOS_CLIENT_ID: str = "client_01K6QQP8Q721ZX1YM1PBV3EWMR"
    WORKOS_CLIENT_SECRET: str = ""  # Required for OAuth flow
    WORKOS_API_URL: str = "https://api.workos.com"
    WORKOS_REDIRECT_URI: str = "http://localhost:8000/auth/callback"
    JWT_ALGORITHM: str = "RS256"

    # Session settings
    SESSION_SECRET_KEY: str = "floww-session-secret-change-in-production"

    # Centrifugo settings
    CENTRIFUGO_HOST: str = "centrifugo"
    CENTRIFUGO_PORT: int = 8000
    CENTRIFUGO_API_KEY: str = "floww-api-key-dev"
    CENTRIFUGO_JWT_SECRET: str = "floww-dev-jwt-secret-key-change-in-production"

    # Secret encryption key (must be a valid Fernet key - 32 url-safe base64-encoded bytes)
    ENCRYPTION_KEY: str = "OTLHgX6E8_3k-c6rHBsbHDKnuPGtmD1ycNip9CgfiFk="

    # AWS Credentials
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"


settings = Settings()
