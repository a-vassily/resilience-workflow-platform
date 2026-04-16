from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore', case_sensitive=False)

    database_url: str = Field(default='postgresql+psycopg://resilience:resilience@localhost:5432/resilience', alias='DATABASE_URL')
    minio_endpoint: str = Field(default='localhost:9000', alias='MINIO_ENDPOINT')
    minio_access_key: str = Field(default='minio', alias='MINIO_ROOT_USER')
    minio_secret_key: str = Field(default='minio12345', alias='MINIO_ROOT_PASSWORD')
    minio_secure: bool = Field(default=False, alias='MINIO_SECURE')

    llm_base_url: str = Field(default='http://localhost:1234', alias='LLM_BASE_URL')
    llm_chat_path: str = Field(default='/v1/responses', alias='LLM_CHAT_PATH')
    llm_model: str = Field(default='qwen2.5-7b-instruct', alias='LLM_MODEL')

    intelligence_service_url: str = Field(default='http://localhost:8001', alias='INTELLIGENCE_SERVICE_URL')
    ingest_api_url: str = Field(default='http://localhost:8000', alias='INGEST_API_URL')
    control_api_url: str = Field(default='http://localhost:8002', alias='CONTROL_API_URL')
    app_env: str = Field(default='local', alias='APP_ENV')
    log_level: str = Field(default='INFO', alias='LOG_LEVEL')


@lru_cache
def get_settings() -> Settings:
    return Settings()
