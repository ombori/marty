"""Tests for API endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def api_client():
    """Create async test client for API."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


class TestReconcileAPI:
    """Tests for reconciliation API endpoints."""

    @pytest.mark.asyncio
    async def test_list_entities(self, api_client):
        """Test listing configured entities."""
        response = await api_client.get("/api/recon/entities")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        # Check first entity structure
        entity = data[0]
        assert "profile_id" in entity
        assert "name" in entity
        assert "jurisdiction" in entity

    @pytest.mark.asyncio
    async def test_trigger_reconciliation(self, api_client):
        """Test triggering reconciliation."""
        response = await api_client.post(
            "/api/recon/trigger",
            json={
                "profile_id": 19941830,
                "fetch_transactions": True,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert "Phygrid Limited" in data["message"]

    @pytest.mark.asyncio
    async def test_trigger_reconciliation_invalid_profile(self, api_client):
        """Test triggering reconciliation with invalid profile."""
        response = await api_client.post(
            "/api/recon/trigger",
            json={"profile_id": 99999999},
        )

        assert response.status_code == 400
        assert "Unknown profile ID" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_trigger_sync(self, api_client):
        """Test triggering sync for an entity."""
        response = await api_client.post("/api/recon/sync/19941830")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"

    @pytest.mark.asyncio
    async def test_trigger_sync_invalid_profile(self, api_client):
        """Test triggering sync with invalid profile."""
        response = await api_client.post("/api/recon/sync/99999999")

        assert response.status_code == 404


class TestHealthEndpoints:
    """Tests for health endpoints."""

    @pytest.mark.asyncio
    async def test_root(self, api_client):
        """Test root endpoint."""
        response = await api_client.get("/")

        assert response.status_code == 200
        assert "message" in response.json()

    @pytest.mark.asyncio
    async def test_health(self, api_client):
        """Test health endpoint."""
        response = await api_client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
