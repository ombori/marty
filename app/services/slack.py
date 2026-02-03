"""Slack notification service using Slack App API."""

import logging
from dataclasses import dataclass
from decimal import Decimal

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.config import settings
from app.services.reconcile import ReconciliationResult

logger = logging.getLogger(__name__)


@dataclass
class SlackMessage:
    """A Slack message to send."""

    text: str
    blocks: list[dict] | None = None
    channel: str | None = None


class SlackNotifier:
    """Sends Slack notifications for reconciliation events.

    Notification types:
    1. Daily digest - Summary of pending approvals
    2. Discrepancy alert - When unmatched transactions exceed threshold
    3. Reconciliation complete - After batch finishes
    """

    def __init__(
        self,
        bot_token: str | None = None,
        channel: str | None = None,
        spectre_base_url: str = "https://spectre.example.com",
    ):
        """Initialize Slack notifier.

        Args:
            bot_token: Slack bot token (xoxb-...)
            channel: Default channel (can be overridden per message)
            spectre_base_url: Base URL for Spectre links
        """
        self.bot_token = bot_token or settings.slack_bot_token
        self.channel = channel or settings.slack_channel
        self.spectre_base_url = spectre_base_url
        self._client: AsyncWebClient | None = None

    @property
    def client(self) -> AsyncWebClient:
        """Get or create Slack client."""
        if self._client is None:
            self._client = AsyncWebClient(token=self.bot_token)
        return self._client

    async def send_message(self, message: SlackMessage) -> bool:
        """Send a message to Slack.

        Args:
            message: SlackMessage to send

        Returns:
            True if successful
        """
        if not self.bot_token:
            logger.warning("Slack bot token not configured")
            return False

        channel = message.channel or self.channel

        try:
            if message.blocks:
                await self.client.chat_postMessage(
                    channel=channel,
                    text=message.text,
                    blocks=message.blocks,
                )
            else:
                await self.client.chat_postMessage(
                    channel=channel,
                    text=message.text,
                )
            return True
        except SlackApiError as e:
            logger.error(f"Failed to send Slack message: {e.response['error']}")
            return False
        except Exception as e:
            logger.error(f"Failed to send Slack message: {e}")
            return False

    async def send_daily_digest(
        self,
        pending_count: int,
        pending_amount: Decimal,
        by_entity: dict[str, int],
    ) -> bool:
        """Send daily digest of pending approvals.

        Args:
            pending_count: Total pending items
            pending_amount: Total amount pending
            by_entity: Count by entity name

        Returns:
            True if successful
        """
        # Build entity breakdown
        entity_lines = []
        for entity, count in sorted(by_entity.items(), key=lambda x: -x[1]):
            entity_lines.append(f"• {entity}: {count}")
        entity_text = "\n".join(entity_lines) if entity_lines else "No pending items"

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Daily Reconciliation Digest",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Pending Approvals:*\n{pending_count}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Total Amount:*\n{pending_amount:,.2f}",
                    },
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*By Entity:*\n{entity_text}",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Review Pending",
                            "emoji": True,
                        },
                        "url": f"{self.spectre_base_url}/reconciliation?status=pending",
                        "style": "primary",
                    }
                ],
            },
        ]

        message = SlackMessage(
            text=f"Daily Digest: {pending_count} items pending approval ({pending_amount:,.2f})",
            blocks=blocks,
        )
        return await self.send_message(message)

    async def send_discrepancy_alert(
        self,
        entity_name: str,
        unmatched_count: int,
        large_transactions: list[dict],
        threshold_exceeded: bool = False,
    ) -> bool:
        """Send alert for discrepancies.

        Args:
            entity_name: Entity with discrepancies
            unmatched_count: Number of unmatched transactions
            large_transactions: List of large unmatched transactions
            threshold_exceeded: Whether this exceeded configured threshold

        Returns:
            True if successful
        """
        # Build large transaction list
        tx_lines = []
        for tx in large_transactions[:5]:  # Limit to 5
            tx_lines.append(
                f"• {tx.get('date', 'N/A')} | {tx.get('amount', 0):,.2f} {tx.get('currency', '')} | {tx.get('counterparty', 'Unknown')}"
            )
        tx_text = "\n".join(tx_lines) if tx_lines else "None"

        urgency = "HIGH" if threshold_exceeded else "ATTENTION"

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Discrepancy Alert - {entity_name}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{urgency}:* {unmatched_count} transactions could not be matched automatically.",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Largest Unmatched Transactions:*\n{tx_text}",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Review Now",
                            "emoji": True,
                        },
                        "url": f"{self.spectre_base_url}/reconciliation?entity={entity_name}&status=unmatched",
                        "style": "danger" if threshold_exceeded else "primary",
                    }
                ],
            },
        ]

        message = SlackMessage(
            text=f"Discrepancy Alert: {unmatched_count} unmatched transactions for {entity_name}",
            blocks=blocks,
        )
        return await self.send_message(message)

    async def send_reconciliation_complete(
        self,
        result: ReconciliationResult,
    ) -> bool:
        """Send notification when reconciliation batch completes.

        Args:
            result: ReconciliationResult with statistics

        Returns:
            True if successful
        """
        total = result.transactions_processed
        matched = result.exact_matches + result.fuzzy_matches + result.llm_matches
        match_rate = (matched / total * 100) if total > 0 else 0

        status_text = "Complete" if result.unmatched == 0 else "Needs Review"
        if result.errors:
            status_text = "Completed with Errors"

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Reconciliation {status_text} - {result.entity_name}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Period:*\n{result.start_date.date()} to {result.end_date.date()}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Duration:*\n{result.duration_seconds:.1f}s",
                    },
                ],
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Processed:*\n{total}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Match Rate:*\n{match_rate:.1f}%",
                    },
                ],
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Exact:* {result.exact_matches}\n*Fuzzy:* {result.fuzzy_matches}\n*LLM:* {result.llm_matches}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Auto-approved:* {result.auto_approved}\n*For Review:* {result.submitted_for_review}\n*Unmatched:* {result.unmatched}",
                    },
                ],
            },
        ]

        if result.errors:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Errors:* {len(result.errors)}\n`{result.errors[0][:100]}...`"
                        if len(result.errors[0]) > 100
                        else f"*Errors:* {len(result.errors)}\n`{result.errors[0]}`",
                    },
                }
            )

        if result.submitted_for_review > 0:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": f"Review {result.submitted_for_review} Items",
                                "emoji": True,
                            },
                            "url": f"{self.spectre_base_url}/reconciliation?entity={result.entity_name}&status=pending",
                            "style": "primary",
                        }
                    ],
                }
            )

        message = SlackMessage(
            text=f"Reconciliation complete for {result.entity_name}: {matched}/{total} matched ({match_rate:.1f}%)",
            blocks=blocks,
        )
        return await self.send_message(message)

    async def send_error_alert(
        self,
        entity_name: str,
        error_message: str,
        context: str | None = None,
    ) -> bool:
        """Send error alert.

        Args:
            entity_name: Entity that had the error
            error_message: Error description
            context: Additional context

        Returns:
            True if successful
        """
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Reconciliation Error - {entity_name}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Error:*\n```{error_message[:500]}```",
                },
            },
        ]

        if context:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Context:*\n{context}",
                    },
                }
            )

        message = SlackMessage(
            text=f"Reconciliation error for {entity_name}: {error_message[:100]}",
            blocks=blocks,
        )
        return await self.send_message(message)


class MockSlackNotifier(SlackNotifier):
    """Mock Slack notifier for testing."""

    def __init__(self):
        """Initialize mock notifier."""
        super().__init__(bot_token="xoxb-mock-token")
        self.messages: list[SlackMessage] = []

    async def send_message(self, message: SlackMessage) -> bool:
        """Store message instead of sending."""
        self.messages.append(message)
        return True
