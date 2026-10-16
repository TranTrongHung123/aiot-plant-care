from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        extra="ignore",
        hide_input_in_errors=True,
    )
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "plantcare"
    postgres_user: str = "plantcare"
    postgres_password: str
    minio_endpoint: str = "127.0.0.1:9000"
    minio_app_user: str
    minio_app_password: str
    minio_secure: bool = False
    minio_images_bucket: str = "plant-images"
    app_token: str
    device_mode: str = "simulation"
    ai_provider: str = "trained"
    leaf_model_dir: str = "model-artifacts/leaf/mobilenetv3-20261015T031708_509896Z"
    fruit_model_dir: str = "model-artifacts/fruit/full-20261015T040727_579719Z"
    weather_model_dir: str = "model-artifacts/weather/xgboost-20261015T035744_305966Z"
    weather_allow_domain_shift: bool = True
    openweather_api_key: str = ""
    weather_latitude: str = ""
    weather_longitude: str = ""
    demo_mqtt_host: str = "127.0.0.1"
    demo_mqtt_port: int = 1883
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @model_validator(mode="after")
    def simulation_only(self):
        if self.device_mode != "simulation":
            raise ValueError("This release supports simulated devices only")
        # Old .env files may still say mock; there is no mock execution path anymore.
        if self.ai_provider not in {"mock", "trained"}:
            raise ValueError("AI_PROVIDER must be trained")
        self.ai_provider = "trained"
        if len(self.app_token) < 16:
            raise ValueError("APP_TOKEN must contain at least 16 characters")
        return self

    @property
    def database_url(self):
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


settings = Settings()
