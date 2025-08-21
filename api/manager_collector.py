"""
CIRISManager Telemetry Collector Service
Collects telemetry from registered CIRISManager instances
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import httpx
import asyncpg
from asyncpg import Pool
import json
import os

logger = logging.getLogger(__name__)


class ManagerCollector:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: Optional[Pool] = None
        self.running = False
        self.tasks = []
        
    async def start(self):
        """Start the collector service"""
        logger.info("Starting ManagerCollector service")
        self.pool = await asyncpg.create_pool(self.database_url, min_size=2, max_size=10)
        self.running = True
        
        # Start collection tasks for each manager
        managers = await self.get_enabled_managers()
        for manager in managers:
            task = asyncio.create_task(self.collect_manager_loop(manager))
            self.tasks.append(task)
            
        logger.info(f"Started collection for {len(managers)} managers")
        
    async def stop(self):
        """Stop the collector service"""
        logger.info("Stopping ManagerCollector service")
        self.running = False
        
        # Cancel all collection tasks
        for task in self.tasks:
            task.cancel()
            
        # Wait for tasks to complete
        await asyncio.gather(*self.tasks, return_exceptions=True)
        
        # Close database pool
        if self.pool:
            await self.pool.close()
            
    async def get_enabled_managers(self) -> List[Dict]:
        """Get all enabled managers from database"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM managers WHERE enabled = true"
            )
            return [dict(row) for row in rows]
            
    async def collect_manager_loop(self, manager: Dict):
        """Collection loop for a single manager"""
        manager_id = manager['id']
        manager_name = manager['name']
        manager_url = manager['url'].rstrip('/')
        interval = manager.get('collection_interval_seconds', 30)
        
        logger.info(f"Starting collection loop for {manager_name} ({manager_url}) every {interval}s")
        
        while self.running:
            try:
                await self.collect_from_manager(manager)
            except Exception as e:
                logger.error(f"Error collecting from {manager_name}: {e}")
                await self.update_manager_error(manager_id, str(e))
                
            # Wait for next collection interval
            await asyncio.sleep(interval)
            
    async def collect_from_manager(self, manager: Dict):
        """Collect telemetry from a single manager"""
        manager_id = manager['id']
        manager_name = manager['name']
        manager_url = manager['url'].rstrip('/')
        auth_token = manager.get('auth_token')
        
        headers = {}
        if auth_token:
            headers['Authorization'] = f"Bearer {auth_token}"
            
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Collect manager status
            try:
                status_response = await client.get(
                    f"{manager_url}/manager/v1/status",
                    headers=headers
                )
                status_data = status_response.json() if status_response.status_code == 200 else None
            except Exception as e:
                logger.warning(f"Failed to get status from {manager_name}: {e}")
                status_data = None
                
            # Collect agents list
            try:
                agents_response = await client.get(
                    f"{manager_url}/manager/v1/agents",
                    headers=headers
                )
                agents_data = agents_response.json() if agents_response.status_code == 200 else []
            except Exception as e:
                logger.warning(f"Failed to get agents from {manager_name}: {e}")
                agents_data = []
                
        # Store collected data
        await self.store_manager_telemetry(manager_id, status_data, agents_data)
        
    async def store_manager_telemetry(self, manager_id: int, status_data: Optional[Dict], agents_data: List[Dict]):
        """Store collected telemetry in database"""
        async with self.pool.acquire() as conn:
            # Update manager last_seen
            await conn.execute(
                "UPDATE managers SET last_seen = $1, last_error = NULL WHERE id = $2",
                datetime.now(timezone.utc), manager_id
            )
            
            # Store manager telemetry
            if status_data:
                await conn.execute("""
                    INSERT INTO manager_telemetry 
                    (manager_id, agent_count, status, version, uptime_seconds, raw_data)
                    VALUES ($1, $2, $3, $4, $5, $6)
                """, 
                    manager_id,
                    len(agents_data),
                    status_data.get('status'),
                    status_data.get('version'),
                    status_data.get('uptime_seconds'),
                    json.dumps(status_data)
                )
                
            # Store/update discovered agents
            for agent in agents_data:
                await conn.execute("""
                    INSERT INTO discovered_agents 
                    (manager_id, agent_id, agent_name, status, cognitive_state, version, 
                     codename, api_port, health, template, deployment, last_seen, raw_data)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                    ON CONFLICT (manager_id, agent_id) 
                    DO UPDATE SET
                        agent_name = EXCLUDED.agent_name,
                        status = EXCLUDED.status,
                        cognitive_state = EXCLUDED.cognitive_state,
                        version = EXCLUDED.version,
                        codename = EXCLUDED.codename,
                        api_port = EXCLUDED.api_port,
                        health = EXCLUDED.health,
                        template = EXCLUDED.template,
                        deployment = EXCLUDED.deployment,
                        last_seen = EXCLUDED.last_seen,
                        raw_data = EXCLUDED.raw_data
                """,
                    manager_id,
                    agent.get('agent_id'),
                    agent.get('agent_name'),
                    agent.get('status'),
                    agent.get('cognitive_state'),
                    agent.get('version'),
                    agent.get('codename'),
                    agent.get('api_port'),
                    agent.get('health'),
                    agent.get('template'),
                    agent.get('deployment'),
                    datetime.now(timezone.utc),
                    json.dumps(agent)
                )
                
            logger.info(f"Stored telemetry for manager {manager_id}: {len(agents_data)} agents")
            
    async def update_manager_error(self, manager_id: int, error: str):
        """Update manager with error status"""
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE managers SET last_error = $1, last_seen = $2 WHERE id = $3",
                error, datetime.now(timezone.utc), manager_id
            )
            
    async def add_manager(self, name: str, url: str, description: str = "", 
                          auth_token: Optional[str] = None, 
                          collection_interval: int = 30) -> int:
        """Add a new manager to monitor"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO managers 
                (name, url, description, auth_token, collection_interval_seconds, enabled)
                VALUES ($1, $2, $3, $4, $5, true)
                RETURNING id
            """, name, url, description, auth_token, collection_interval)
            
            manager_id = row['id']
            
            # Start collection for new manager
            manager = {
                'id': manager_id,
                'name': name,
                'url': url,
                'auth_token': auth_token,
                'collection_interval_seconds': collection_interval
            }
            task = asyncio.create_task(self.collect_manager_loop(manager))
            self.tasks.append(task)
            
            logger.info(f"Added new manager: {name} ({url})")
            return manager_id
            
    async def remove_manager(self, manager_id: int):
        """Remove a manager and stop collecting from it"""
        async with self.pool.acquire() as conn:
            # Disable the manager (soft delete)
            await conn.execute(
                "UPDATE managers SET enabled = false WHERE id = $1",
                manager_id
            )
            
            logger.info(f"Disabled manager {manager_id}")
            
    async def get_manager_stats(self) -> Dict:
        """Get statistics about all managers"""
        async with self.pool.acquire() as conn:
            stats = {}
            
            # Total managers
            row = await conn.fetchrow("SELECT COUNT(*) as count FROM managers WHERE enabled = true")
            stats['total_managers'] = row['count']
            
            # Total discovered agents
            row = await conn.fetchrow("""
                SELECT COUNT(DISTINCT agent_id) as count 
                FROM discovered_agents 
                WHERE last_seen > NOW() - INTERVAL '5 minutes'
            """)
            stats['total_agents'] = row['count']
            
            # Managers with errors
            row = await conn.fetchrow("""
                SELECT COUNT(*) as count 
                FROM managers 
                WHERE enabled = true AND last_error IS NOT NULL
            """)
            stats['managers_with_errors'] = row['count']
            
            return stats


async def main():
    """Standalone collector service"""
    database_url = os.getenv('DATABASE_URL', 'postgresql://user:password@host:5432/dbname')
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    collector = ManagerCollector(database_url)
    
    try:
        await collector.start()
        
        # Keep running until interrupted
        while True:
            await asyncio.sleep(60)
            stats = await collector.get_manager_stats()
            logger.info(f"Collector stats: {stats}")
            
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        await collector.stop()


if __name__ == "__main__":
    asyncio.run(main())