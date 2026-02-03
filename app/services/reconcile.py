"""Reconciliation orchestrator - main workflow coordination."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ENTITIES
from app.models.recon import WiseTransaction as WiseTransactionModel
from app.services.cache import CacheClient
from app.services.learning import PatternLearner
from app.services.matching import ConfidenceScorer, ExactMatcher, FuzzyMatcher, IntercompanyDetector
from app.services.matching.confidence import MatchResult, MatchType
from app.services.matching.llm import LLMMatcher
from app.services.spectre import GLEntry, SpectreClient
from app.services.sync import TransactionSyncService
from app.services.vectors import VectorClient
from app.services.wise import WiseClient

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationResult:
    """Result of a reconciliation run."""

    entity_name: str
    start_date: datetime
    end_date: datetime
    transactions_processed: int = 0
    exact_matches: int = 0
    fuzzy_matches: int = 0
    llm_matches: int = 0
    pattern_matches: int = 0
    unmatched: int = 0
    auto_approved: int = 0
    submitted_for_review: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


class ReconciliationOrchestrator:
    """Orchestrates the full reconciliation workflow.

    Flow:
    1. Fetch transactions from Wise
    2. Get GL entries from Spectre
    3. Detect intercompany transfers
    4. Run matching pipeline (exact -> fuzzy -> LLM -> pattern)
    5. Submit suggestions to Spectre
    6. Notify via Slack
    """

    def __init__(
        self,
        session: AsyncSession,
        wise_client: WiseClient,
        spectre_client: SpectreClient,
        cache_client: CacheClient,
        vector_client: VectorClient,
        llm_enabled: bool = True,
    ):
        """Initialize orchestrator.

        Args:
            session: Database session
            wise_client: Wise API client
            spectre_client: Spectre API client
            cache_client: Redis cache client
            vector_client: Qdrant vector client
            llm_enabled: Whether to use LLM matching
        """
        self.session = session
        self.wise_client = wise_client
        self.spectre_client = spectre_client
        self.cache_client = cache_client
        self.vector_client = vector_client
        self.llm_enabled = llm_enabled

        # Initialize sub-components
        self.sync_service = TransactionSyncService(session, wise_client)
        self.exact_matcher = ExactMatcher()
        self.fuzzy_matcher = FuzzyMatcher()
        self.llm_matcher = LLMMatcher() if llm_enabled else None
        self.ic_detector = IntercompanyDetector()
        self.confidence_scorer = ConfidenceScorer()
        self.pattern_learner = PatternLearner(vector_client, spectre_client)

    async def reconcile_entity(
        self,
        profile_id: int,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        fetch_transactions: bool = True,
    ) -> ReconciliationResult:
        """Run reconciliation for a single entity.

        Args:
            profile_id: Wise profile ID
            start_date: Start of period (defaults to 30 days ago)
            end_date: End of period (defaults to now)
            fetch_transactions: Whether to fetch new transactions from Wise

        Returns:
            ReconciliationResult with statistics
        """
        start_time = datetime.now(UTC)
        entity_info = ENTITIES.get(profile_id, {})
        entity_name = entity_info.get("name", f"Profile {profile_id}")

        end_date = end_date or datetime.now(UTC)
        start_date = start_date or (end_date - timedelta(days=30))

        result = ReconciliationResult(
            entity_name=entity_name,
            start_date=start_date,
            end_date=end_date,
        )

        try:
            # Step 1: Fetch transactions from Wise
            if fetch_transactions:
                logger.info(f"Fetching transactions for {entity_name}")
                await self.sync_service.sync_profile(
                    profile_id=profile_id,
                    start_date=start_date,
                    end_date=end_date,
                )

            # Step 2: Get unmatched transactions from database
            transactions = await self._get_pending_transactions(profile_id, start_date, end_date)
            result.transactions_processed = len(transactions)
            logger.info(f"Processing {len(transactions)} transactions for {entity_name}")

            if not transactions:
                return result

            # Step 3: Get GL entries from Spectre
            subsidiary_id = await self._get_subsidiary_id(profile_id)
            gl_entries = await self._get_gl_entries(subsidiary_id, start_date, end_date)
            logger.info(f"Loaded {len(gl_entries)} GL entries for matching")

            # Step 4: Get patterns from Spectre
            patterns = await self._get_patterns()

            # Step 5: Process each transaction
            for transaction in transactions:
                try:
                    match_result = await self._process_transaction(
                        transaction, gl_entries, patterns
                    )
                    await self._handle_match_result(transaction, match_result, result)
                except Exception as e:
                    logger.error(f"Error processing {transaction.id}: {e}")
                    result.errors.append(f"{transaction.id}: {e}")

            # Commit changes
            await self.session.commit()

        except Exception as e:
            logger.error(f"Reconciliation failed for {entity_name}: {e}")
            result.errors.append(str(e))

        result.duration_seconds = (datetime.now(UTC) - start_time).total_seconds()
        return result

    async def reconcile_all_entities(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[int, ReconciliationResult]:
        """Run reconciliation for all configured entities.

        Returns:
            Dict mapping profile_id to ReconciliationResult
        """
        results = {}
        for profile_id in ENTITIES:
            try:
                result = await self.reconcile_entity(
                    profile_id=profile_id,
                    start_date=start_date,
                    end_date=end_date,
                )
                results[profile_id] = result
            except Exception as e:
                logger.error(f"Failed to reconcile profile {profile_id}: {e}")
                results[profile_id] = ReconciliationResult(
                    entity_name=ENTITIES[profile_id]["name"],
                    start_date=start_date or datetime.now(UTC),
                    end_date=end_date or datetime.now(UTC),
                    errors=[str(e)],
                )
        return results

    async def _get_pending_transactions(
        self,
        profile_id: int,
        start_date: datetime,
        end_date: datetime,
    ) -> list[WiseTransactionModel]:
        """Get transactions pending reconciliation."""
        result = await self.session.execute(
            select(WiseTransactionModel)
            .where(
                WiseTransactionModel.profile_id == profile_id,
                WiseTransactionModel.date >= start_date,
                WiseTransactionModel.date <= end_date,
                WiseTransactionModel.match_status == "pending",
            )
            .order_by(WiseTransactionModel.date)
        )
        return list(result.scalars().all())

    async def _get_subsidiary_id(self, profile_id: int) -> int:
        """Get NetSuite subsidiary ID for a profile."""
        # Try cache first
        entity = await self.cache_client.get_entity(profile_id)
        if entity and entity.netsuite_subsidiary_id:
            return entity.netsuite_subsidiary_id

        # Fallback to profile_id (should be configured properly)
        return profile_id

    async def _get_gl_entries(
        self,
        subsidiary_id: int,
        start_date: datetime,
        end_date: datetime,
    ) -> list[GLEntry]:
        """Get GL entries from cache or Spectre."""
        # Try cache first
        cached = await self.cache_client.get_gl_entries(subsidiary_id, start_date, end_date)
        if cached:
            return [
                GLEntry(
                    transaction_id=e["transaction_id"],
                    line_id=e["line_id"],
                    transaction_type=e["transaction_type"],
                    date=datetime.fromisoformat(e["date"]),
                    amount=Decimal(e["amount"]),
                    currency=e["currency"],
                    account_id=e["account_id"],
                    account_name=e["account_name"],
                    entity_id=e["entity_id"],
                    entity_name=e["entity_name"],
                    memo=e.get("memo"),
                )
                for e in cached
            ]

        # Fetch from Spectre
        entries = await self.spectre_client.get_gl_entries(
            subsidiary_id=subsidiary_id,
            start_date=start_date,
            end_date=end_date,
            unreconciled_only=True,
        )

        # Cache the results
        cache_data = [
            {
                "transaction_id": e.transaction_id,
                "line_id": e.line_id,
                "transaction_type": e.transaction_type,
                "date": e.date.isoformat(),
                "amount": str(e.amount),
                "currency": e.currency,
                "account_id": e.account_id,
                "account_name": e.account_name,
                "entity_id": e.entity_id,
                "entity_name": e.entity_name,
                "memo": e.memo,
            }
            for e in entries
        ]
        await self.cache_client.set_gl_entries(subsidiary_id, start_date, end_date, cache_data)

        return entries

    async def _get_patterns(self) -> list[dict[str, Any]]:
        """Get reconciliation patterns from Spectre."""
        try:
            patterns = await self.spectre_client.get_patterns(active_only=True)
            return [
                {
                    "id": str(p.id),
                    "pattern_type": p.pattern_type,
                    "pattern_value": p.pattern_value,
                    "is_regex": p.is_regex,
                    "target_type": p.target_type,
                    "target_netsuite_id": p.target_netsuite_id,
                    "target_name": p.target_name,
                    "is_auto_approve": p.is_auto_approve,
                    "confidence_boost": p.confidence_boost,
                }
                for p in patterns
            ]
        except Exception as e:
            logger.warning(f"Failed to fetch patterns: {e}")
            return []

    async def _process_transaction(
        self,
        transaction: WiseTransactionModel,
        gl_entries: list[GLEntry],
        patterns: list[dict[str, Any]],
    ) -> MatchResult:
        """Process a single transaction through the matching pipeline."""
        # Step 1: Detect intercompany
        ic_result = self.ic_detector.detect(transaction)

        # Step 2: Try exact match
        match_result = self.exact_matcher.match(transaction, gl_entries, patterns)
        if match_result and match_result.confidence >= Decimal("0.90"):
            match_result.is_intercompany = ic_result.is_intercompany
            match_result.counterparty_entity = ic_result.counterparty_entity
            return await self._apply_pattern_boost(transaction, match_result)

        # Step 3: Try fuzzy match
        fuzzy_result = self.fuzzy_matcher.match(transaction, gl_entries)
        if fuzzy_result and fuzzy_result.confidence >= Decimal("0.70"):
            fuzzy_result.is_intercompany = ic_result.is_intercompany
            fuzzy_result.counterparty_entity = ic_result.counterparty_entity
            return await self._apply_pattern_boost(transaction, fuzzy_result)

        # Step 4: Try LLM match
        if self.llm_matcher and gl_entries:
            llm_result = await self.llm_matcher.match(transaction, gl_entries)
            if llm_result and llm_result.confidence >= Decimal("0.50"):
                llm_result.is_intercompany = ic_result.is_intercompany
                llm_result.counterparty_entity = ic_result.counterparty_entity
                return await self._apply_pattern_boost(transaction, llm_result)

        # No match found
        return MatchResult(
            match_type=MatchType.UNMATCHED,
            confidence=Decimal("0.00"),
            reasons=["no_match_found"],
            is_intercompany=ic_result.is_intercompany,
            counterparty_entity=ic_result.counterparty_entity,
        )

    async def _apply_pattern_boost(
        self,
        transaction: WiseTransactionModel,
        match_result: MatchResult,
    ) -> MatchResult:
        """Apply confidence boost from pattern matching."""
        try:
            boost, similar_patterns = await self.pattern_learner.get_pattern_boost(transaction)
            if boost > Decimal("0.00"):
                match_result.confidence = min(Decimal("1.00"), match_result.confidence + boost)
                match_result.reasons.append(
                    f"pattern_boost:{boost} ({len(similar_patterns)} similar)"
                )
        except Exception as e:
            logger.warning(f"Pattern boost failed: {e}")

        return match_result

    async def _handle_match_result(
        self,
        transaction: WiseTransactionModel,
        match_result: MatchResult,
        stats: ReconciliationResult,
    ) -> None:
        """Handle the result of matching and submit to Spectre."""
        # Update statistics
        if match_result.match_type == MatchType.EXACT_ALL or match_result.match_type in (
            MatchType.EXACT_AMOUNT_REF,
            MatchType.EXACT_AMOUNT_DATE,
        ):
            stats.exact_matches += 1
        elif match_result.match_type in (MatchType.FUZZY_HIGH, MatchType.FUZZY_MEDIUM):
            stats.fuzzy_matches += 1
        elif match_result.match_type in (
            MatchType.LLM_CONFIDENT,
            MatchType.LLM_UNCERTAIN,
        ):
            stats.llm_matches += 1
        elif match_result.match_type == MatchType.PATTERN:
            stats.pattern_matches += 1
        else:
            stats.unmatched += 1

        # Determine action
        action = self.confidence_scorer.get_action(match_result.confidence)

        if action == "auto_approve":
            stats.auto_approved += 1
        elif action in ("suggest", "review"):
            stats.submitted_for_review += 1

        # Submit to Spectre
        try:
            response = await self.spectre_client.submit_suggestion(
                wise_transaction_id=transaction.id,
                wise_profile_id=transaction.profile_id,
                entity_name=transaction.entity_name,
                transaction_date=transaction.date,
                amount=transaction.amount,
                currency=transaction.currency,
                transaction_type=transaction.transaction_type,
                match_type=match_result.match_type.value,
                confidence_score=match_result.confidence,
                description=transaction.description,
                counterparty=transaction.counterparty_name,
                match_explanation=match_result.explanation,
                match_reasons=match_result.reasons,
                netsuite_transaction_id=match_result.netsuite_transaction_id,
                netsuite_line_id=match_result.netsuite_line_id,
                netsuite_type=match_result.netsuite_type,
                suggested_account_id=match_result.suggested_account_id,
                suggested_account_name=match_result.suggested_account_name,
                is_intercompany=match_result.is_intercompany,
                counterparty_entity=match_result.counterparty_entity,
            )

            # Update transaction status
            await self.session.execute(
                update(WiseTransactionModel)
                .where(WiseTransactionModel.id == transaction.id)
                .values(
                    match_status="submitted",
                    spectre_suggestion_id=response.id,
                    last_match_attempt=datetime.now(UTC),
                    match_attempts=transaction.match_attempts + 1,
                    best_confidence=match_result.confidence,
                )
            )
        except Exception as e:
            logger.error(f"Failed to submit suggestion for {transaction.id}: {e}")
            stats.errors.append(f"Submit failed for {transaction.id}: {e}")
