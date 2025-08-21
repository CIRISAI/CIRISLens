#!/usr/bin/env python3
"""
Test OTLP Collection from CIRIS Agents
Verifies that we can collect telemetry from Datum and Sage
"""

import asyncio
import httpx
import json
import os
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.json import JSON
from typing import Dict, Optional

# Load environment
from dotenv import load_dotenv
load_dotenv()

console = Console()


class OTLPTester:
    def __init__(self):
        self.agents = {
            "datum": {
                "url": os.getenv("AGENT_DATUM_URL", "https://agents.ciris.ai/api/datum"),
                "token": os.getenv("AGENT_DATUM_TOKEN", "")
            },
            "sage": {
                "url": os.getenv("AGENT_SAGE_URL", "https://agents.ciris.ai/api/sage-2wnuc8"),
                "token": os.getenv("AGENT_SAGE_TOKEN", "")
            }
        }
        
    async def test_agent(self, name: str, config: Dict) -> Dict:
        """Test OTLP endpoints for a single agent"""
        result = {
            "agent": name,
            "url": config["url"],
            "metrics": None,
            "traces": None,
            "logs": None,
            "errors": []
        }
        
        if not config["token"]:
            result["errors"].append("No token configured")
            return result
            
        headers = {
            "Authorization": f"Bearer {config['token']}",
            "Accept": "application/json"
        }
        
        console.print(f"\n[bold cyan]Testing {name.upper()} agent...[/bold cyan]")
        console.print(f"URL: {config['url']}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Test metrics endpoint
            console.print("  📊 Testing metrics endpoint...")
            result["metrics"] = await self._test_endpoint(
                client, 
                f"{config['url']}/v1/telemetry/otlp/metrics",
                headers,
                "metrics"
            )
            
            # Test traces endpoint
            console.print("  🔍 Testing traces endpoint...")
            result["traces"] = await self._test_endpoint(
                client,
                f"{config['url']}/v1/telemetry/otlp/traces",
                headers,
                "traces"
            )
            
            # Test logs endpoint
            console.print("  📝 Testing logs endpoint...")
            result["logs"] = await self._test_endpoint(
                client,
                f"{config['url']}/v1/telemetry/otlp/logs",
                headers,
                "logs"
            )
            
        return result
        
    async def _test_endpoint(self, client: httpx.AsyncClient, url: str, 
                            headers: Dict, signal_type: str) -> Optional[Dict]:
        """Test a single OTLP endpoint"""
        try:
            response = await client.get(url, headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                
                # Analyze the response
                if signal_type == "metrics" and "resourceMetrics" in data:
                    metrics_count = 0
                    for rm in data.get("resourceMetrics", []):
                        for sm in rm.get("scopeMetrics", []):
                            metrics_count += len(sm.get("metrics", []))
                    
                    console.print(f"    ✅ Success: {metrics_count} metrics found")
                    return {
                        "status": "success",
                        "count": metrics_count,
                        "sample": data["resourceMetrics"][0] if data.get("resourceMetrics") else None
                    }
                    
                elif signal_type == "traces" and "resourceSpans" in data:
                    span_count = 0
                    for rs in data.get("resourceSpans", []):
                        for ss in rs.get("scopeSpans", []):
                            span_count += len(ss.get("spans", []))
                    
                    console.print(f"    ✅ Success: {span_count} spans found")
                    return {
                        "status": "success",
                        "count": span_count,
                        "sample": data["resourceSpans"][0] if data.get("resourceSpans") else None
                    }
                    
                elif signal_type == "logs" and "resourceLogs" in data:
                    log_count = 0
                    for rl in data.get("resourceLogs", []):
                        for sl in rl.get("scopeLogs", []):
                            log_count += len(sl.get("logRecords", []))
                    
                    console.print(f"    ✅ Success: {log_count} log records found")
                    return {
                        "status": "success",
                        "count": log_count,
                        "sample": data["resourceLogs"][0] if data.get("resourceLogs") else None
                    }
                else:
                    console.print(f"    ⚠️ No data in response")
                    return {"status": "empty", "data": data}
                    
            else:
                console.print(f"    ❌ HTTP {response.status_code}: {response.text[:100]}")
                return {"status": "error", "code": response.status_code, "message": response.text[:200]}
                
        except Exception as e:
            console.print(f"    ❌ Error: {str(e)}")
            return {"status": "error", "message": str(e)}
            
    def display_results(self, results: list):
        """Display test results in a formatted table"""
        table = Table(title="OTLP Collection Test Results", show_header=True)
        table.add_column("Agent", style="cyan")
        table.add_column("Metrics", style="green")
        table.add_column("Traces", style="yellow")
        table.add_column("Logs", style="blue")
        table.add_column("Status", style="magenta")
        
        for result in results:
            metrics_status = "❌ Failed"
            traces_status = "❌ Failed"
            logs_status = "❌ Failed"
            overall_status = "❌ Failed"
            
            if result["metrics"] and result["metrics"].get("status") == "success":
                metrics_status = f"✅ {result['metrics']['count']} metrics"
                
            if result["traces"] and result["traces"].get("status") == "success":
                traces_status = f"✅ {result['traces']['count']} spans"
                
            if result["logs"] and result["logs"].get("status") == "success":
                logs_status = f"✅ {result['logs']['count']} logs"
                
            if result["errors"]:
                overall_status = f"❌ {result['errors'][0]}"
            elif all([
                result["metrics"] and result["metrics"].get("status") == "success",
                result["traces"] and result["traces"].get("status") == "success",
                result["logs"] and result["logs"].get("status") == "success"
            ]):
                overall_status = "✅ All OK"
            else:
                overall_status = "⚠️ Partial"
                
            table.add_row(
                result["agent"].upper(),
                metrics_status,
                traces_status,
                logs_status,
                overall_status
            )
            
        console.print("\n")
        console.print(table)
        
    def show_sample_data(self, results: list):
        """Show sample data from successful collections"""
        for result in results:
            if result["metrics"] and result["metrics"].get("sample"):
                console.print(f"\n[bold]Sample metrics from {result['agent'].upper()}:[/bold]")
                
                sample = result["metrics"]["sample"]
                if "resource" in sample and "attributes" in sample["resource"]:
                    console.print("Resource attributes:")
                    for attr in sample["resource"]["attributes"][:5]:
                        key = attr.get("key", "")
                        value = attr.get("value", {})
                        if "stringValue" in value:
                            console.print(f"  • {key}: {value['stringValue']}")
                            
                if "scopeMetrics" in sample and sample["scopeMetrics"]:
                    metrics = sample["scopeMetrics"][0].get("metrics", [])[:3]
                    console.print(f"Sample metrics ({len(metrics)} shown):")
                    for metric in metrics:
                        console.print(f"  • {metric.get('name', 'unknown')}")


async def main():
    console.print("[bold magenta]CIRISLens OTLP Collection Test[/bold magenta]")
    console.print("=" * 50)
    
    tester = OTLPTester()
    
    # Check tokens are configured
    console.print("\n[bold]Checking configuration...[/bold]")
    for name, config in tester.agents.items():
        if config["token"]:
            # Mask token for display
            masked_token = config["token"][:20] + "..." if len(config["token"]) > 20 else "***"
            console.print(f"  ✅ {name.upper()}: Token configured ({masked_token})")
        else:
            console.print(f"  ❌ {name.upper()}: No token found")
    
    # Test each agent
    results = []
    for name, config in tester.agents.items():
        result = await tester.test_agent(name, config)
        results.append(result)
        
    # Display results
    tester.display_results(results)
    
    # Show sample data
    tester.show_sample_data(results)
    
    # Summary
    console.print("\n[bold]Summary:[/bold]")
    successful = sum(1 for r in results if not r["errors"] and all([
        r["metrics"] and r["metrics"].get("status") == "success",
        r["traces"] and r["traces"].get("status") == "success", 
        r["logs"] and r["logs"].get("status") == "success"
    ]))
    
    if successful == len(results):
        console.print(f"[green]✅ All {len(results)} agents are responding correctly![/green]")
        console.print("[green]OTLP collection is ready to use.[/green]")
    elif successful > 0:
        console.print(f"[yellow]⚠️ {successful}/{len(results)} agents are working[/yellow]")
    else:
        console.print("[red]❌ No agents are responding correctly[/red]")
        console.print("[red]Please check tokens and agent availability[/red]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Test interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"\n[red]Test failed: {e}[/red]")