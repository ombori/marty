"""Main FastAPI application."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .health import get_health_status

app = FastAPI(title="Agent", version="1.0.0")


@app.get("/")
async def root():
    return {"message": "Agent running"}


@app.get("/health")
async def health():
    """Liveness probe - always returns OK if app is running."""
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready():
    """Readiness probe - checks all dependencies."""
    status = await get_health_status()
    code = 200 if status["status"] == "healthy" else 503
    return JSONResponse(content=status, status_code=code)


@app.get("/health/full")
async def health_full():
    """Full health check with details."""
    return await get_health_status()
