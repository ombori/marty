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

    # Wise API
    wise_api_token: str = ""
    wise_private_key_path: str = "./wise_private.pem"
    wise_api_base: str = "https://api.wise.com"

    # Spectre API
    spectre_api_url: str = "http://spectre.agents.svc.cluster.local"
    spectre_api_key: str = ""

    # Slack
    slack_webhook_url: str = ""
    slack_channel: str = "#accounting-alerts"

    @property
    def database_url(self) -> str:
        """SQLAlchemy database URL."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        """Synchronous database URL for Alembic."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    class Config:
        env_prefix = ""
        case_sensitive = False


settings = Settings()


# Entity configuration - maps profile IDs to entity names
ENTITIES = {
    19941830: {"name": "Phygrid Limited", "jurisdiction": "UK"},
    76219117: {"name": "Phygrid S.A.", "jurisdiction": "Luxembourg"},
    70350947: {"name": "Phygrid Inc", "jurisdiction": "US"},
    52035101: {"name": "PHYGRID AB (PUBL)", "jurisdiction": "Sweden"},
    78680339: {"name": "Ombori, Inc", "jurisdiction": "US"},
    47253364: {"name": "Ombori AG", "jurisdiction": "Switzerland"},
    25587793: {"name": "Fendops Limited", "jurisdiction": "UK"},
    21069793: {"name": "Fendops Kft", "jurisdiction": "Hungary"},
    66668662: {"name": "NEXORA AB", "jurisdiction": "Sweden"},
    49911299: {"name": "Ombori Services Limited", "jurisdiction": "Hong Kong"},
    52034148: {"name": "OMBORI GROUP SWEDEN AB", "jurisdiction": "Sweden"},
}

# Reverse lookup - entity name to profile ID (normalized lowercase)
ENTITY_NAME_TO_PROFILE = {info["name"].lower(): profile_id for profile_id, info in ENTITIES.items()}
