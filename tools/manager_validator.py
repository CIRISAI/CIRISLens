#!/usr/bin/env python3
"""
CIRISLens Manager Validator CLI Tool
Queries all registered managers and displays collected data for validation
"""

import asyncio
import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import asyncpg
import httpx
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.json import JSON
from rich.live import Live
from rich.layout import Layout
from rich.text import Text
import os

# Rich console for pretty output
console = Console()


class ManagerValidator:
    def __init__(self, database_url: str, verbose: bool = False):
        self.database_url = database_url
        self.verbose = verbose
        self.pool: Optional[asyncpg.Pool] = None
        
    async def connect(self):
        """Connect to database"""
        try:
            self.pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5)
            console.print("[green]✓[/green] Connected to database")
        except Exception as e:
            console.print(f"[red]✗[/red] Database connection failed: {e}")
            raise
            
    async def disconnect(self):
        """Disconnect from database"""
        if self.pool:
            await self.pool.close()
            
    async def get_managers(self) -> List[Dict]:
        """Get all registered managers"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, name, url, enabled, last_seen, last_error, 
                       collection_interval_seconds, added_at, description
                FROM managers
                ORDER BY name
            """)
            return [dict(row) for row in rows]
            
    async def test_manager_connection(self, manager: Dict) -> Dict:
        """Test connection to a manager"""
        result = {
            "manager": manager["name"],
            "url": manager["url"],
            "status": None,
            "agents": [],
            "error": None,
            "response_time": None
        }
        
        start_time = datetime.now()
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Test status endpoint
                status_response = await client.get(f"{manager['url']}/manager/v1/status")
                
                if status_response.status_code == 200:
                    result["status"] = status_response.json()
                    
                # Test agents endpoint
                agents_response = await client.get(f"{manager['url']}/manager/v1/agents")
                
                if agents_response.status_code == 200:
                    result["agents"] = agents_response.json()
                    
                result["response_time"] = (datetime.now() - start_time).total_seconds()
                
        except httpx.TimeoutException:
            result["error"] = "Connection timeout"
        except httpx.ConnectError:
            result["error"] = "Connection refused"
        except Exception as e:
            result["error"] = str(e)
            
        return result
        
    async def get_discovered_agents(self) -> List[Dict]:
        """Get all discovered agents from database"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT da.*, m.name as manager_name, m.url as manager_url
                FROM discovered_agents da
                JOIN managers m ON da.manager_id = m.id
                WHERE da.last_seen > NOW() - INTERVAL '5 minutes'
                ORDER BY da.agent_name
            """)
            return [dict(row) for row in rows]
            
    async def get_telemetry_history(self, manager_id: int, limit: int = 10) -> List[Dict]:
        """Get recent telemetry history for a manager"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT collected_at, agent_count, status, version, uptime_seconds
                FROM manager_telemetry
                WHERE manager_id = $1
                ORDER BY collected_at DESC
                LIMIT $2
            """, manager_id, limit)
            return [dict(row) for row in rows]
            
    def display_managers_table(self, managers: List[Dict]):
        """Display managers in a table"""
        table = Table(title="Registered Managers", show_header=True)
        table.add_column("ID", style="cyan", width=4)
        table.add_column("Name", style="green")
        table.add_column("URL", style="blue")
        table.add_column("Enabled", style="yellow")
        table.add_column("Interval", style="magenta")
        table.add_column("Last Seen", style="white")
        table.add_column("Status", style="red")
        
        for manager in managers:
            last_seen = manager["last_seen"]
            if last_seen:
                # Calculate time ago
                time_ago = datetime.now(timezone.utc) - last_seen.replace(tzinfo=timezone.utc)
                last_seen_str = f"{int(time_ago.total_seconds())}s ago"
            else:
                last_seen_str = "Never"
                
            status = "[green]OK[/green]" if not manager["last_error"] else f"[red]Error[/red]"
            
            table.add_row(
                str(manager["id"]),
                manager["name"],
                manager["url"],
                "✓" if manager["enabled"] else "✗",
                f"{manager['collection_interval_seconds']}s",
                last_seen_str,
                status
            )
            
        console.print(table)
        
    def display_agents_table(self, agents: List[Dict]):
        """Display discovered agents in a table"""
        table = Table(title="Discovered Agents", show_header=True)
        table.add_column("Agent ID", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Manager", style="blue")
        table.add_column("Status", style="yellow")
        table.add_column("Cognitive State", style="magenta")
        table.add_column("Version", style="white")
        table.add_column("Health", style="red")
        table.add_column("Last Seen", style="white")
        
        for agent in agents:
            last_seen = agent["last_seen"]
            if last_seen:
                time_ago = datetime.now(timezone.utc) - last_seen.replace(tzinfo=timezone.utc)
                last_seen_str = f"{int(time_ago.total_seconds())}s ago"
            else:
                last_seen_str = "Unknown"
                
            health_color = "green" if agent["health"] == "healthy" else "red"
            
            table.add_row(
                agent["agent_id"],
                agent["agent_name"] or "N/A",
                agent["manager_name"],
                agent["status"] or "unknown",
                agent["cognitive_state"] or "N/A",
                agent["version"] or "N/A",
                f"[{health_color}]{agent['health'] or 'unknown'}[/{health_color}]",
                last_seen_str
            )
            
        console.print(table)
        
    def display_connection_test(self, results: List[Dict]):
        """Display connection test results"""
        for result in results:
            if result["error"]:
                console.print(Panel(
                    f"[red]Connection Failed[/red]\n"
                    f"Manager: {result['manager']}\n"
                    f"URL: {result['url']}\n"
                    f"Error: {result['error']}",
                    title="❌ Connection Test Failed",
                    border_style="red"
                ))
            else:
                agent_count = len(result["agents"]) if isinstance(result["agents"], list) else 0
                status_info = result["status"] if result["status"] else {}
                
                content = f"[green]Connection Successful[/green]\n"
                content += f"Manager: {result['manager']}\n"
                content += f"URL: {result['url']}\n"
                content += f"Response Time: {result['response_time']:.2f}s\n"
                content += f"Agents Found: {agent_count}\n"
                
                if status_info:
                    content += f"Manager Version: {status_info.get('version', 'N/A')}\n"
                    content += f"Manager Status: {status_info.get('status', 'N/A')}\n"
                    if 'uptime_seconds' in status_info:
                        uptime_hours = status_info['uptime_seconds'] / 3600
                        content += f"Manager Uptime: {uptime_hours:.1f} hours\n"
                
                console.print(Panel(
                    content,
                    title="✅ Connection Test Successful",
                    border_style="green"
                ))
                
                if self.verbose and result["agents"]:
                    console.print("\n[bold]Agent Details:[/bold]")
                    if isinstance(result["agents"], list):
                        for agent in result["agents"][:5]:  # Show first 5 agents
                            console.print(f"  • {agent.get('agent_name', 'N/A')} "
                                        f"({agent.get('agent_id', 'N/A')}) - "
                                        f"{agent.get('status', 'unknown')}")
                    
    async def monitor_live(self, interval: int = 5):
        """Live monitoring mode"""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="managers"),
            Layout(name="agents")
        )
        
        with Live(layout, refresh_per_second=1) as live:
            while True:
                try:
                    # Get current data
                    managers = await self.get_managers()
                    agents = await self.get_discovered_agents()
                    
                    # Update header
                    header_text = Text()
                    header_text.append("CIRISLens Manager Monitor", style="bold magenta")
                    header_text.append(f"\nLast Update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    header_text.append(f" | Managers: {len(managers)} | Agents: {len(agents)}")
                    layout["header"].update(Panel(header_text))
                    
                    # Update managers table
                    managers_table = Table(show_header=True)
                    managers_table.add_column("Manager", style="cyan")
                    managers_table.add_column("Status", style="green")
                    managers_table.add_column("Agents", style="yellow")
                    
                    for manager in managers:
                        status = "✓ Active" if manager["enabled"] and not manager["last_error"] else "✗ Error"
                        # Count agents for this manager
                        agent_count = sum(1 for a in agents if a.get("manager_name") == manager["name"])
                        managers_table.add_row(manager["name"], status, str(agent_count))
                        
                    layout["managers"].update(Panel(managers_table, title="Managers"))
                    
                    # Update agents table
                    agents_table = Table(show_header=True)
                    agents_table.add_column("Agent", style="cyan")
                    agents_table.add_column("State", style="green")
                    agents_table.add_column("Health", style="yellow")
                    
                    for agent in agents[:10]:  # Show top 10 agents
                        agents_table.add_row(
                            agent["agent_name"] or agent["agent_id"],
                            agent["cognitive_state"] or "N/A",
                            agent["health"] or "unknown"
                        )
                        
                    layout["agents"].update(Panel(agents_table, title="Recent Agents"))
                    
                    await asyncio.sleep(interval)
                    
                except KeyboardInterrupt:
                    break
                except Exception as e:
                    console.print(f"[red]Monitor error: {e}[/red]")
                    await asyncio.sleep(interval)


async def main():
    parser = argparse.ArgumentParser(description="CIRISLens Manager Validator")
    parser.add_argument("--database-url", 
                       default=os.getenv("DATABASE_URL", "postgresql://user:password@host:5432/dbname"),
                       help="PostgreSQL database URL")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Verbose output")
    parser.add_argument("--test-connections", "-t", action="store_true",
                       help="Test connections to all managers")
    parser.add_argument("--monitor", "-m", action="store_true",
                       help="Live monitoring mode")
    parser.add_argument("--interval", "-i", type=int, default=5,
                       help="Monitor refresh interval in seconds")
    parser.add_argument("--show-history", action="store_true",
                       help="Show telemetry history")
    parser.add_argument("--json", action="store_true",
                       help="Output in JSON format")
    
    args = parser.parse_args()
    
    validator = ManagerValidator(args.database_url, args.verbose)
    
    try:
        await validator.connect()
        
        if args.monitor:
            console.print("[bold]Starting live monitor mode (Ctrl+C to exit)...[/bold]")
            await validator.monitor_live(args.interval)
            
        else:
            # Get managers
            managers = await validator.get_managers()
            
            if args.json:
                # JSON output mode
                output = {
                    "managers": managers,
                    "discovered_agents": await validator.get_discovered_agents()
                }
                
                if args.test_connections:
                    connection_tests = []
                    for manager in managers:
                        if manager["enabled"]:
                            result = await validator.test_manager_connection(manager)
                            connection_tests.append(result)
                    output["connection_tests"] = connection_tests
                    
                print(json.dumps(output, default=str, indent=2))
                
            else:
                # Display managers
                console.print("\n[bold magenta]CIRISLens Manager Validation Report[/bold magenta]\n")
                validator.display_managers_table(managers)
                
                # Test connections if requested
                if args.test_connections:
                    console.print("\n[bold]Testing Manager Connections...[/bold]\n")
                    for manager in managers:
                        if manager["enabled"]:
                            result = await validator.test_manager_connection(manager)
                            validator.display_connection_test([result])
                
                # Display discovered agents
                console.print("\n")
                agents = await validator.get_discovered_agents()
                validator.display_agents_table(agents)
                
                # Show history if requested
                if args.show_history:
                    console.print("\n[bold]Telemetry History:[/bold]\n")
                    for manager in managers:
                        history = await validator.get_telemetry_history(manager["id"], limit=5)
                        if history:
                            console.print(f"\n[cyan]{manager['name']}:[/cyan]")
                            for entry in history:
                                console.print(f"  • {entry['collected_at']}: "
                                            f"{entry['agent_count']} agents, "
                                            f"status={entry['status']}")
                
                # Summary
                console.print("\n[bold]Summary:[/bold]")
                console.print(f"  • Total Managers: {len(managers)}")
                console.print(f"  • Enabled Managers: {sum(1 for m in managers if m['enabled'])}")
                console.print(f"  • Total Discovered Agents: {len(agents)}")
                console.print(f"  • Unique Agent IDs: {len(set(a['agent_id'] for a in agents))}")
                
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
        
    finally:
        await validator.disconnect()


if __name__ == "__main__":
    asyncio.run(main())