"""
OTLP Collector for CIRISAgent telemetry
Pulls metrics, traces, and logs from agent OTLP endpoints
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import httpx
import json
import asyncpg
from asyncpg import Pool

logger = logging.getLogger(__name__)


class OTLPCollector:
    """Collects telemetry from agent OTLP endpoints"""
    
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: Optional[Pool] = None
        self.running = False
        self.tasks = []
        self.agent_configs = {}  # Will be loaded dynamically from database
        self.agent_tasks = {}  # Track tasks by agent_id for dynamic updates
        self.refresh_interval = int(os.getenv("AGENT_DISCOVERY_INTERVAL", "60"))
        
    async def _load_agent_configs_from_db(self) -> Dict[str, Dict[str, str]]:
        """Load agent configurations from discovered_agents table"""
        configs = {}
        
        try:
            async with self.pool.acquire() as conn:
                # Get active agents discovered by manager collector
                rows = await conn.fetch("""
                    SELECT DISTINCT ON (agent_id) 
                        da.agent_id, 
                        da.agent_name, 
                        da.api_port,
                        da.status,
                        da.deployment,
                        m.url as manager_url,
                        m.auth_token as manager_token
                    FROM discovered_agents da
                    JOIN managers m ON da.manager_id = m.manager_id
                    WHERE da.status IN ('running', 'active', 'healthy')
                        AND da.last_seen > NOW() - INTERVAL '10 minutes'
                        AND m.status = 'online'
                    ORDER BY da.agent_id, da.last_seen DESC
                """)
                
                for row in rows:
                    agent_id = row['agent_id']
                    agent_name = row['agent_name']
                    
                    # Build agent URL - agents expose telemetry on their API port
                    # Assuming agents are accessible via container name on Docker network
                    agent_url = f"http://{agent_name}:{row['api_port'] or 8080}"
                    
                    # Try environment variable token first, fallback to manager token
                    token = os.getenv(f"AGENT_{agent_name.upper()}_TOKEN", "")
                    if not token:
                        # Use a default token or manager's token
                        token = row['manager_token'] or os.getenv("DEFAULT_AGENT_TOKEN", "default")
                    
                    configs[agent_id] = {
                        "url": agent_url,
                        "token": token,
                        "name": agent_name,
                        "agent_id": agent_id
                    }
                    
                logger.info(f"Loaded {len(configs)} agent configurations from database")
                
        except Exception as e:
            logger.error(f"Failed to load agent configs from database: {e}")
            # Fallback to environment variables
            configs = self._load_agent_configs_from_env()
            
        return configs
    
    def _load_agent_configs_from_env(self) -> Dict[str, Dict[str, str]]:
        """Fallback: Load agent configurations from environment variables"""
        configs = {}
        
        # Look for AGENT_*_TOKEN and AGENT_*_URL pairs
        for key in os.environ:
            if key.startswith("AGENT_") and key.endswith("_TOKEN"):
                agent_name = key[6:-6].lower()  # Extract name between AGENT_ and _TOKEN
                token = os.environ[key]
                url_key = f"AGENT_{agent_name.upper()}_URL"
                url = os.environ.get(url_key)
                
                if url:
                    agent_id = f"env_{agent_name}"  # Prefix with env_ for env-based configs
                    configs[agent_id] = {
                        "url": url.rstrip("/"),
                        "token": token,
                        "name": agent_name,
                        "agent_id": agent_id
                    }
                    logger.info(f"Loaded config from env for agent: {agent_name}")
                    
        return configs
    
    async def _agent_discovery_loop(self):
        """Periodically refresh agent configurations from database"""
        while self.running:
            try:
                await asyncio.sleep(self.refresh_interval)
                await self._refresh_agent_configs()
            except Exception as e:
                logger.error(f"Error in agent discovery loop: {e}")
                
    async def _refresh_agent_configs(self):
        """Refresh agent configurations and update collection tasks"""
        logger.info("Refreshing agent configurations from database")
        
        # Load latest configs
        new_configs = await self._load_agent_configs_from_db()
        
        # Find agents to add (in new_configs but not in current)
        agents_to_add = set(new_configs.keys()) - set(self.agent_configs.keys())
        
        # Find agents to remove (in current but not in new_configs)
        agents_to_remove = set(self.agent_configs.keys()) - set(new_configs.keys())
        
        # Remove old agents
        for agent_id in agents_to_remove:
            if agent_id in self.agent_tasks:
                logger.info(f"Stopping collection for removed agent: {agent_id}")
                self.agent_tasks[agent_id].cancel()
                del self.agent_tasks[agent_id]
            if agent_id in self.agent_configs:
                del self.agent_configs[agent_id]
                
        # Add new agents
        for agent_id in agents_to_add:
            config = new_configs[agent_id]
            logger.info(f"Starting collection for new agent: {config['name']} (ID: {agent_id})")
            task = asyncio.create_task(self.collect_agent_loop(config))
            self.agent_tasks[agent_id] = task
            self.tasks.append(task)
            self.agent_configs[agent_id] = config
            
        # Update existing agents' configs (URL/token might have changed)
        for agent_id in set(new_configs.keys()) & set(self.agent_configs.keys()):
            self.agent_configs[agent_id].update(new_configs[agent_id])
            
        logger.info(f"Agent discovery complete. Active agents: {len(self.agent_configs)}")
    
    async def start(self):
        """Start the OTLP collector"""
        logger.info("Starting OTLP Collector")
        self.pool = await asyncpg.create_pool(self.database_url, min_size=2, max_size=10)
        self.running = True
        
        # Load initial agent configs from database
        await self._refresh_agent_configs()
        
        # Start the agent discovery refresh task
        self.tasks.append(asyncio.create_task(self._agent_discovery_loop()))
        
        logger.info(f"Started OTLP collector with dynamic agent discovery")
        
    async def stop(self):
        """Stop the collector"""
        logger.info("Stopping OTLP Collector")
        self.running = False
        
        for task in self.tasks:
            task.cancel()
            
        await asyncio.gather(*self.tasks, return_exceptions=True)
        
        if self.pool:
            await self.pool.close()
            
    async def collect_agent_loop(self, config: Dict[str, str]):
        """Collection loop for a single agent"""
        agent_name = config["name"]
        agent_id = config.get("agent_id", agent_name)
        agent_url = config["url"]
        interval = int(os.getenv("COLLECTION_INTERVAL_SECONDS", "30"))
        
        logger.info(f"Starting OTLP collection for {agent_name} (ID: {agent_id}) at {agent_url} every {interval}s")
        
        while self.running:
            try:
                await self.collect_otlp_data(config)
            except Exception as e:
                logger.error(f"Error collecting OTLP from {agent_name}: {e}")
                await self.store_collection_error(agent_name, str(e))
                
            await asyncio.sleep(interval)
            
    async def collect_otlp_data(self, config: Dict[str, str]):
        """Collect OTLP data from all signal endpoints"""
        agent_name = config["name"]
        agent_url = config["url"]
        token = config["token"]
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Collect metrics
            metrics_data = await self._fetch_otlp_signal(
                client, f"{agent_url}/v1/telemetry/otlp/metrics", headers
            )
            
            # Collect traces
            traces_data = await self._fetch_otlp_signal(
                client, f"{agent_url}/v1/telemetry/otlp/traces", headers
            )
            
            # Collect logs
            logs_data = await self._fetch_otlp_signal(
                client, f"{agent_url}/v1/telemetry/otlp/logs", headers
            )
            
        # Store collected data
        await self.store_otlp_data(agent_name, metrics_data, traces_data, logs_data)
        
    async def _fetch_otlp_signal(self, client: httpx.AsyncClient, url: str, headers: Dict) -> Optional[Dict]:
        """Fetch a single OTLP signal"""
        try:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"OTLP fetch failed for {url}: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None
            
    async def store_otlp_data(self, agent_name: str, metrics: Optional[Dict], 
                             traces: Optional[Dict], logs: Optional[Dict]):
        """Store OTLP data in database"""
        async with self.pool.acquire() as conn:
            # Store raw OTLP data
            await conn.execute("""
                INSERT INTO otlp_telemetry 
                (agent_name, collected_at, metrics_data, traces_data, logs_data)
                VALUES ($1, $2, $3, $4, $5)
            """,
                agent_name,
                datetime.now(timezone.utc),
                json.dumps(metrics) if metrics else None,
                json.dumps(traces) if traces else None,
                json.dumps(logs) if logs else None
            )
            
            # Process and store metrics in time-series format
            if metrics:
                await self._process_metrics(conn, agent_name, metrics)
                
            # Process and store traces
            if traces:
                await self._process_traces(conn, agent_name, traces)
                
            # Process and store logs
            if logs:
                await self._process_logs(conn, agent_name, logs)
                
            logger.info(f"Stored OTLP data for {agent_name}")
            
    async def _process_metrics(self, conn, agent_name: str, metrics: Dict):
        """Process OTLP metrics and store in time-series format"""
        if "resourceMetrics" not in metrics:
            return
        
        metric_count = 0
        error_count = 0
        
        for resource_metric in metrics["resourceMetrics"]:
            resource_attrs = resource_metric.get("resource", {}).get("attributes", [])
            
            for scope_metric in resource_metric.get("scopeMetrics", []):
                for metric in scope_metric.get("metrics", []):
                    metric_name = metric.get("name")
                    
                    # Handle different metric types
                    data_points = []
                    if "gauge" in metric:
                        data_points = metric["gauge"].get("dataPoints", [])
                    elif "sum" in metric:
                        data_points = metric["sum"].get("dataPoints", [])
                    elif "histogram" in metric:
                        data_points = metric["histogram"].get("dataPoints", [])
                        
                    for point in data_points:
                        try:
                            # Extract value based on type
                            value = None
                            if "asDouble" in point:
                                value = point["asDouble"]
                            elif "asInt" in point:
                                value = point["asInt"]
                            else:
                                continue
                                
                            # Extract labels from attributes - handle all value types
                            labels = {}
                            for attr in point.get("attributes", []):
                                key = attr.get("key", "")
                                val = attr.get("value", {})
                                if "stringValue" in val:
                                    labels[key] = val["stringValue"]
                                elif "intValue" in val:
                                    labels[key] = str(val["intValue"])
                                elif "boolValue" in val:
                                    labels[key] = str(val["boolValue"]).lower()
                                    
                            # Store metric with conflict handling
                            await conn.execute("""
                                INSERT INTO agent_metrics 
                                (agent_name, metric_name, value, labels, timestamp)
                                VALUES ($1, $2, $3, $4, $5)
                                ON CONFLICT (agent_name, metric_name, timestamp, labels)
                                DO UPDATE SET value = EXCLUDED.value
                            """,
                                agent_name,
                                metric_name,
                                float(value),
                                json.dumps(labels),
                                datetime.fromtimestamp(float(point.get("timeUnixNano", 0)) / 1e9, tz=timezone.utc)
                            )
                            metric_count += 1
                        except Exception as e:
                            logger.warning(f"Failed to store metric {metric_name}: {e}")
                            error_count += 1
        
        if metric_count > 0 or error_count > 0:
            logger.info(f"Processed {metric_count} metrics for {agent_name} ({error_count} errors)")
                        
    async def _process_traces(self, conn, agent_name: str, traces: Dict):
        """Process OTLP traces"""
        if "resourceSpans" not in traces:
            logger.warning(f"No resourceSpans in traces for {agent_name}")
            return
        
        trace_count = 0
        error_count = 0
        
        for resource_span in traces["resourceSpans"]:
            for scope_span in resource_span.get("scopeSpans", []):
                spans = scope_span.get("spans", [])
                logger.info(f"Processing {len(spans)} spans for {agent_name}")
                for span in spans:
                    # Store trace span with conflict handling
                    try:
                        await conn.execute("""
                            INSERT INTO agent_traces 
                            (agent_name, trace_id, span_id, parent_span_id, operation_name, 
                             start_time, end_time, attributes, events, status)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                            ON CONFLICT (trace_id, span_id) DO NOTHING
                        """,
                            agent_name,
                            span.get("traceId"),
                            span.get("spanId"),
                            span.get("parentSpanId"),
                            span.get("name"),
                            datetime.fromtimestamp(float(span.get("startTimeUnixNano", 0)) / 1e9, tz=timezone.utc),
                            datetime.fromtimestamp(float(span.get("endTimeUnixNano", 0)) / 1e9, tz=timezone.utc),
                            json.dumps(span.get("attributes", [])),
                            json.dumps(span.get("events", [])),
                            span.get("status", {}).get("code", "OK")
                        )
                        trace_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to store trace for {agent_name}: {e}")
                        error_count += 1
        
        if trace_count > 0 or error_count > 0:
            logger.info(f"Processed {trace_count} traces for {agent_name} ({error_count} errors)")
        elif len(traces.get("resourceSpans", [])) > 0:
            logger.info(f"No spans found in traces for {agent_name}")
                    
    async def _process_logs(self, conn, agent_name: str, logs: Dict):
        """Process OTLP logs"""
        if "resourceLogs" not in logs:
            return
            
        for resource_log in logs["resourceLogs"]:
            for scope_log in resource_log.get("scopeLogs", []):
                for log_record in scope_log.get("logRecords", []):
                    # Map severity
                    severity_map = {
                        5: "DEBUG",
                        9: "INFO",
                        13: "WARNING",
                        17: "ERROR",
                        21: "CRITICAL"
                    }
                    severity = severity_map.get(log_record.get("severityNumber", 9), "INFO")
                    
                    # Store log
                    await conn.execute("""
                        INSERT INTO agent_logs 
                        (agent_name, timestamp, severity, message, 
                         trace_id, span_id, attributes)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                        agent_name,
                        datetime.fromtimestamp(float(log_record.get("timeUnixNano", 0)) / 1e9, tz=timezone.utc),
                        severity,
                        log_record.get("body", {}).get("stringValue", ""),
                        log_record.get("traceId"),
                        log_record.get("spanId"),
                        json.dumps(log_record.get("attributes", []))
                    )
                    
    async def store_collection_error(self, agent_name: str, error: str):
        """Store collection error"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO collection_errors 
                (agent_name, error_message, occurred_at)
                VALUES ($1, $2, $3)
            """,
                agent_name,
                error,
                datetime.now(timezone.utc)
            )
            
    async def get_agent_health(self, agent_name: str) -> Dict:
        """Get agent health based on recent telemetry"""
        async with self.pool.acquire() as conn:
            # Check recent metrics
            row = await conn.fetchrow("""
                SELECT COUNT(*) as metric_count,
                       MAX(timestamp) as last_metric
                FROM agent_metrics
                WHERE agent_name = $1 
                  AND timestamp > NOW() - INTERVAL '5 minutes'
            """, agent_name)
            
            metric_count = row["metric_count"] if row else 0
            last_metric = row["last_metric"] if row else None
            
            # Check for recent errors
            error_row = await conn.fetchrow("""
                SELECT COUNT(*) as error_count
                FROM collection_errors
                WHERE agent_name = $1
                  AND occurred_at > NOW() - INTERVAL '5 minutes'
            """, agent_name)
            
            error_count = error_row["error_count"] if error_row else 0
            
            return {
                "agent_name": agent_name,
                "healthy": metric_count > 0 and error_count == 0,
                "metric_count": metric_count,
                "last_metric": last_metric.isoformat() if last_metric else None,
                "recent_errors": error_count
            }


async def main():
    """Standalone OTLP collector"""
    database_url = os.getenv("DATABASE_URL", "postgresql://user:password@host:5432/dbname")
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    collector = OTLPCollector(database_url)
    
    try:
        await collector.start()
        
        # Keep running
        while True:
            await asyncio.sleep(60)
            
            # Log health status
            for agent_name in collector.agent_configs:
                health = await collector.get_agent_health(agent_name)
                logger.info(f"Agent {agent_name} health: {health}")
                
    except KeyboardInterrupt:
        logger.info("Received interrupt")
    finally:
        await collector.stop()


if __name__ == "__main__":
    asyncio.run(main())