from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://admin:secret@localhost:5432/postgres"

    WORKOS_CLIENT_ID: str = "client_01K6QQP8Q721ZX1YM1PBV3EWMR"
    WORKOS_API_URL: str = "https://api.workos.com"
    JWT_ALGORITHM: str = "RS256"


settings = Settings()
