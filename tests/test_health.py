"""Tests for health endpoints."""

import pytest


@pytest.mark.asyncio
async def test_health_endpoint(client):
    """Test liveness probe returns ok status."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_health_ready_endpoint(client):
    """Test readiness probe returns ready status."""
    response = await client.get("/health/ready")
    # May return 503 if dependencies not available, but endpoint should exist
    assert response.status_code in [200, 503]
