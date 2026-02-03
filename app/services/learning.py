"""Pattern learning from approved matches."""

import logging
import re
from dataclasses import dataclass
from decimal import Decimal

from app.models.recon import WiseTransaction
from app.services.spectre import SpectreClient
from app.services.vectors import SimilarPattern, VectorClient

logger = logging.getLogger(__name__)


@dataclass
class LearnedPattern:
    """A pattern learned from an approved match."""

    pattern_type: str  # counterparty, reference, description
    pattern_value: str
    is_regex: bool
    target_type: str  # vendor, customer, account
    target_netsuite_id: str
    target_name: str
    confidence_boost: Decimal


class PatternLearner:
    """Learns patterns from approved transaction matches.

    When a match is approved, the learner:
    1. Stores the transaction embedding in Qdrant for similarity search
    2. Extracts explicit patterns (counterparty, reference) for rule-based matching
    3. Submits new patterns to Spectre for storage
    """

    # Minimum approval rate before auto-creating pattern
    MIN_APPROVAL_RATE = 0.8

    # Confidence boost for pattern matches based on approval count
    CONFIDENCE_BOOSTS = {
        1: Decimal("0.10"),
        5: Decimal("0.15"),
        10: Decimal("0.20"),
        20: Decimal("0.25"),
    }

    def __init__(
        self,
        vector_client: VectorClient,
        spectre_client: SpectreClient | None = None,
    ):
        """Initialize pattern learner.

        Args:
            vector_client: Qdrant client for storing embeddings
            spectre_client: Spectre client for submitting patterns
        """
        self.vector_client = vector_client
        self.spectre_client = spectre_client

    async def learn_from_approval(
        self,
        transaction: WiseTransaction,
        matched_netsuite_id: str,
        matched_account_id: int,
        matched_account_name: str,
        match_type: str,
    ) -> list[LearnedPattern]:
        """Learn patterns from an approved match.

        Args:
            transaction: The approved transaction
            matched_netsuite_id: NetSuite transaction ID it was matched to
            matched_account_id: NetSuite account ID
            matched_account_name: NetSuite account name
            match_type: Type of match that was approved

        Returns:
            List of patterns that were learned
        """
        learned = []

        # 1. Store in vector database for similarity search
        try:
            await self.vector_client.store_pattern(
                transaction=transaction,
                matched_to=matched_netsuite_id,
                match_type=match_type,
            )
            logger.info(f"Stored pattern embedding for {transaction.id}")
        except Exception as e:
            logger.error(f"Failed to store pattern embedding: {e}")

        # 2. Extract explicit patterns
        patterns = self._extract_patterns(
            transaction=transaction,
            matched_account_id=matched_account_id,
            matched_account_name=matched_account_name,
        )

        # 3. Submit to Spectre
        if self.spectre_client and patterns:
            for pattern in patterns:
                try:
                    async with self.spectre_client as client:
                        await client.submit_pattern(
                            pattern_type=pattern.pattern_type,
                            pattern_value=pattern.pattern_value,
                            target_type=pattern.target_type,
                            target_netsuite_id=pattern.target_netsuite_id,
                            target_name=pattern.target_name,
                            is_regex=pattern.is_regex,
                            description=f"Learned from {transaction.id}",
                        )
                    learned.append(pattern)
                    logger.info(
                        f"Submitted pattern: {pattern.pattern_type}={pattern.pattern_value}"
                    )
                except Exception as e:
                    logger.error(f"Failed to submit pattern: {e}")

        return learned

    def _extract_patterns(
        self,
        transaction: WiseTransaction,
        matched_account_id: int,
        matched_account_name: str,
    ) -> list[LearnedPattern]:
        """Extract learnable patterns from a transaction."""
        patterns = []

        # Counterparty pattern
        if transaction.counterparty_name:
            counterparty = transaction.counterparty_name.strip()
            if len(counterparty) >= 3:  # Minimum useful length
                patterns.append(
                    LearnedPattern(
                        pattern_type="counterparty",
                        pattern_value=counterparty,
                        is_regex=False,
                        target_type="account",
                        target_netsuite_id=str(matched_account_id),
                        target_name=matched_account_name,
                        confidence_boost=Decimal("0.15"),
                    )
                )

        # Reference pattern (if contains recognizable structure)
        if transaction.payment_reference:
            ref_pattern = self._extract_reference_pattern(transaction.payment_reference)
            if ref_pattern:
                patterns.append(
                    LearnedPattern(
                        pattern_type="reference",
                        pattern_value=ref_pattern,
                        is_regex=True,
                        target_type="account",
                        target_netsuite_id=str(matched_account_id),
                        target_name=matched_account_name,
                        confidence_boost=Decimal("0.20"),
                    )
                )

        # Merchant pattern (for card transactions)
        if transaction.merchant_name:
            patterns.append(
                LearnedPattern(
                    pattern_type="counterparty",
                    pattern_value=transaction.merchant_name,
                    is_regex=False,
                    target_type="account",
                    target_netsuite_id=str(matched_account_id),
                    target_name=matched_account_name,
                    confidence_boost=Decimal("0.15"),
                )
            )

        return patterns

    def _extract_reference_pattern(self, reference: str) -> str | None:
        """Extract a regex pattern from a payment reference.

        Looks for common reference formats:
        - INV-2024-001
        - Invoice #12345
        - PO/2024/001
        """
        # Common patterns to extract
        patterns_to_match = [
            # INV-YYYY-NNN or INV/YYYY/NNN
            (r"(INV[-/]\d{4}[-/]\d+)", r"INV[-/]\\d{4}[-/]\\d+"),
            # PO-YYYY-NNN
            (r"(PO[-/]\d{4}[-/]\d+)", r"PO[-/]\\d{4}[-/]\\d+"),
            # Invoice #NNN
            (r"(Invoice\s*#?\s*\d+)", r"Invoice\\s*#?\\s*\\d+"),
            # Bill #NNN
            (r"(Bill\s*#?\s*\d+)", r"Bill\\s*#?\\s*\\d+"),
        ]

        for match_pattern, regex_output in patterns_to_match:
            if re.search(match_pattern, reference, re.IGNORECASE):
                return regex_output

        return None

    async def get_pattern_boost(
        self,
        transaction: WiseTransaction,
    ) -> tuple[Decimal, list[SimilarPattern]]:
        """Get confidence boost from pattern matches.

        Args:
            transaction: Transaction to check

        Returns:
            Tuple of (confidence_boost, matching_patterns)
        """
        try:
            similar = await self.vector_client.find_similar(
                transaction=transaction,
                min_score=0.85,
                limit=5,
            )
        except Exception as e:
            logger.warning(f"Pattern search failed: {e}")
            return Decimal("0.00"), []

        if not similar:
            return Decimal("0.00"), []

        # Calculate boost based on number of similar patterns
        count = len(similar)
        boost = Decimal("0.10")  # Base boost

        for threshold, value in sorted(self.CONFIDENCE_BOOSTS.items()):
            if count >= threshold:
                boost = value

        # Increase boost based on similarity score
        avg_score = sum(p.score for p in similar) / len(similar)
        if avg_score >= 0.95:
            boost += Decimal("0.05")
        elif avg_score >= 0.90:
            boost += Decimal("0.02")

        return boost, similar

    def get_confidence_boost_for_count(self, approval_count: int) -> Decimal:
        """Get confidence boost based on approval count.

        Args:
            approval_count: Number of times pattern was approved

        Returns:
            Confidence boost value
        """
        boost = Decimal("0.10")
        for threshold, value in sorted(self.CONFIDENCE_BOOSTS.items()):
            if approval_count >= threshold:
                boost = value
        return boost
