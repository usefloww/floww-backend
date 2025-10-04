from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    example: str = "example"
    
    DATABASE_URL: str = "postgresql+asyncpg://admin:secret@localhost:5432/postgres"
    


settings = Settings()
