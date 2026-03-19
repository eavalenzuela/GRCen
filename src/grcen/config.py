from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    APP_NAME: str = "GRCen"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-to-a-random-secret-key"
    DATABASE_URL: str = "postgresql+asyncpg://grcen:grcen@localhost:5432/grcen"
    UPLOAD_DIR: str = "./uploads"


settings = Settings()
