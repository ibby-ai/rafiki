/**
 * Type definitions for Agent Sandbox Control Plane
 * 
 * This file defines the TypeScript interfaces and types for:
 * - API request/response schemas
 * - Durable Object state models
 * - WebSocket message formats
 * - Modal backend integration
 */

// =============================================================================
// Environment Bindings
// =============================================================================

export interface Env {
  // Durable Object bindings
  SESSION_AGENT: DurableObjectNamespace;
  EVENT_BUS: DurableObjectNamespace;
  
  // KV namespace for caching
  SESSION_CACHE: KVNamespace;
  
  // Environment variables
  MODAL_API_BASE_URL: string;
  ENVIRONMENT: "development" | "staging" | "production";
  
  // Secrets (set via wrangler secret put)
  MODAL_TOKEN_ID: string;
  MODAL_TOKEN_SECRET: string;
  INTERNAL_AUTH_SECRET: string;
  SESSION_SIGNING_SECRET: string;
}

// =============================================================================
// API Request/Response Schemas (matches Modal schemas)
// =============================================================================

export interface QueryRequest {
  question: string;
  agent_type?: string;
  session_id?: string | null;
  session_key?: string | null;
  fork_session?: boolean;
  job_id?: string | null;
  user_id?: string | null;
  warm_id?: string | null;
}

export interface QueryResponse {
  ok: boolean;
  session_id: string;
  messages: Message[];
  error?: string;
}

export interface Message {
  role: "user" | "assistant";
  content: MessageContent[];
}

export interface MessageContent {
  type: "text" | "tool_use" | "tool_result";
  text?: string;
  tool_use_id?: string;
  name?: string;
  input?: Record<string, unknown>;
  content?: unknown;
  is_error?: boolean;
}

export interface JobSubmitRequest {
  question: string;
  agent_type?: string;
  session_id?: string | null;
  session_key?: string | null;
  job_id?: string | null;
  user_id?: string | null;
  tenant_id?: string | null;
  schedule_at?: number | null;
  webhook?: WebhookConfig | null;
}

export interface WebhookConfig {
  url: string;
  headers?: Record<string, string>;
  signing_secret?: string;
  secret_ref?: string;
  timeout_seconds?: number;
  max_attempts?: number;
}

export interface JobSubmitResponse {
  ok: boolean;
  job_id: string;
}

export interface JobStatusResponse {
  job_id: string;
  status: "queued" | "running" | "complete" | "failed" | "canceled";
  created_at: number;
  started_at?: number | null;
  completed_at?: number | null;
  question?: string;
  agent_type?: string;
  session_id?: string | null;
  user_id?: string | null;
  tenant_id?: string | null;
  result?: QueryResponse | null;
  error?: string | null;
  artifacts?: ArtifactManifest | null;
}

export interface ArtifactManifest {
  job_id: string;
  workspace_path: string;
  files: ArtifactFile[];
  total_size_bytes: number;
  collected_at: number;
}

export interface ArtifactFile {
  path: string;
  size_bytes: number;
  modified_at: number;
  mime_type?: string;
}

// =============================================================================
// WebSocket Message Types
// =============================================================================

export type WebSocketMessageType = 
  | "session_update"
  | "assistant_message"
  | "tool_use"
  | "tool_result"
  | "query_start"
  | "query_complete"
  | "query_error"
  | "prompt_queued"
  | "execution_state"
  | "connection_ack"
  | "ping"
  | "pong";

export interface WebSocketMessage {
  type: WebSocketMessageType;
  session_id: string;
  timestamp: number;
  data: unknown;
}

export interface SessionUpdateMessage extends WebSocketMessage {
  type: "session_update";
  data: {
    status: "idle" | "executing" | "waiting_approval" | "error";
    current_prompt?: string;
    queue_length?: number;
  };
}

export interface AssistantMessageMessage extends WebSocketMessage {
  type: "assistant_message";
  data: {
    content: string;
    partial: boolean;
  };
}

export interface ToolUseMessage extends WebSocketMessage {
  type: "tool_use";
  data: {
    tool_use_id: string;
    name: string;
    input: Record<string, unknown>;
  };
}

export interface QueryCompleteMessage extends WebSocketMessage {
  type: "query_complete";
  data: {
    messages: Message[];
    duration_ms: number;
  };
}

// =============================================================================
// Durable Object State Models
// =============================================================================

export interface SessionState {
  session_id: string;
  session_key?: string;
  user_id?: string;
  tenant_id?: string;
  created_at: number;
  last_active_at: number;
  status: "idle" | "executing" | "waiting_approval" | "error";
  current_prompt?: string;
  modal_sandbox_id?: string;
  modal_sandbox_url?: string;
}

export interface SessionMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: MessageContent[];
  created_at: number;
}

export interface PromptQueueEntry {
  id: string;
  session_id: string;
  question: string;
  agent_type: string;
  user_id?: string;
  queued_at: number;
  priority: number;
}

export interface ConnectionInfo {
  connection_id: string;
  user_id?: string;
  tenant_id?: string;
  session_ids: string[];
  connected_at: number;
  last_ping_at: number;
}

// =============================================================================
// Modal Backend Integration
// =============================================================================

export interface ModalSandboxInfo {
  sandbox_id: string;
  sandbox_name: string;
  url: string;
  status: "running" | "terminated";
  created_at: number;
}

export interface ModalBackendRequest {
  endpoint: string;
  method: "GET" | "POST" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
}

export interface ModalBackendResponse {
  ok: boolean;
  status: number;
  data?: unknown;
  error?: string;
}

// =============================================================================
// Authentication & Authorization
// =============================================================================

export interface SessionToken {
  session_id: string;
  user_id?: string;
  tenant_id?: string;
  issued_at: number;
  expires_at: number;
}

export interface InternalAuthToken {
  service: "cloudflare-worker";
  issued_at: number;
  expires_at: number;
}

// =============================================================================
// SSE Event Types (for Modal backend compatibility)
// =============================================================================

export interface SSEEvent {
  event: "assistant" | "tool_use" | "result" | "done" | "error";
  data: string; // JSON stringified
}
