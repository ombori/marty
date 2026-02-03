"""Qdrant vector database integration for pattern matching."""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.config import settings
from app.models.recon import WiseTransaction

logger = logging.getLogger(__name__)


@dataclass
class TransactionPattern:
    """A stored transaction pattern for similarity search."""

    id: UUID
    wise_transaction_id: str
    entity_name: str
    transaction_type: str
    counterparty: str | None
    description: str | None
    payment_reference: str | None
    amount: Decimal
    currency: str
    matched_to: str  # NetSuite transaction ID
    match_type: str
    approved_at: datetime
    embedding: list[float] | None = None

    def to_payload(self) -> dict[str, Any]:
        """Convert to Qdrant payload format."""
        return {
            "wise_transaction_id": self.wise_transaction_id,
            "entity_name": self.entity_name,
            "transaction_type": self.transaction_type,
            "counterparty": self.counterparty,
            "description": self.description,
            "payment_reference": self.payment_reference,
            "amount": float(self.amount),
            "currency": self.currency,
            "matched_to": self.matched_to,
            "match_type": self.match_type,
            "approved_at": self.approved_at.isoformat(),
        }


@dataclass
class SimilarPattern:
    """A similar pattern found via search."""

    pattern: TransactionPattern
    score: float  # Cosine similarity score


class VectorClient:
    """Qdrant vector database client for transaction patterns."""

    COLLECTION_NAME = "transaction_patterns"
    VECTOR_SIZE = 1536  # text-embedding-3-small

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        api_key: str | None = None,
        openai_api_key: str | None = None,
    ):
        """Initialize vector client.

        Args:
            host: Qdrant host
            port: Qdrant port
            api_key: Qdrant API key
            openai_api_key: OpenAI API key for embeddings
        """
        self.host = host or settings.qdrant_host
        self.port = port or settings.qdrant_port
        self.api_key = api_key or settings.qdrant_api_key
        self.openai_api_key = openai_api_key or getattr(settings, "openai_api_key", "")
        self._base_url = f"http://{self.host}:{self.port}"

    async def initialize_collection(self) -> None:
        """Create collection if it doesn't exist."""
        async with httpx.AsyncClient() as client:
            # Check if collection exists
            headers = self._get_headers()
            response = await client.get(
                f"{self._base_url}/collections/{self.COLLECTION_NAME}",
                headers=headers,
            )

            if response.status_code == 404:
                # Create collection
                await client.put(
                    f"{self._base_url}/collections/{self.COLLECTION_NAME}",
                    headers=headers,
                    json={
                        "vectors": {
                            "size": self.VECTOR_SIZE,
                            "distance": "Cosine",
                        },
                    },
                )
                logger.info(f"Created Qdrant collection: {self.COLLECTION_NAME}")

    def _get_headers(self) -> dict[str, str]:
        """Get request headers."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        return headers

    async def _get_embedding(self, text: str) -> list[float]:
        """Get embedding for text using OpenAI API."""
        if not self.openai_api_key:
            raise ValueError("OpenAI API key required for embeddings")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {self.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "text-embedding-3-small",
                    "input": text,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]

    def _build_embedding_text(self, transaction: WiseTransaction) -> str:
        """Build text for embedding from transaction."""
        parts = []

        if transaction.description:
            parts.append(transaction.description)
        if transaction.counterparty_name:
            parts.append(f"counterparty: {transaction.counterparty_name}")
        if transaction.payment_reference:
            parts.append(f"reference: {transaction.payment_reference}")
        parts.append(f"type: {transaction.transaction_type}")
        parts.append(f"amount: {transaction.amount} {transaction.currency}")

        return " | ".join(parts)

    async def store_pattern(
        self,
        transaction: WiseTransaction,
        matched_to: str,
        match_type: str,
    ) -> UUID:
        """Store a transaction pattern for future matching.

        Args:
            transaction: The matched transaction
            matched_to: NetSuite transaction ID it was matched to
            match_type: Type of match that was approved

        Returns:
            UUID of stored pattern
        """
        pattern_id = uuid4()

        # Get embedding
        embedding_text = self._build_embedding_text(transaction)
        embedding = await self._get_embedding(embedding_text)

        pattern = TransactionPattern(
            id=pattern_id,
            wise_transaction_id=transaction.id,
            entity_name=transaction.entity_name,
            transaction_type=transaction.transaction_type,
            counterparty=transaction.counterparty_name,
            description=transaction.description,
            payment_reference=transaction.payment_reference,
            amount=transaction.amount,
            currency=transaction.currency,
            matched_to=matched_to,
            match_type=match_type,
            approved_at=datetime.now(),
            embedding=embedding,
        )

        # Store in Qdrant
        async with httpx.AsyncClient() as client:
            await client.put(
                f"{self._base_url}/collections/{self.COLLECTION_NAME}/points",
                headers=self._get_headers(),
                json={
                    "points": [
                        {
                            "id": str(pattern_id),
                            "vector": embedding,
                            "payload": pattern.to_payload(),
                        }
                    ]
                },
                timeout=30.0,
            )

        logger.info(f"Stored pattern {pattern_id} for transaction {transaction.id}")
        return pattern_id

    async def find_similar(
        self,
        transaction: WiseTransaction,
        min_score: float = 0.85,
        limit: int = 5,
    ) -> list[SimilarPattern]:
        """Find similar approved patterns for a transaction.

        Args:
            transaction: Transaction to find similar patterns for
            min_score: Minimum cosine similarity score (0-1)
            limit: Maximum number of results

        Returns:
            List of similar patterns with scores
        """
        # Get embedding for query
        embedding_text = self._build_embedding_text(transaction)

        try:
            embedding = await self._get_embedding(embedding_text)
        except Exception as e:
            logger.warning(f"Failed to get embedding: {e}")
            return []

        # Search Qdrant
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/collections/{self.COLLECTION_NAME}/points/search",
                headers=self._get_headers(),
                json={
                    "vector": embedding,
                    "limit": limit,
                    "score_threshold": min_score,
                    "with_payload": True,
                },
                timeout=30.0,
            )

            if response.status_code != 200:
                logger.warning(f"Qdrant search failed: {response.text}")
                return []

            data = response.json()

        results = []
        for hit in data.get("result", []):
            payload = hit.get("payload", {})
            pattern = TransactionPattern(
                id=UUID(hit["id"]),
                wise_transaction_id=payload.get("wise_transaction_id", ""),
                entity_name=payload.get("entity_name", ""),
                transaction_type=payload.get("transaction_type", ""),
                counterparty=payload.get("counterparty"),
                description=payload.get("description"),
                payment_reference=payload.get("payment_reference"),
                amount=Decimal(str(payload.get("amount", 0))),
                currency=payload.get("currency", ""),
                matched_to=payload.get("matched_to", ""),
                match_type=payload.get("match_type", ""),
                approved_at=datetime.fromisoformat(payload.get("approved_at", "")),
            )
            results.append(SimilarPattern(pattern=pattern, score=hit["score"]))

        return results

    async def delete_pattern(self, pattern_id: UUID) -> bool:
        """Delete a pattern by ID.

        Args:
            pattern_id: Pattern UUID to delete

        Returns:
            True if deleted
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/collections/{self.COLLECTION_NAME}/points/delete",
                headers=self._get_headers(),
                json={"points": [str(pattern_id)]},
                timeout=30.0,
            )
            return response.status_code == 200


class MockVectorClient(VectorClient):
    """Mock vector client for testing."""

    def __init__(self, similar_patterns: list[SimilarPattern] | None = None):
        """Initialize mock client.

        Args:
            similar_patterns: Patterns to return from find_similar
        """
        super().__init__(host="mock", port=6333)
        self.similar_patterns = similar_patterns or []
        self.stored_patterns: list[TransactionPattern] = []

    async def initialize_collection(self) -> None:
        """No-op for mock."""
        pass

    async def _get_embedding(self, text: str) -> list[float]:  # noqa: ARG002
        """Return dummy embedding."""
        return [0.0] * self.VECTOR_SIZE

    async def store_pattern(
        self,
        transaction: WiseTransaction,
        matched_to: str,
        match_type: str,
    ) -> UUID:
        """Store pattern in memory."""
        pattern_id = uuid4()
        pattern = TransactionPattern(
            id=pattern_id,
            wise_transaction_id=transaction.id,
            entity_name=transaction.entity_name,
            transaction_type=transaction.transaction_type,
            counterparty=transaction.counterparty_name,
            description=transaction.description,
            payment_reference=transaction.payment_reference,
            amount=transaction.amount,
            currency=transaction.currency,
            matched_to=matched_to,
            match_type=match_type,
            approved_at=datetime.now(),
        )
        self.stored_patterns.append(pattern)
        return pattern_id

    async def find_similar(
        self,
        transaction: WiseTransaction,  # noqa: ARG002
        min_score: float = 0.85,  # noqa: ARG002
        limit: int = 5,
    ) -> list[SimilarPattern]:
        """Return configured similar patterns."""
        return self.similar_patterns[:limit]
