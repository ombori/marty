"""Configuration from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # PostgreSQL (from postgresql-credentials secret)
    postgres_host: str = "postgresql.agents.svc.cluster.local"
    postgres_port: int = 5432
    postgres_user: str = "app"
    postgres_password: str = ""
    postgres_db: str = "app"

    # Redis (from redis-credentials secret)
    redis_host: str = "redis-master.agents.svc.cluster.local"
    redis_port: int = 6379
    redis_password: str = ""

    # Qdrant (from qdrant-credentials secret)
    qdrant_host: str = "qdrant.agents.svc.cluster.local"
    qdrant_port: int = 6333
    qdrant_api_key: str = ""

    class Config:
        env_prefix = ""
        case_sensitive = False


settings = Settings()
