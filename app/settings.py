from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    example: str = "example"

    DATABASE_URL: str = "postgresql+asyncpg://admin:secret@localhost:5432/postgres"

    # WorkOS JWT Authentication
    WORKOS_CLIENT_ID: str = ""
    WORKOS_API_URL: str = "https://api.workos.com"
    JWT_ALGORITHM: str = "RS256"


settings = Settings()
