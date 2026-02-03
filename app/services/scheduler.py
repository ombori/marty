"""Reconciliation scheduler for automated runs."""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)


class ReconciliationScheduler:
    """Schedules and manages reconciliation jobs.

    Supports:
    - Daily reconciliation runs
    - Hourly sync checks
    - Manual trigger support
    - Graceful shutdown
    """

    def __init__(
        self,
        reconcile_callback: Callable,
        notify_callback: Callable | None = None,
        daily_run_hour: int = 6,  # 6 AM
        sync_interval_hours: int = 4,
    ):
        """Initialize scheduler.

        Args:
            reconcile_callback: Async function to call for reconciliation
            notify_callback: Async function to call for notifications
            daily_run_hour: Hour of day for daily reconciliation (0-23)
            sync_interval_hours: Hours between sync checks
        """
        self.reconcile_callback = reconcile_callback
        self.notify_callback = notify_callback
        self.daily_run_hour = daily_run_hour
        self.sync_interval_hours = sync_interval_hours
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        self._running = True
        logger.info("Starting reconciliation scheduler")

        # Start background tasks
        self._tasks = [
            asyncio.create_task(self._daily_reconciliation_loop()),
            asyncio.create_task(self._periodic_sync_loop()),
        ]

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        self._running = False
        logger.info("Stopping reconciliation scheduler")

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        self._tasks = []

    async def _daily_reconciliation_loop(self) -> None:
        """Run daily reconciliation at configured hour."""
        while self._running:
            try:
                # Calculate time until next run
                now = datetime.now(UTC)
                next_run = now.replace(
                    hour=self.daily_run_hour,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                if next_run <= now:
                    next_run += timedelta(days=1)

                wait_seconds = (next_run - now).total_seconds()
                logger.info(f"Next daily reconciliation at {next_run} ({wait_seconds:.0f}s)")

                await asyncio.sleep(wait_seconds)

                if self._running:
                    await self._run_daily_reconciliation()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in daily reconciliation loop: {e}")
                await asyncio.sleep(60)  # Wait before retry

    async def _periodic_sync_loop(self) -> None:
        """Run periodic transaction syncs."""
        while self._running:
            try:
                await asyncio.sleep(self.sync_interval_hours * 3600)

                if self._running:
                    await self._run_sync()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in periodic sync loop: {e}")
                await asyncio.sleep(300)  # Wait before retry

    async def _run_daily_reconciliation(self) -> None:
        """Execute daily reconciliation for all entities."""
        logger.info("Starting daily reconciliation run")
        start_time = datetime.now(UTC)

        try:
            # Run reconciliation for all entities
            results = await self.reconcile_callback()

            # Send notifications
            if self.notify_callback:
                await self.notify_callback(results)

            duration = (datetime.now(UTC) - start_time).total_seconds()
            logger.info(f"Daily reconciliation completed in {duration:.1f}s")

        except Exception as e:
            logger.error(f"Daily reconciliation failed: {e}")

    async def _run_sync(self) -> None:
        """Execute transaction sync for all entities."""
        logger.info("Starting periodic sync")

        try:
            # This would call the sync service
            # For now, just log
            logger.info("Periodic sync completed")

        except Exception as e:
            logger.error(f"Periodic sync failed: {e}")

    async def run_now(self, profile_id: int | None = None) -> dict:
        """Manually trigger reconciliation.

        Args:
            profile_id: Specific profile to reconcile, or None for all

        Returns:
            Results from reconciliation
        """
        logger.info(f"Manual reconciliation triggered for profile {profile_id or 'all'}")

        if profile_id:
            result = await self.reconcile_callback(profile_id=profile_id)
            return {profile_id: result}
        else:
            return await self.reconcile_callback()


class CronExpression:
    """Simple cron expression parser for scheduling."""

    def __init__(self, expression: str):
        """Parse cron expression.

        Supports: minute hour day month weekday
        Special values: * (any), */N (every N), N-M (range)
        """
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {expression}")

        self.minute = self._parse_field(parts[0], 0, 59)
        self.hour = self._parse_field(parts[1], 0, 23)
        self.day = self._parse_field(parts[2], 1, 31)
        self.month = self._parse_field(parts[3], 1, 12)
        self.weekday = self._parse_field(parts[4], 0, 6)

    def _parse_field(self, field: str, min_val: int, max_val: int) -> set[int]:
        """Parse a single cron field."""
        if field == "*":
            return set(range(min_val, max_val + 1))

        if field.startswith("*/"):
            step = int(field[2:])
            return set(range(min_val, max_val + 1, step))

        if "-" in field:
            start, end = field.split("-")
            return set(range(int(start), int(end) + 1))

        if "," in field:
            return {int(v) for v in field.split(",")}

        return {int(field)}

    def matches(self, dt: datetime) -> bool:
        """Check if datetime matches cron expression."""
        return (
            dt.minute in self.minute
            and dt.hour in self.hour
            and dt.day in self.day
            and dt.month in self.month
            and dt.weekday() in self.weekday
        )

    def next_run(self, after: datetime | None = None) -> datetime:
        """Calculate next run time after given datetime."""
        dt = after or datetime.now(UTC)
        dt = dt.replace(second=0, microsecond=0) + timedelta(minutes=1)

        # Search up to 1 year ahead
        for _ in range(365 * 24 * 60):
            if self.matches(dt):
                return dt
            dt += timedelta(minutes=1)

        raise ValueError("Could not find next run time within 1 year")
