from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    database_url: str = "postgresql+asyncpg://tradium:tradium@localhost:5432/tradium"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours
    encryption_key: str = "change-me-to-a-32-byte-hex-key"
    polymarket_api_url: str = "https://clob.polymarket.com"
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com"
    polygon_rpc_url: str = "https://polygon-rpc.com"
    node_env: str = "development"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
