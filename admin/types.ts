/**
 * TypeScript types for CIRISLens Admin Interface
 * Mirrors the CIRISManager schema for consistency
 */

// Agent type from manager API
export interface Agent {
  agent_id: string;
  name: string;
  status: 'running' | 'stopped' | 'error';
  cognitive_state: 'WORK' | 'WAKEUP' | 'SHUTDOWN' | 'DREAM' | 'PLAY' | 'SOLITUDE';
  version: string;
  codename: string;
  api_port: number;
  health: 'healthy' | 'unhealthy' | 'unknown';
  container_id?: string;
  deployment_type?: 'docker' | 'kubernetes' | 'bare-metal';
  ip_address?: string;
  created_at?: string;
  updated_at?: string;
}

// Manager type for tracking multiple manager instances
export interface Manager {
  manager_id: string;
  name: string;
  url: string;
  status: 'online' | 'offline' | 'error';
  version?: string;
  last_seen: string;
  agent_count: number;
}

// Telemetry configuration per agent
export interface TelemetryConfig {
  agent_id: string;
  enabled: boolean;
  collection_interval: number; // seconds
  metrics_enabled: boolean;
  traces_enabled: boolean;
  logs_enabled: boolean;
  last_updated: string;
  updated_by: string;
}

// Dashboard visibility configuration
export interface DashboardVisibility {
  agent_id: string;
  public_visible: boolean;
  show_metrics: boolean;
  show_traces: boolean;
  show_logs: boolean;
  show_cognitive_state: boolean;
  show_health_status: boolean;
  redact_pii: boolean;
  last_updated: string;
  updated_by: string;
}

// Combined view for admin interface
export interface AgentAdminView {
  agent: Agent;
  telemetry: TelemetryConfig;
  visibility: DashboardVisibility;
  manager: Manager;
}

// OAuth user info
export interface OAuthUser {
  email: string;
  name: string;
  picture?: string;
  hd?: string; // hosted domain for Google Workspace
}

// API Response types
export interface ApiResponse<T> {
  data: T;
  error?: string;
  timestamp: string;
}

export interface ManagerDiscoveryResponse {
  managers: Manager[];
  total: number;
}

export interface AgentDiscoveryResponse {
  agents: Agent[];
  total: number;
  manager_id: string;
}

// Configuration update requests
export interface UpdateTelemetryRequest {
  agent_id: string;
  enabled?: boolean;
  collection_interval?: number;
  metrics_enabled?: boolean;
  traces_enabled?: boolean;
  logs_enabled?: boolean;
}

export interface UpdateVisibilityRequest {
  agent_id: string;
  public_visible?: boolean;
  show_metrics?: boolean;
  show_traces?: boolean;
  show_logs?: boolean;
  show_cognitive_state?: boolean;
  show_health_status?: boolean;
  redact_pii?: boolean;
}