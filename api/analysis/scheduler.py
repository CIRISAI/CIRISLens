"""
Coherence Ratchet Scheduled Job Runner

Runs detection queries periodically and stores alerts.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta
from typing import Any

from api.analysis.coherence_ratchet import (
    AlertSeverity,
    AnomalyAlert,
    CoherenceRatchetAnalyzer,
)

logger = logging.getLogger(__name__)


class CoherenceRatchetScheduler:
    """
    Manages scheduled execution of Coherence Ratchet detection queries.

    Runs detection jobs on configurable intervals and persists alerts to database.
    """

    def __init__(
        self,
        db_pool: Any,
        cross_agent_interval_hours: int = 24,
        temporal_drift_interval_hours: int = 24,
        hash_chain_interval_hours: int = 1,
        conscience_override_interval_hours: int = 24,
        intra_agent_interval_hours: int = 24,
    ):
        """
        Initialize scheduler.

        Args:
            db_pool: asyncpg connection pool
            cross_agent_interval_hours: How often to run cross-agent divergence check
            temporal_drift_interval_hours: How often to run temporal drift detection
            hash_chain_interval_hours: How often to verify hash chains
            conscience_override_interval_hours: How often to check override rates
            intra_agent_interval_hours: How often to check intra-agent consistency
        """
        self.db_pool = db_pool
        self.analyzer = CoherenceRatchetAnalyzer(db_pool)

        self.intervals = {
            "cross_agent_divergence": timedelta(hours=cross_agent_interval_hours),
            "temporal_drift": timedelta(hours=temporal_drift_interval_hours),
            "hash_chain": timedelta(hours=hash_chain_interval_hours),
            "conscience_override": timedelta(hours=conscience_override_interval_hours),
            "intra_agent_consistency": timedelta(hours=intra_agent_interval_hours),
        }

        self.last_run: dict[str, datetime] = {}
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the scheduler background task."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Coherence Ratchet scheduler started")

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("Coherence Ratchet scheduler stopped")

    async def _run_loop(self) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                await self._check_and_run_jobs()
                await asyncio.sleep(60)  # Check every minute
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Scheduler error: {e}")
                await asyncio.sleep(60)

    async def _check_and_run_jobs(self) -> None:
        """Check if any jobs need to run and execute them."""
        now = datetime.utcnow()

        for job_name, interval in self.intervals.items():
            last = self.last_run.get(job_name)
            if last is None or (now - last) >= interval:
                await self._run_job(job_name)
                self.last_run[job_name] = now

    async def _run_job(self, job_name: str) -> None:
        """Run a specific detection job."""
        logger.info(f"Running Coherence Ratchet job: {job_name}")
        alerts: list[AnomalyAlert] = []

        try:
            if job_name == "cross_agent_divergence":
                alerts = await self.analyzer.detect_cross_agent_divergence()
            elif job_name == "temporal_drift":
                alerts = await self.analyzer.detect_temporal_drift()
            elif job_name == "hash_chain":
                alerts = await self.analyzer.detect_hash_chain_anomalies()
            elif job_name == "conscience_override":
                alerts = await self.analyzer.detect_conscience_override_anomalies()
            elif job_name == "intra_agent_consistency":
                alerts = await self.analyzer.detect_intra_agent_inconsistency()

            if alerts:
                await self._persist_alerts(alerts)
                logger.info(f"Job {job_name} found {len(alerts)} alerts")
            else:
                logger.debug(f"Job {job_name} found no alerts")

        except Exception as e:
            logger.exception(f"Error in job {job_name}: {e}")

    async def _persist_alerts(self, alerts: list[AnomalyAlert]) -> None:
        """Persist alerts to the database."""
        insert_query = """
        INSERT INTO cirislens.coherence_ratchet_alerts (
            alert_id, alert_type, severity, detection_mechanism,
            agent_id_hash, domain, metric, value, baseline, deviation,
            timestamp, evidence_traces, recommended_action
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        ON CONFLICT (alert_id) DO NOTHING;
        """

        async with self.db_pool.acquire() as conn:
            for alert in alerts:
                try:
                    await conn.execute(
                        insert_query,
                        alert.alert_id,
                        alert.alert_type,
                        alert.severity.value,
                        alert.detection_mechanism.value,
                        alert.agent_id_hash,
                        alert.domain,
                        alert.metric,
                        alert.value,
                        alert.baseline,
                        alert.deviation,
                        alert.timestamp,
                        alert.evidence_traces,
                        alert.recommended_action,
                    )
                except Exception as e:
                    logger.error(f"Failed to persist alert {alert.alert_id}: {e}")

    async def run_all_now(self) -> list[AnomalyAlert]:
        """
        Run all detection jobs immediately (for manual triggering).

        Returns:
            Combined list of all detected anomalies
        """
        alerts = await self.analyzer.run_all_detections()
        if alerts:
            await self._persist_alerts(alerts)
        return alerts

    async def get_recent_alerts(
        self,
        hours: int = 24,
        severity: AlertSeverity | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Get recent alerts from the database.

        Args:
            hours: How many hours back to look
            severity: Filter by severity level
            limit: Maximum number of alerts to return

        Returns:
            List of alert dictionaries
        """
        query = """
        SELECT
            alert_id, alert_type, severity, detection_mechanism,
            agent_id_hash, domain, metric, value, baseline, deviation,
            timestamp, evidence_traces, recommended_action, acknowledged
        FROM cirislens.coherence_ratchet_alerts
        WHERE timestamp > NOW() - $1::interval
        """
        params: list[Any] = [f"{hours} hours"]

        if severity:
            query += " AND severity = $2"
            params.append(severity.value)

        query += " ORDER BY timestamp DESC LIMIT $" + str(len(params) + 1)
        params.append(limit)

        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    async def acknowledge_alert(self, alert_id: str, acknowledged_by: str) -> bool:
        """
        Mark an alert as acknowledged.

        Args:
            alert_id: The alert to acknowledge
            acknowledged_by: Who acknowledged it

        Returns:
            True if alert was found and updated
        """
        query = """
        UPDATE cirislens.coherence_ratchet_alerts
        SET acknowledged = TRUE,
            acknowledged_at = NOW(),
            acknowledged_by = $2
        WHERE alert_id = $1
        RETURNING alert_id;
        """

        async with self.db_pool.acquire() as conn:
            result = await conn.fetchval(query, alert_id, acknowledged_by)
            return result is not None
