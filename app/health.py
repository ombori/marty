"""Health check endpoints."""

import asyncio
from typing import Any

import asyncpg
import httpx
import redis.asyncio as redis

from .config import settings


async def check_postgresql() -> dict[str, Any]:
    """Check PostgreSQL connectivity."""
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(
                host=settings.postgres_host,
                port=settings.postgres_port,
                user=settings.postgres_user,
                password=settings.postgres_password,
                database=settings.postgres_db,
            ),
            timeout=5.0,
        )
        version = await conn.fetchval("SELECT version()")
        await conn.close()
        return {"status": "healthy", "version": version[:50] + "..."}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


async def check_redis() -> dict[str, Any]:
    """Check Redis connectivity."""
    try:
        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            decode_responses=True,
        )
        await asyncio.wait_for(client.ping(), timeout=5.0)
        info = await client.info("server")
        await client.close()
        return {"status": "healthy", "version": info.get("redis_version", "unknown")}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


async def check_qdrant() -> dict[str, Any]:
    """Check Qdrant connectivity."""
    try:
        url = f"http://{settings.qdrant_host}:{settings.qdrant_port}/readyz"
        headers = {}
        if settings.qdrant_api_key:
            headers["api-key"] = settings.qdrant_api_key

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=5.0)
            if response.status_code == 200:
                return {"status": "healthy"}
            return {"status": "unhealthy", "code": response.status_code}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


async def get_health_status() -> dict[str, Any]:
    """Get overall health status."""
    postgres, redis_status, qdrant = await asyncio.gather(
        check_postgresql(),
        check_redis(),
        check_qdrant(),
        return_exceptions=True,
    )

    # Handle exceptions
    if isinstance(postgres, Exception):
        postgres = {"status": "unhealthy", "error": str(postgres)}
    if isinstance(redis_status, Exception):
        redis_status = {"status": "unhealthy", "error": str(redis_status)}
    if isinstance(qdrant, Exception):
        qdrant = {"status": "unhealthy", "error": str(qdrant)}

    all_healthy = all(s.get("status") == "healthy" for s in [postgres, redis_status, qdrant])

    return {
        "status": "healthy" if all_healthy else "degraded",
        "services": {
            "postgresql": postgres,
            "redis": redis_status,
            "qdrant": qdrant,
        },
    }
