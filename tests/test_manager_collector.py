"""
Unit tests for the CIRISLens Manager Collector using typed mocks
"""

import asyncio
import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.manager_collector import ManagerCollector
from tests.mocks import (
    MockFactory, MockManager, MockAgent, MockDiscoveredAgent,
    MockManagerStatus, MockTelemetryEntry, MockHTTPResponse, MockDatabase
)


@pytest.fixture
async def mock_pool():
    """Create a mock database pool"""
    pool = AsyncMock()
    conn = AsyncMock()
    
    # Mock pool.acquire context manager
    pool.acquire.return_value.__aenter__.return_value = conn
    pool.acquire.return_value.__aexit__.return_value = None
    
    return pool, conn


@pytest.fixture
async def mock_database():
    """Create a mock database with test data"""
    db = MockDatabase()
    
    # Add test managers
    for manager in MockFactory.create_managers(3):
        db.add_manager(manager)
    
    # Add test agents
    for manager in db.get_enabled_managers():
        agents = MockFactory.create_agents(5)
        for agent in agents:
            discovered = MockFactory.create_discovered_agent(
                manager_id=manager.id,
                agent=agent,
                manager_name=manager.name,
                manager_url=manager.url
            )
            db.add_discovered_agent(discovered)
    
    # Add telemetry history
    for manager in db.managers:
        history = MockFactory.create_telemetry_history(
            manager_id=manager.id,
            entries=10,
            base_agent_count=5
        )
        for entry in history:
            db.add_telemetry_entry(entry)
    
    return db


@pytest.fixture
async def collector(mock_pool):
    """Create a collector instance with mocked database"""
    pool, conn = mock_pool
    collector = ManagerCollector("postgresql://test@localhost/test")
    collector.pool = pool
    collector.running = True
    return collector, conn


class TestManagerCollectorWithMocks:
    """Test suite for ManagerCollector using typed mocks"""
    
    @pytest.mark.asyncio
    async def test_get_enabled_managers_typed(self, collector, mock_database):
        """Test getting enabled managers with typed mocks"""
        collector_instance, conn = collector
        
        # Setup mock response with typed managers
        managers = mock_database.get_enabled_managers()
        mock_rows = [m.to_db_row() for m in managers]
        conn.fetch.return_value = mock_rows
        
        # Call method
        result = await collector_instance.get_enabled_managers()
        
        # Assertions
        assert len(result) == 2  # Only enabled managers
        assert all(m['enabled'] for m in result)
        assert result[0]['name'] == 'Production'
        assert result[1]['name'] == 'Staging'
        conn.fetch.assert_called_once_with("SELECT * FROM managers WHERE enabled = true")
    
    @pytest.mark.asyncio
    async def test_collect_from_manager_with_typed_responses(self, collector):
        """Test collection with fully typed mock responses"""
        collector_instance, conn = collector
        
        # Create typed manager
        manager = MockFactory.create_manager(
            name="Test Manager",
            url="https://test.ciris.ai"
        )
        
        # Create typed responses
        status = MockFactory.create_manager_status(uptime_hours=48)
        agents = MockFactory.create_agents(3)
        
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            
            # Mock typed responses
            mock_client.get.side_effect = [
                MockHTTPResponse.success(status.to_dict()),
                MockHTTPResponse.success([a.to_dict() for a in agents])
            ]
            
            # Call method
            await collector_instance.collect_from_manager(manager.to_db_row())
            
            # Verify HTTP calls with correct URLs
            assert mock_client.get.call_count == 2
            calls = mock_client.get.call_args_list
            assert "manager/v1/status" in str(calls[0])
            assert "manager/v1/agents" in str(calls[1])
    
    @pytest.mark.asyncio
    async def test_store_manager_telemetry_typed(self, collector):
        """Test storing telemetry with typed data"""
        collector_instance, conn = collector
        
        # Create typed test data
        manager = MockFactory.create_manager()
        status = MockFactory.create_manager_status()
        agents = MockFactory.create_agents(3)
        
        # Call method with typed data
        await collector_instance.store_manager_telemetry(
            manager.id,
            status.to_dict(),
            [a.to_dict() for a in agents]
        )
        
        # Verify database calls
        calls = conn.execute.call_args_list
        
        # Check manager update call
        manager_update_call = calls[0]
        assert "UPDATE managers SET last_seen" in manager_update_call[0][0]
        assert manager_update_call[0][2] == manager.id
        
        # Check telemetry insert
        telemetry_call = calls[1]
        assert "INSERT INTO manager_telemetry" in telemetry_call[0][0]
        assert telemetry_call[0][1] == manager.id
        assert telemetry_call[0][2] == 3  # agent_count
        assert telemetry_call[0][3] == status.status
        assert telemetry_call[0][4] == status.version
        
        # Check agent inserts (one for each agent)
        assert len(calls) >= 2 + len(agents)
    
    @pytest.mark.asyncio
    async def test_discover_agents_from_multiple_managers(self, collector, mock_database):
        """Test discovering agents from multiple managers"""
        collector_instance, conn = collector
        
        # Setup mock discovered agents
        discovered_agents = mock_database.get_recent_agents()
        mock_rows = [a.to_db_row() for a in discovered_agents]
        
        # Mock the SQL query that joins managers and agents
        conn.fetch.return_value = mock_rows
        
        # Simulate getting all discovered agents
        query = """
            SELECT da.*, m.name as manager_name, m.url as manager_url
            FROM discovered_agents da
            JOIN managers m ON da.manager_id = m.id
            WHERE m.enabled = true AND da.last_seen > NOW() - INTERVAL '5 minutes'
            ORDER BY da.agent_name
        """
        
        result = await conn.fetch(query)
        
        # Verify we got agents from multiple managers
        manager_names = set(row['manager_name'] for row in result)
        assert len(manager_names) >= 2
        assert 'Production' in manager_names
        assert all(row['health'] in ['healthy', 'unhealthy', 'unknown'] for row in result)
    
    @pytest.mark.asyncio
    async def test_handle_manager_with_auth(self, collector):
        """Test manager with authentication token"""
        collector_instance, conn = collector
        
        # Create manager with auth
        manager = MockFactory.create_manager(
            name="Secure Manager",
            url="https://secure.ciris.ai"
        )
        manager.auth_token = "Bearer secret-token-123"
        
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            
            # Mock successful auth response
            status = MockFactory.create_manager_status()
            agents = MockFactory.create_agents(2)
            
            mock_client.get.side_effect = [
                MockHTTPResponse.success(status.to_dict()),
                MockHTTPResponse.success([a.to_dict() for a in agents])
            ]
            
            # Call method
            await collector_instance.collect_from_manager(manager.to_db_row())
            
            # Verify auth header was included
            for call in mock_client.get.call_args_list:
                headers = call[1].get('headers', {})
                assert headers.get('Authorization') == 'Bearer secret-token-123'
    
    @pytest.mark.asyncio
    async def test_manager_error_handling(self, collector):
        """Test error handling with typed manager"""
        collector_instance, conn = collector
        
        # Create manager that will fail
        manager = MockFactory.create_manager(
            name="Failing Manager",
            url="https://failing.ciris.ai",
            with_error=True
        )
        
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            
            # Simulate connection error
            import httpx
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")
            
            # Should handle error gracefully
            await collector_instance.collect_from_manager(manager.to_db_row())
            
            # No exception should be raised
            assert mock_client.get.called
    
    @pytest.mark.asyncio
    async def test_get_manager_stats_typed(self, collector, mock_database):
        """Test statistics with typed mock data"""
        collector_instance, conn = collector
        
        # Setup statistics based on mock database
        enabled_managers = len(mock_database.get_enabled_managers())
        recent_agents = len(mock_database.get_recent_agents())
        managers_with_errors = sum(1 for m in mock_database.managers 
                                  if m.enabled and m.last_error)
        
        # Mock database responses
        conn.fetchrow.side_effect = [
            {'count': enabled_managers},
            {'count': recent_agents},
            {'count': managers_with_errors}
        ]
        
        # Call method
        stats = await collector_instance.get_manager_stats()
        
        # Assertions
        assert stats['total_managers'] == enabled_managers
        assert stats['total_agents'] == recent_agents
        assert stats['managers_with_errors'] == managers_with_errors
    
    @pytest.mark.asyncio
    async def test_telemetry_history_retrieval(self, collector, mock_database):
        """Test retrieving telemetry history with typed data"""
        collector_instance, conn = collector
        
        manager = mock_database.managers[0]
        history = mock_database.get_manager_telemetry(manager.id, limit=5)
        
        # Mock database response
        mock_rows = [entry.to_db_row() for entry in history]
        conn.fetch.return_value = mock_rows
        
        # Simulate query
        result = await conn.fetch(
            "SELECT * FROM manager_telemetry WHERE manager_id = $1 ORDER BY collected_at DESC LIMIT $2",
            manager.id, 5
        )
        
        # Verify history
        assert len(result) == 5
        assert all(row['manager_id'] == manager.id for row in result)
        # Check that entries are ordered by time (most recent first)
        times = [row['collected_at'] for row in result]
        assert times == sorted(times, reverse=True)
    
    @pytest.mark.asyncio
    async def test_cognitive_state_distribution(self, mock_database):
        """Test that discovered agents have proper cognitive state distribution"""
        agents = mock_database.get_recent_agents()
        
        # Check cognitive states
        valid_states = {"WORK", "DREAM", "PLAY", "SOLITUDE", "WAKEUP", "SHUTDOWN"}
        states = [agent.cognitive_state for agent in agents]
        
        assert all(state in valid_states for state in states)
        assert len(set(states)) > 1  # Should have variety
    
    @pytest.mark.asyncio
    async def test_add_manager_typed(self, collector):
        """Test adding a new manager with typed data"""
        collector_instance, conn = collector
        
        # Create new manager data
        new_manager = MockFactory.create_manager(
            id=None,  # Will be assigned
            name="New Manager",
            url="https://new.ciris.ai"
        )
        
        # Mock database response
        conn.fetchrow.return_value = {'id': 4}
        
        # Mock the collection loop
        collector_instance.collect_manager_loop = AsyncMock()
        
        # Call method
        manager_id = await collector_instance.add_manager(
            name=new_manager.name,
            url=new_manager.url,
            description=new_manager.description,
            auth_token=new_manager.auth_token,
            collection_interval=new_manager.collection_interval_seconds
        )
        
        # Assertions
        assert manager_id == 4
        assert len(collector_instance.tasks) > 0
        
        # Verify SQL call
        insert_call = conn.fetchrow.call_args[0][0]
        assert "INSERT INTO managers" in insert_call
        assert new_manager.name in conn.fetchrow.call_args[0]
        assert new_manager.url in conn.fetchrow.call_args[0]


class TestManagerValidation:
    """Test validation logic for managers and agents"""
    
    def test_manager_url_validation(self):
        """Test that manager URLs are properly formatted"""
        manager = MockFactory.create_manager()
        
        assert manager.url.startswith("https://")
        assert not manager.url.endswith("/")
        
    def test_agent_port_range(self):
        """Test that agent ports are in valid range"""
        agents = MockFactory.create_agents(100)
        
        for agent in agents:
            assert 8001 <= agent.api_port <= 8999
            
    def test_cognitive_state_transitions(self):
        """Test that cognitive states are valid"""
        valid_states = {"WORK", "DREAM", "PLAY", "SOLITUDE", "WAKEUP", "SHUTDOWN"}
        
        agents = MockFactory.create_agents(50)
        for agent in agents:
            assert agent.cognitive_state in valid_states
            
    def test_health_status_values(self):
        """Test that health statuses are valid"""
        valid_health = {"healthy", "unhealthy", "unknown"}
        
        agents = MockFactory.create_agents(50)
        for agent in agents:
            assert agent.health in valid_health
            
    def test_version_format(self):
        """Test that versions follow semantic versioning"""
        import re
        version_pattern = re.compile(r'^\d+\.\d+\.\d+(-\w+)?$')
        
        agents = MockFactory.create_agents(10)
        for agent in agents:
            assert version_pattern.match(agent.version), f"Invalid version: {agent.version}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])