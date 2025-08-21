"""
Secure token management for agent OTLP endpoints
Tokens are write-only - can be set but not retrieved
"""

import os
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timezone
import aiofiles
import logging

logger = logging.getLogger(__name__)


class TokenManager:
    """Manages agent tokens securely"""
    
    def __init__(self, env_file_path: str = ".env"):
        self.env_file_path = Path(env_file_path)
        self.tokens_metadata_file = Path(".tokens_metadata.json")
        self._load_metadata()
        
    def _load_metadata(self):
        """Load token metadata (not the tokens themselves)"""
        if self.tokens_metadata_file.exists():
            with open(self.tokens_metadata_file, 'r') as f:
                self.metadata = json.load(f)
        else:
            self.metadata = {}
            
    def _save_metadata(self):
        """Save token metadata"""
        with open(self.tokens_metadata_file, 'w') as f:
            json.dump(self.metadata, f, indent=2, default=str)
            
    async def get_configured_agents(self) -> List[Dict]:
        """Get list of configured agents (without tokens)"""
        agents = []
        
        # Read from environment variables
        for key in os.environ:
            if key.startswith("AGENT_") and key.endswith("_TOKEN"):
                agent_name = key[6:-6].lower()
                url_key = f"AGENT_{agent_name.upper()}_URL"
                
                agent_info = {
                    "name": agent_name,
                    "url": os.environ.get(url_key, ""),
                    "configured": True,
                    "token_set": True,
                    "metadata": self.metadata.get(agent_name, {})
                }
                
                # Add metadata if available
                if agent_name in self.metadata:
                    agent_info["last_updated"] = self.metadata[agent_name].get("last_updated")
                    agent_info["updated_by"] = self.metadata[agent_name].get("updated_by")
                    agent_info["token_hash"] = self.metadata[agent_name].get("token_hash", "")[:8] + "..."
                    
                agents.append(agent_info)
                
        return agents
        
    async def set_agent_token(self, agent_name: str, token: str, url: str, updated_by: str) -> bool:
        """
        Set or update an agent token
        This updates the .env file securely
        """
        try:
            agent_name = agent_name.lower()
            
            # Read existing .env file
            env_lines = []
            if self.env_file_path.exists():
                async with aiofiles.open(self.env_file_path, 'r') as f:
                    content = await f.read()
                    env_lines = content.splitlines()
            
            # Find and update or add token lines
            token_key = f"AGENT_{agent_name.upper()}_TOKEN"
            url_key = f"AGENT_{agent_name.upper()}_URL"
            token_line = f"{token_key}={token}"
            url_line = f"{url_key}={url}"
            
            # Update existing or add new
            token_found = False
            url_found = False
            
            for i, line in enumerate(env_lines):
                if line.startswith(f"{token_key}="):
                    env_lines[i] = token_line
                    token_found = True
                elif line.startswith(f"{url_key}="):
                    env_lines[i] = url_line
                    url_found = True
                    
            # Add if not found
            if not token_found:
                # Find agent section or add at end
                agent_section_index = None
                for i, line in enumerate(env_lines):
                    if "Agent Service Tokens" in line:
                        agent_section_index = i
                        break
                        
                if agent_section_index is not None:
                    # Add after agent section header
                    insert_index = agent_section_index + 1
                    while insert_index < len(env_lines) and env_lines[insert_index].strip():
                        insert_index += 1
                    env_lines.insert(insert_index, f"\n# {agent_name.capitalize()} Agent")
                    env_lines.insert(insert_index + 1, token_line)
                    env_lines.insert(insert_index + 2, url_line)
                else:
                    # Add at end
                    env_lines.append(f"\n# {agent_name.capitalize()} Agent")
                    env_lines.append(token_line)
                    env_lines.append(url_line)
            elif not url_found:
                # Add URL after token
                for i, line in enumerate(env_lines):
                    if line.startswith(f"{token_key}="):
                        env_lines.insert(i + 1, url_line)
                        break
                        
            # Write back to .env file
            async with aiofiles.open(self.env_file_path, 'w') as f:
                await f.write('\n'.join(env_lines))
                
            # Update metadata
            self.metadata[agent_name] = {
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "updated_by": updated_by,
                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                "url": url
            }
            self._save_metadata()
            
            # Also update environment variable for current process
            os.environ[token_key] = token
            os.environ[url_key] = url
            
            logger.info(f"Token updated for agent {agent_name} by {updated_by}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to set token for {agent_name}: {e}")
            return False
            
    async def remove_agent_token(self, agent_name: str) -> bool:
        """Remove an agent token from configuration"""
        try:
            agent_name = agent_name.lower()
            
            # Read existing .env file
            if not self.env_file_path.exists():
                return False
                
            async with aiofiles.open(self.env_file_path, 'r') as f:
                content = await f.read()
                env_lines = content.splitlines()
            
            # Remove token and URL lines
            token_key = f"AGENT_{agent_name.upper()}_TOKEN"
            url_key = f"AGENT_{agent_name.upper()}_URL"
            
            new_lines = []
            skip_next_empty = False
            
            for line in env_lines:
                if line.startswith(f"{token_key}=") or line.startswith(f"{url_key}="):
                    skip_next_empty = True
                    continue
                elif skip_next_empty and not line.strip():
                    skip_next_empty = False
                    continue
                elif f"# {agent_name.capitalize()} Agent" in line:
                    continue
                else:
                    new_lines.append(line)
                    
            # Write back to .env file
            async with aiofiles.open(self.env_file_path, 'w') as f:
                await f.write('\n'.join(new_lines))
                
            # Remove from metadata
            if agent_name in self.metadata:
                del self.metadata[agent_name]
                self._save_metadata()
                
            # Remove from environment
            if token_key in os.environ:
                del os.environ[token_key]
            if url_key in os.environ:
                del os.environ[url_key]
                
            logger.info(f"Token removed for agent {agent_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to remove token for {agent_name}: {e}")
            return False
            
    async def validate_token(self, agent_name: str, token: str) -> bool:
        """
        Validate a token by checking its hash
        Used for confirming token updates
        """
        agent_name = agent_name.lower()
        if agent_name not in self.metadata:
            return False
            
        stored_hash = self.metadata[agent_name].get("token_hash", "")
        provided_hash = hashlib.sha256(token.encode()).hexdigest()
        
        return stored_hash == provided_hash