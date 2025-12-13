"""
Mock objects and fixtures for CIRISLens testing
Provides typed mock data for managers, agents, and telemetry
"""

import random
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass
class MockManager:
    """Mock CIRISManager instance with typed fields"""
    id: int
    name: str
    url: str
    description: str | None = ""
    enabled: bool = True
    auth_token: str | None = None
    collection_interval_seconds: int = 30
    last_seen: datetime | None = None
    last_error: str | None = None
    added_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for database/API compatibility"""
        data = asdict(self)
        # Convert datetime objects to ISO format strings
        if data['last_seen']:
            data['last_seen'] = data['last_seen'].isoformat()
        if data['added_at']:
            data['added_at'] = data['added_at'].isoformat()
        return data

    def to_db_row(self) -> dict[str, Any]:
        """Convert to database row format"""
        return {
            'id': self.id,
            'name': self.name,
            'url': self.url,
            'description': self.description,
            'enabled': self.enabled,
            'auth_token': self.auth_token,
            'collection_interval_seconds': self.collection_interval_seconds,
            'last_seen': self.last_seen,
            'last_error': self.last_error,
            'added_at': self.added_at,
            'metadata': self.metadata
        }


@dataclass
class MockAgent:
    """Mock CIRIS Agent with typed fields matching actual agent data"""
    agent_id: str
    agent_name: str
    status: str = "running"  # running, stopped, error
    cognitive_state: str = "WORK"  # WORK, DREAM, PLAY, SOLITUDE, WAKEUP, SHUTDOWN
    version: str = "1.4.5"
    codename: str = "Graceful Guardian"
    code_hash: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    container_name: str = field(default_factory=lambda: f"ciris-{uuid.uuid4().hex[:8]}")
    api_port: int = field(default_factory=lambda: random.randint(8001, 8999))
    health: str = "healthy"  # healthy, unhealthy, unknown
    template: str = "base"
    deployment: str = "CIRIS_DISCORD_PILOT"
    discord_enabled: bool = True
    mock_llm: bool = False
    image: str = "ghcr.io/cirisai/ciris-agent:latest"
    update_available: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary matching manager API response"""
        return asdict(self)


@dataclass
class MockManagerStatus:
    """Mock manager status response"""
    status: str = "running"
    version: str = "2.2.0"
    uptime_seconds: int = field(default_factory=lambda: random.randint(3600, 86400))
    start_time: datetime = field(default_factory=lambda: datetime.now(UTC) - timedelta(hours=24))
    auth_mode: str = "production"
    components: dict[str, str] = field(default_factory=lambda: {
        "api_server": "running",
        "watchdog": "running",
        "nginx": "enabled"
    })
    system_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to API response format"""
        data = asdict(self)
        data['start_time'] = data['start_time'].isoformat()
        return data


@dataclass
class MockDiscoveredAgent:
    """Mock discovered agent from database"""
    id: int
    manager_id: int
    agent_id: str
    agent_name: str
    status: str
    cognitive_state: str
    version: str
    codename: str
    api_port: int
    health: str
    template: str
    deployment: str
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw_data: dict[str, Any] = field(default_factory=dict)
    # Join fields
    manager_name: str | None = None
    manager_url: str | None = None

    def to_db_row(self) -> dict[str, Any]:
        """Convert to database row format"""
        return {
            'id': self.id,
            'manager_id': self.manager_id,
            'agent_id': self.agent_id,
            'agent_name': self.agent_name,
            'status': self.status,
            'cognitive_state': self.cognitive_state,
            'version': self.version,
            'codename': self.codename,
            'api_port': self.api_port,
            'health': self.health,
            'template': self.template,
            'deployment': self.deployment,
            'last_seen': self.last_seen,
            'raw_data': self.raw_data,
            'manager_name': self.manager_name,
            'manager_url': self.manager_url
        }


@dataclass
class MockTelemetryEntry:
    """Mock telemetry history entry"""
    id: int
    manager_id: int
    collected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    agent_count: int = 0
    status: str | None = "running"
    version: str | None = "2.2.0"
    uptime_seconds: int | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_db_row(self) -> dict[str, Any]:
        """Convert to database row format"""
        return asdict(self)


class MockFactory:
    """Factory for creating consistent mock objects"""

    @staticmethod
    def create_manager(
        id: int = 1,
        name: str = "Production",
        url: str = "https://agents.ciris.ai",
        enabled: bool = True,
        with_error: bool = False
    ) -> MockManager:
        """Create a mock manager with optional error state"""
        manager = MockManager(
            id=id,
            name=name,
            url=url,
            description=f"{name} CIRISManager instance",
            enabled=enabled,
            last_seen=datetime.now(UTC) if not with_error else datetime.now(UTC) - timedelta(minutes=10),
            last_error="Connection timeout" if with_error else None
        )
        return manager

    @staticmethod
    def create_managers(count: int = 3) -> list[MockManager]:
        """Create multiple mock managers"""
        names = ["Production", "Staging", "Development", "Testing", "Backup"]
        urls = ["https://agents.ciris.ai", "https://staging.ciris.ai", "https://dev.ciris.ai",
                "https://test.ciris.ai", "https://backup.ciris.ai"]

        managers = []
        for i in range(min(count, len(names))):
            managers.append(MockFactory.create_manager(
                id=i+1,
                name=names[i],
                url=urls[i],
                enabled=i < 2,  # First two are enabled
                with_error=i == count - 1  # Last one has error
            ))
        return managers

    @staticmethod
    def create_agent(
        agent_id: str | None = None,
        agent_name: str | None = None,
        cognitive_state: str = "WORK"
    ) -> MockAgent:
        """Create a mock agent"""
        if not agent_id:
            agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        if not agent_name:
            agent_name = agent_id.capitalize()

        states = ["WORK", "DREAM", "PLAY", "SOLITUDE", "WAKEUP", "SHUTDOWN"]
        codenames = ["Graceful Guardian", "Stellar Sentinel", "Quantum Quester",
                    "Digital Dreamer", "Neural Navigator"]

        return MockAgent(
            agent_id=agent_id,
            agent_name=agent_name,
            cognitive_state=cognitive_state if cognitive_state in states else random.choice(states),
            codename=random.choice(codenames),
            health=random.choice(["healthy", "healthy", "healthy", "unhealthy"])  # 75% healthy
        )

    @staticmethod
    def create_agents(count: int = 5) -> list[MockAgent]:
        """Create multiple mock agents"""
        agent_names = ["Datum", "Nexus", "Prism", "Echo", "Forge", "Spark", "Pulse", "Wave"]
        agents = []

        for i in range(count):
            name = agent_names[i] if i < len(agent_names) else f"Agent-{i+1}"
            agents.append(MockFactory.create_agent(
                agent_id=name.lower(),
                agent_name=name
            ))
        return agents

    @staticmethod
    def create_discovered_agent(
        manager_id: int = 1,
        agent: MockAgent | None = None,
        manager_name: str = "Production",
        manager_url: str = "https://agents.ciris.ai"
    ) -> MockDiscoveredAgent:
        """Create a discovered agent from a mock agent"""
        if not agent:
            agent = MockFactory.create_agent()

        return MockDiscoveredAgent(
            id=random.randint(1, 1000),
            manager_id=manager_id,
            agent_id=agent.agent_id,
            agent_name=agent.agent_name,
            status=agent.status,
            cognitive_state=agent.cognitive_state,
            version=agent.version,
            codename=agent.codename,
            api_port=agent.api_port,
            health=agent.health,
            template=agent.template,
            deployment=agent.deployment,
            raw_data=agent.to_dict(),
            manager_name=manager_name,
            manager_url=manager_url
        )

    @staticmethod
    def create_manager_status(uptime_hours: int = 24) -> MockManagerStatus:
        """Create a mock manager status response"""
        return MockManagerStatus(
            uptime_seconds=uptime_hours * 3600,
            start_time=datetime.now(UTC) - timedelta(hours=uptime_hours)
        )

    @staticmethod
    def create_telemetry_entry(
        manager_id: int = 1,
        agent_count: int = 5,
        hours_ago: int = 0
    ) -> MockTelemetryEntry:
        """Create a mock telemetry history entry"""
        return MockTelemetryEntry(
            id=random.randint(1, 10000),
            manager_id=manager_id,
            collected_at=datetime.now(UTC) - timedelta(hours=hours_ago),
            agent_count=agent_count,
            uptime_seconds=random.randint(3600, 86400)
        )

    @staticmethod
    def create_telemetry_history(
        manager_id: int = 1,
        entries: int = 10,
        base_agent_count: int = 5
    ) -> list[MockTelemetryEntry]:
        """Create a telemetry history with realistic variations"""
        history = []
        for i in range(entries):
            # Vary agent count slightly
            agent_count = base_agent_count + random.randint(-2, 2)
            agent_count = max(0, agent_count)  # Ensure non-negative

            history.append(MockFactory.create_telemetry_entry(
                manager_id=manager_id,
                agent_count=agent_count,
                hours_ago=i
            ))
        return history


class MockHTTPResponse:
    """Mock HTTP response for httpx testing"""

    def __init__(self, status_code: int = 200, json_data: Any = None, error: str | None = None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.error = error

    def json(self):
        """Return JSON data"""
        if self.error:
            raise Exception(self.error)
        return self._json_data

    @classmethod
    def success(cls, data: Any):
        """Create a successful response"""
        return cls(status_code=200, json_data=data)

    @classmethod
    def error(cls, status_code: int = 500, message: str = "Internal Server Error"):
        """Create an error response"""
        return cls(status_code=status_code, error=message)


class MockDatabase:
    """Mock database for testing"""

    def __init__(self):
        self.managers: list[MockManager] = []
        self.agents: list[MockDiscoveredAgent] = []
        self.telemetry: list[MockTelemetryEntry] = []

    def add_manager(self, manager: MockManager) -> int:
        """Add a manager and return its ID"""
        if not manager.id:
            manager.id = len(self.managers) + 1
        self.managers.append(manager)
        return manager.id

    def add_discovered_agent(self, agent: MockDiscoveredAgent):
        """Add a discovered agent"""
        self.agents.append(agent)

    def add_telemetry_entry(self, entry: MockTelemetryEntry):
        """Add a telemetry entry"""
        self.telemetry.append(entry)

    def get_enabled_managers(self) -> list[MockManager]:
        """Get all enabled managers"""
        return [m for m in self.managers if m.enabled]

    def get_recent_agents(self, minutes: int = 5) -> list[MockDiscoveredAgent]:
        """Get recently seen agents"""
        cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
        return [a for a in self.agents if a.last_seen > cutoff]

    def get_manager_telemetry(self, manager_id: int, limit: int = 10) -> list[MockTelemetryEntry]:
        """Get telemetry history for a manager"""
        entries = [t for t in self.telemetry if t.manager_id == manager_id]
        entries.sort(key=lambda x: x.collected_at, reverse=True)
        return entries[:limit]
