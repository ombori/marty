"""Redis cache layer for reconciliation data."""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import redis.asyncio as redis

from app.config import ENTITIES, settings

logger = logging.getLogger(__name__)


@dataclass
class ICAccountMapping:
    """Intercompany account mapping."""

    account_id: int
    account_number: str
    account_name: str
    counterparty_subsidiary: str


@dataclass
class EntityInfo:
    """Entity information."""

    wise_profile_id: int
    netsuite_subsidiary_id: int
    name: str
    currency: str
    jurisdiction: str


class CacheClient:
    """Redis cache client for reconciliation data."""

    # Key prefixes
    IC_ACCOUNTS_PREFIX = "ic:accounts:"
    ENTITY_NAMES_KEY = "entities:names"
    ENTITY_PREFIX = "entities:"
    SCA_SESSION_PREFIX = "sca:session:"
    RATE_LIMIT_PREFIX = "ratelimit:wise"
    GL_ENTRIES_PREFIX = "gl:entries:"

    # TTLs in seconds
    IC_ACCOUNTS_TTL = 3600  # 1 hour
    ENTITY_TTL = 86400  # 24 hours
    SCA_SESSION_TTL = 300  # 5 minutes
    GL_ENTRIES_TTL = 600  # 10 minutes

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        password: str | None = None,
    ):
        self.host = host or settings.redis_host
        self.port = port or settings.redis_port
        self.password = password or settings.redis_password
        self._client: redis.Redis | None = None

    async def __aenter__(self) -> "CacheClient":
        """Async context manager entry."""
        self._client = redis.Redis(
            host=self.host,
            port=self.port,
            password=self.password or None,
            decode_responses=True,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> redis.Redis:
        """Get Redis client."""
        if self._client is None:
            raise RuntimeError("Cache client not initialized. Use async with context manager.")
        return self._client

    # IC Account Mappings
    async def get_ic_accounts(self, subsidiary_id: int) -> list[ICAccountMapping] | None:
        """Get IC account mappings for a subsidiary."""
        key = f"{self.IC_ACCOUNTS_PREFIX}{subsidiary_id}"
        data = await self.client.get(key)
        if data is None:
            return None

        items = json.loads(data)
        return [
            ICAccountMapping(
                account_id=item["account_id"],
                account_number=item["account_number"],
                account_name=item["account_name"],
                counterparty_subsidiary=item["counterparty_subsidiary"],
            )
            for item in items
        ]

    async def set_ic_accounts(self, subsidiary_id: int, accounts: list[ICAccountMapping]) -> None:
        """Cache IC account mappings."""
        key = f"{self.IC_ACCOUNTS_PREFIX}{subsidiary_id}"
        data = json.dumps([asdict(a) for a in accounts])
        await self.client.setex(key, self.IC_ACCOUNTS_TTL, data)

    # Entity Lookups
    async def get_entity_by_name(self, name: str) -> EntityInfo | None:
        """Get entity info by name (case-insensitive)."""
        normalized = name.lower().strip()

        # First check name mapping
        profile_id = await self.client.hget(self.ENTITY_NAMES_KEY, normalized)
        if profile_id is None:
            return None

        # Then get entity details
        return await self.get_entity(int(profile_id))

    async def get_entity(self, profile_id: int) -> EntityInfo | None:
        """Get entity info by profile ID."""
        key = f"{self.ENTITY_PREFIX}{profile_id}"
        data = await self.client.hgetall(key)
        if not data:
            return None

        return EntityInfo(
            wise_profile_id=int(data["wise_profile_id"]),
            netsuite_subsidiary_id=int(data["netsuite_subsidiary_id"]),
            name=data["name"],
            currency=data["currency"],
            jurisdiction=data["jurisdiction"],
        )

    async def set_entity(self, entity: EntityInfo) -> None:
        """Cache entity info."""
        # Set entity details
        key = f"{self.ENTITY_PREFIX}{entity.wise_profile_id}"
        await self.client.hset(
            key,
            mapping={
                "wise_profile_id": entity.wise_profile_id,
                "netsuite_subsidiary_id": entity.netsuite_subsidiary_id,
                "name": entity.name,
                "currency": entity.currency,
                "jurisdiction": entity.jurisdiction,
            },
        )
        await self.client.expire(key, self.ENTITY_TTL)

        # Add to name lookup
        normalized = entity.name.lower().strip()
        await self.client.hset(self.ENTITY_NAMES_KEY, normalized, entity.wise_profile_id)
        await self.client.expire(self.ENTITY_NAMES_KEY, self.ENTITY_TTL)

    async def initialize_entities(
        self, entity_subsidiary_map: dict[int, int] | None = None
    ) -> None:
        """Initialize entity cache from config.

        Args:
            entity_subsidiary_map: Optional mapping of profile_id to NetSuite subsidiary_id
        """
        entity_subsidiary_map = entity_subsidiary_map or {}

        for profile_id, info in ENTITIES.items():
            entity = EntityInfo(
                wise_profile_id=profile_id,
                netsuite_subsidiary_id=entity_subsidiary_map.get(profile_id, 0),
                name=info["name"],
                currency="EUR",  # Default, should be fetched from Wise
                jurisdiction=info["jurisdiction"],
            )
            await self.set_entity(entity)

    # SCA Session
    async def get_sca_session(self, profile_id: int) -> datetime | None:
        """Get SCA session expiry for a profile."""
        key = f"{self.SCA_SESSION_PREFIX}{profile_id}"
        expiry = await self.client.get(key)
        if expiry is None:
            return None
        return datetime.fromisoformat(expiry)

    async def set_sca_session(self, profile_id: int, expiry: datetime) -> None:
        """Set SCA session expiry."""
        key = f"{self.SCA_SESSION_PREFIX}{profile_id}"
        await self.client.setex(key, self.SCA_SESSION_TTL, expiry.isoformat())

    async def is_sca_valid(self, profile_id: int) -> bool:
        """Check if SCA session is still valid."""
        expiry = await self.get_sca_session(profile_id)
        if expiry is None:
            return False
        return datetime.now(expiry.tzinfo) < expiry

    # Rate Limiting
    async def check_rate_limit(self, max_requests: int = 10) -> bool:
        """Check if we're within rate limits.

        Args:
            max_requests: Maximum requests per second

        Returns:
            True if within limits, False if should wait
        """
        count = await self.client.incr(self.RATE_LIMIT_PREFIX)
        if count == 1:
            await self.client.expire(self.RATE_LIMIT_PREFIX, 1)
        return count <= max_requests

    async def wait_for_rate_limit(self, max_requests: int = 10) -> None:
        """Wait until rate limit allows request."""
        import asyncio

        while not await self.check_rate_limit(max_requests):
            await asyncio.sleep(0.1)

    # GL Entry Cache
    async def get_gl_entries(
        self,
        subsidiary_id: int,
        start_date: datetime,
        end_date: datetime,
    ) -> list[dict[str, Any]] | None:
        """Get cached GL entries."""
        key = self._gl_entries_key(subsidiary_id, start_date, end_date)
        data = await self.client.get(key)
        if data is None:
            return None
        return json.loads(data)

    async def set_gl_entries(
        self,
        subsidiary_id: int,
        start_date: datetime,
        end_date: datetime,
        entries: list[dict[str, Any]],
    ) -> None:
        """Cache GL entries."""
        key = self._gl_entries_key(subsidiary_id, start_date, end_date)
        # Convert Decimal to str for JSON serialization
        serializable = []
        for entry in entries:
            item = {}
            for k, v in entry.items():
                if isinstance(v, Decimal):
                    item[k] = str(v)
                elif isinstance(v, datetime):
                    item[k] = v.isoformat()
                else:
                    item[k] = v
            serializable.append(item)
        await self.client.setex(key, self.GL_ENTRIES_TTL, json.dumps(serializable))

    def _gl_entries_key(self, subsidiary_id: int, start_date: datetime, end_date: datetime) -> str:
        """Generate GL entries cache key."""
        start = start_date.date().isoformat()
        end = end_date.date().isoformat()
        return f"{self.GL_ENTRIES_PREFIX}{subsidiary_id}:{start}:{end}"

    # Generic operations
    async def delete(self, key: str) -> None:
        """Delete a key."""
        await self.client.delete(key)

    async def flush_cache(self, prefix: str | None = None) -> int:
        """Flush cache keys.

        Args:
            prefix: Optional prefix to filter keys (e.g., 'ic:' for IC accounts)

        Returns:
            Number of keys deleted
        """
        if prefix:
            keys = []
            async for key in self.client.scan_iter(match=f"{prefix}*"):
                keys.append(key)
            if keys:
                return await self.client.delete(*keys)
            return 0
        else:
            await self.client.flushdb()
            return -1  # Unknown count
