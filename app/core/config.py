from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    jwt_secret: str
    jwt_access_token_minutes: int = Field(default=60, gt=0)
    database_url: str
    worker_interval_seconds: int = Field(default=60, gt=0)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
