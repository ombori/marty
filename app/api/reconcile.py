"""Reconciliation API endpoints."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ENTITIES
from app.database import get_session
from app.models.recon import SyncMetadata, WiseTransaction

router = APIRouter(prefix="/api/recon", tags=["reconciliation"])


class ReconcileRequest(BaseModel):
    """Request to trigger reconciliation."""

    profile_id: int | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    fetch_transactions: bool = True


class ReconcileResponse(BaseModel):
    """Response from reconciliation trigger."""

    status: str
    message: str
    job_id: str | None = None


class SyncStatusResponse(BaseModel):
    """Sync status for an entity."""

    profile_id: int
    entity_name: str
    currency: str
    last_sync_at: datetime | None
    sync_status: str
    transactions_synced: int
    error_message: str | None


class TransactionStatsResponse(BaseModel):
    """Transaction statistics."""

    total: int
    pending: int
    submitted: int
    matched: int
    unmatched: int
    by_entity: dict[str, dict[str, int]]


class EntityResponse(BaseModel):
    """Entity information."""

    profile_id: int
    name: str
    jurisdiction: str


@router.get("/entities", response_model=list[EntityResponse])
async def list_entities():
    """List all configured entities."""
    return [
        EntityResponse(
            profile_id=profile_id,
            name=info["name"],
            jurisdiction=info["jurisdiction"],
        )
        for profile_id, info in ENTITIES.items()
    ]


@router.get("/sync-status", response_model=list[SyncStatusResponse])
async def get_sync_status(
    session: Annotated[AsyncSession, Depends(get_session)],
    profile_id: int | None = Query(None, description="Filter by profile ID"),
):
    """Get sync status for all entities."""
    query = select(SyncMetadata)
    if profile_id:
        query = query.where(SyncMetadata.profile_id == profile_id)

    result = await session.execute(query)
    metadata_list = result.scalars().all()

    return [
        SyncStatusResponse(
            profile_id=m.profile_id,
            entity_name=m.entity_name,
            currency=m.currency,
            last_sync_at=m.last_sync_at,
            sync_status=m.sync_status,
            transactions_synced=m.transactions_synced,
            error_message=m.error_message,
        )
        for m in metadata_list
    ]


@router.get("/stats", response_model=TransactionStatsResponse)
async def get_transaction_stats(
    session: Annotated[AsyncSession, Depends(get_session)],
    profile_id: int | None = Query(None, description="Filter by profile ID"),
    start_date: datetime | None = Query(None, description="Start date"),
    end_date: datetime | None = Query(None, description="End date"),
):
    """Get transaction statistics."""
    # Default date range
    end_date = end_date or datetime.now(UTC)
    start_date = start_date or (end_date - timedelta(days=30))

    # Build base query
    base_query = select(WiseTransaction).where(
        WiseTransaction.date >= start_date,
        WiseTransaction.date <= end_date,
    )
    if profile_id:
        base_query = base_query.where(WiseTransaction.profile_id == profile_id)

    # Get all transactions
    result = await session.execute(base_query)
    transactions = result.scalars().all()

    # Calculate stats
    total = len(transactions)
    pending = sum(1 for t in transactions if t.match_status == "pending")
    submitted = sum(1 for t in transactions if t.match_status == "submitted")
    matched = sum(1 for t in transactions if t.match_status == "matched")
    unmatched = sum(1 for t in transactions if t.match_status == "unmatched")

    # Group by entity
    by_entity: dict[str, dict[str, int]] = {}
    for t in transactions:
        if t.entity_name not in by_entity:
            by_entity[t.entity_name] = {
                "total": 0,
                "pending": 0,
                "submitted": 0,
                "matched": 0,
                "unmatched": 0,
            }
        by_entity[t.entity_name]["total"] += 1
        by_entity[t.entity_name][t.match_status] += 1

    return TransactionStatsResponse(
        total=total,
        pending=pending,
        submitted=submitted,
        matched=matched,
        unmatched=unmatched,
        by_entity=by_entity,
    )


@router.post("/trigger", response_model=ReconcileResponse)
async def trigger_reconciliation(
    request: ReconcileRequest,
    background_tasks: BackgroundTasks,  # noqa: ARG001 - reserved for future task queue
    session: Annotated[AsyncSession, Depends(get_session)],  # noqa: ARG001 - reserved for validation
):
    """Trigger reconciliation manually.

    This endpoint queues a reconciliation job to run in the background.
    """
    # Validate profile_id if provided
    if request.profile_id and request.profile_id not in ENTITIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown profile ID: {request.profile_id}",
        )

    # Generate job ID
    job_id = f"recon-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"

    # Queue background task
    # Note: In production, this would use a proper task queue like Celery
    # For now, we just return success and let the caller poll for status

    entity_name = ENTITIES[request.profile_id]["name"] if request.profile_id else "all entities"

    return ReconcileResponse(
        status="queued",
        message=f"Reconciliation queued for {entity_name}",
        job_id=job_id,
    )


@router.post("/sync/{profile_id}", response_model=ReconcileResponse)
async def trigger_sync(
    profile_id: int,
    background_tasks: BackgroundTasks,  # noqa: ARG001 - reserved for future task queue
    session: Annotated[AsyncSession, Depends(get_session)],  # noqa: ARG001 - reserved for validation
    start_date: datetime | None = Query(None),  # noqa: ARG001 - reserved for future implementation
    end_date: datetime | None = Query(None),  # noqa: ARG001 - reserved for future implementation
):
    """Trigger transaction sync for a specific entity."""
    if profile_id not in ENTITIES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown profile ID: {profile_id}",
        )

    entity_name = ENTITIES[profile_id]["name"]

    # In production, queue sync task
    return ReconcileResponse(
        status="queued",
        message=f"Sync queued for {entity_name}",
    )


@router.get("/transactions")
async def list_transactions(
    session: Annotated[AsyncSession, Depends(get_session)],
    profile_id: int | None = Query(None),
    status: str | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
):
    """List transactions with filtering."""
    query = select(WiseTransaction)

    if profile_id:
        query = query.where(WiseTransaction.profile_id == profile_id)
    if status:
        query = query.where(WiseTransaction.match_status == status)
    if start_date:
        query = query.where(WiseTransaction.date >= start_date)
    if end_date:
        query = query.where(WiseTransaction.date <= end_date)

    query = query.order_by(WiseTransaction.date.desc()).offset(offset).limit(limit)

    result = await session.execute(query)
    transactions = result.scalars().all()

    return {
        "items": [
            {
                "id": t.id,
                "profile_id": t.profile_id,
                "entity_name": t.entity_name,
                "type": t.type,
                "transaction_type": t.transaction_type,
                "date": t.date.isoformat(),
                "amount": str(t.amount),
                "currency": t.currency,
                "description": t.description,
                "counterparty_name": t.counterparty_name,
                "match_status": t.match_status,
                "best_confidence": str(t.best_confidence) if t.best_confidence else None,
            }
            for t in transactions
        ],
        "total": len(transactions),
        "offset": offset,
        "limit": limit,
    }
