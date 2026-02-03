"""Intercompany transfer detection."""

import logging
import re
from dataclasses import dataclass

from app.config import ENTITIES, ENTITY_NAME_TO_PROFILE
from app.models.recon import WiseTransaction

logger = logging.getLogger(__name__)


@dataclass
class ICDetectionResult:
    """Result of intercompany detection."""

    is_intercompany: bool
    counterparty_entity: str | None = None
    counterparty_profile_id: int | None = None
    detection_method: str | None = None
    confidence: float = 0.0


class IntercompanyDetector:
    """Detects intercompany transfers between group entities.

    Detection methods:
    1. Counterparty name matches entity list (normalized)
    2. Counterparty IBAN in known entity bank accounts
    3. Payment reference contains "IC" or entity name
    """

    # Known entity bank IBANs (should be loaded from config/cache in production)
    # Format: IBAN -> (entity_name, profile_id)
    KNOWN_ENTITY_IBANS: dict[str, tuple[str, int]] = {}

    # IC indicators in payment reference (must be standalone words/phrases)
    IC_INDICATORS = [r"\bIC\b", r"\bINTERCOMPANY\b", r"\bINTER-COMPANY\b", r"\bI/C\b"]

    def __init__(self, entity_ibans: dict[str, tuple[str, int]] | None = None):
        """Initialize detector.

        Args:
            entity_ibans: Dict mapping IBAN to (entity_name, profile_id)
        """
        if entity_ibans:
            self.KNOWN_ENTITY_IBANS = entity_ibans

        # Build normalized entity name patterns
        self._entity_patterns = self._build_entity_patterns()

    def _build_entity_patterns(self) -> list[tuple[re.Pattern, str, int]]:
        """Build regex patterns for entity name matching."""
        patterns = []
        for profile_id, info in ENTITIES.items():
            name = info["name"]
            # Create variations
            variations = [
                name,
                name.replace(" ", ""),
                name.replace(".", ""),
                name.replace(",", ""),
            ]
            # Add common abbreviations
            if "Limited" in name:
                variations.append(name.replace("Limited", "Ltd"))
            if "Inc" in name:
                variations.append(name.replace("Inc", "Incorporated"))

            # Build regex pattern (case insensitive)
            escaped = [re.escape(v) for v in variations]
            pattern = re.compile("|".join(escaped), re.IGNORECASE)
            patterns.append((pattern, name, profile_id))

        return patterns

    def detect(self, transaction: WiseTransaction) -> ICDetectionResult:
        """Detect if a transaction is intercompany.

        Args:
            transaction: Wise transaction to analyze

        Returns:
            ICDetectionResult with detection details
        """
        # Method 1: Check counterparty name
        result = self._check_counterparty_name(transaction)
        if result.is_intercompany:
            return result

        # Method 2: Check counterparty IBAN
        result = self._check_counterparty_iban(transaction)
        if result.is_intercompany:
            return result

        # Method 3: Check payment reference
        result = self._check_payment_reference(transaction)
        if result.is_intercompany:
            return result

        return ICDetectionResult(is_intercompany=False)

    def _check_counterparty_name(self, transaction: WiseTransaction) -> ICDetectionResult:
        """Check if counterparty name matches a group entity."""
        if not transaction.counterparty_name:
            return ICDetectionResult(is_intercompany=False)

        counterparty = transaction.counterparty_name.strip()

        # Check against normalized entity names
        normalized = counterparty.lower()
        if normalized in ENTITY_NAME_TO_PROFILE:
            profile_id = ENTITY_NAME_TO_PROFILE[normalized]
            return ICDetectionResult(
                is_intercompany=True,
                counterparty_entity=ENTITIES[profile_id]["name"],
                counterparty_profile_id=profile_id,
                detection_method="counterparty_name_exact",
                confidence=1.0,
            )

        # Check against patterns (fuzzy match)
        for pattern, entity_name, profile_id in self._entity_patterns:
            if pattern.search(counterparty):
                return ICDetectionResult(
                    is_intercompany=True,
                    counterparty_entity=entity_name,
                    counterparty_profile_id=profile_id,
                    detection_method="counterparty_name_pattern",
                    confidence=0.9,
                )

        return ICDetectionResult(is_intercompany=False)

    def _check_counterparty_iban(self, transaction: WiseTransaction) -> ICDetectionResult:
        """Check if counterparty IBAN belongs to a group entity."""
        if not transaction.counterparty_account:
            return ICDetectionResult(is_intercompany=False)

        # Normalize IBAN
        iban = transaction.counterparty_account.replace(" ", "").upper()

        if iban in self.KNOWN_ENTITY_IBANS:
            entity_name, profile_id = self.KNOWN_ENTITY_IBANS[iban]
            return ICDetectionResult(
                is_intercompany=True,
                counterparty_entity=entity_name,
                counterparty_profile_id=profile_id,
                detection_method="counterparty_iban",
                confidence=1.0,
            )

        return ICDetectionResult(is_intercompany=False)

    def _check_payment_reference(self, transaction: WiseTransaction) -> ICDetectionResult:
        """Check if payment reference indicates IC transfer."""
        if not transaction.payment_reference:
            return ICDetectionResult(is_intercompany=False)

        ref = transaction.payment_reference.upper()

        # Check for IC indicators (using regex for word boundaries)
        for indicator in self.IC_INDICATORS:
            if re.search(indicator, ref):
                # Try to identify the counterparty entity from reference
                entity_name, profile_id = self._extract_entity_from_reference(ref)
                return ICDetectionResult(
                    is_intercompany=True,
                    counterparty_entity=entity_name,
                    counterparty_profile_id=profile_id,
                    detection_method="payment_reference_ic_indicator",
                    confidence=0.8 if entity_name else 0.6,
                )

        # Check if reference contains entity name
        for pattern, entity_name, profile_id in self._entity_patterns:
            if pattern.search(ref):
                return ICDetectionResult(
                    is_intercompany=True,
                    counterparty_entity=entity_name,
                    counterparty_profile_id=profile_id,
                    detection_method="payment_reference_entity_name",
                    confidence=0.85,
                )

        return ICDetectionResult(is_intercompany=False)

    def _extract_entity_from_reference(self, reference: str) -> tuple[str | None, int | None]:
        """Try to extract entity name from payment reference."""
        for pattern, entity_name, profile_id in self._entity_patterns:
            if pattern.search(reference):
                return entity_name, profile_id
        return None, None

    def get_ic_account_pattern(self, entity_name: str) -> str:
        """Get the expected IC account pattern for an entity.

        In NetSuite, IC accounts typically follow pattern:
        1563 {Entity Name} - C/A

        Args:
            entity_name: Name of the counterparty entity

        Returns:
            Expected account name pattern
        """
        # Normalize entity name for account lookup
        short_name = entity_name.replace("Limited", "Ltd").replace(".", "").strip()
        return f"1563 {short_name} - C/A"

    def register_entity_iban(self, iban: str, entity_name: str, profile_id: int) -> None:
        """Register an IBAN as belonging to a group entity.

        Args:
            iban: Bank IBAN (will be normalized)
            entity_name: Entity name
            profile_id: Wise profile ID
        """
        normalized = iban.replace(" ", "").upper()
        self.KNOWN_ENTITY_IBANS[normalized] = (entity_name, profile_id)
