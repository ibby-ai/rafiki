/**
 * Type definitions for Rafiki Control Plane
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
  ENVIRONMENT: "development" | "staging" | "production";
  ESTIMATED_COST_PER_1K_CHARS_USD?: string;
  EVENT_BUS: DurableObjectNamespace;
  INTERNAL_AUTH_SECRET: string;
  MAX_QUEUED_PROMPTS_PER_SESSION?: string;
  MAX_SESSION_QUERY_BUDGET_REQUESTS?: string;
  MAX_SESSION_QUERY_BUDGET_USD?: string;

  // Environment variables
  MODAL_API_BASE_URL: string;

  // Secrets (set via wrangler secret put)
  MODAL_TOKEN_ID: string;
  MODAL_TOKEN_SECRET: string;
  PROMPT_QUEUE_ENTRY_EXPIRY_SECONDS?: string;
  RATE_LIMITER?: RateLimitBinding;
  // Durable Object bindings
  SESSION_AGENT: DurableObjectNamespace;

  // KV namespace for caching
  SESSION_CACHE: KVNamespace;
  SESSION_KEY_TTL_SECONDS?: string;
  SESSION_SIGNING_SECRET: string;
}

// =============================================================================
// API Request/Response Schemas (matches Modal schemas)
// =============================================================================

export interface QueryRequest {
  agent_type?: string;
  fork_session?: boolean;
  job_id?: string | null;
  question: string;
  session_id?: string | null;
  session_key?: string | null;
  tenant_id?: string | null;
  user_id?: string | null;
  warm_id?: string | null;
}

export interface QueryResponse {
  error?: string;
  messages: Message[];
  ok: boolean;
  session_id: string;
}

/**
 * Message from Modal backend.
 *
 * Note: Modal serializes messages with "type" field (user, assistant, system, result),
 * not "role". We keep both for compatibility, but "type" is the primary field from Modal.
 */
export interface Message {
  content: MessageContent[];
  /** Legacy role field - may not be present from Modal */
  role?: "user" | "assistant";
  /** Message type from Modal serialization (primary field) */
  type?: "user" | "assistant" | "system" | "result" | "stream_event";
}

export interface MessageContent {
  content?: unknown;
  input?: Record<string, unknown>;
  is_error?: boolean;
  name?: string;
  text?: string;
  tool_use_id?: string;
  type: "text" | "tool_use" | "tool_result";
}

export interface JobSubmitRequest {
  agent_type?: string;
  job_id?: string | null;
  question: string;
  schedule_at?: number | null;
  session_id?: string | null;
  session_key?: string | null;
  tenant_id?: string | null;
  user_id?: string | null;
  webhook?: WebhookConfig | null;
}

export interface WebhookConfig {
  headers?: Record<string, string>;
  max_attempts?: number;
  secret_ref?: string;
  signing_secret?: string;
  timeout_seconds?: number;
  url: string;
}

export interface JobSubmitResponse {
  job_id: string;
  ok: boolean;
}

export interface JobStatusResponse {
  agent_type?: string;
  artifacts?: ArtifactManifest | null;
  completed_at?: number | null;
  created_at: number;
  error?: string | null;
  job_id: string;
  question?: string;
  result?: QueryResponse | null;
  session_id?: string | null;
  started_at?: number | null;
  status: "queued" | "running" | "complete" | "failed" | "canceled";
  tenant_id?: string | null;
  user_id?: string | null;
}

export type ScheduleType = "one_off" | "cron";

export interface ScheduleCreateRequest {
  agent_type?: string | null;
  cron?: string | null;
  enabled?: boolean;
  metadata?: Record<string, unknown> | null;
  name: string;
  question: string;
  run_at?: number | null;
  schedule_type: ScheduleType;
  timezone?: string | null;
  webhook?: WebhookConfig | null;
}

export interface ScheduleUpdateRequest {
  agent_type?: string | null;
  cron?: string | null;
  enabled?: boolean | null;
  metadata?: Record<string, unknown> | null;
  name?: string | null;
  question?: string | null;
  run_at?: number | null;
  timezone?: string | null;
  webhook?: WebhookConfig | null;
}

export interface ScheduleResponse {
  agent_type?: string | null;
  created_at: number;
  cron?: string | null;
  enabled: boolean;
  last_error?: string | null;
  last_job_id?: string | null;
  last_run_at?: number | null;
  metadata?: Record<string, unknown> | null;
  name: string;
  next_run_at?: number | null;
  question: string;
  run_at?: number | null;
  schedule_id: string;
  schedule_type: ScheduleType;
  tenant_id?: string | null;
  timezone: string;
  updated_at: number;
  user_id?: string | null;
  webhook?: WebhookConfig | null;
}

export interface ScheduleListResponse {
  ok: boolean;
  schedules: ScheduleResponse[];
}

export interface ArtifactManifest {
  collected_at: number;
  files: ArtifactFile[];
  job_id: string;
  total_size_bytes: number;
  workspace_path: string;
}

export interface ArtifactFile {
  mime_type?: string;
  modified_at: number;
  path: string;
  size_bytes: number;
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
  | "presence_update"
  | "job_submitted"
  | "job_status"
  | "subscribe_session"
  | "unsubscribe_session"
  | "stop"
  | "ping"
  | "pong";

export interface WebSocketMessage {
  data: unknown;
  session_id: string;
  timestamp: number;
  type: WebSocketMessageType;
}

export interface SessionUpdateMessage extends WebSocketMessage {
  data: {
    status: "idle" | "executing" | "waiting_approval" | "error";
    current_prompt?: string;
    queue_length?: number;
  };
  type: "session_update";
}

export interface AssistantMessageMessage extends WebSocketMessage {
  data: {
    content: string;
    partial: boolean;
  };
  type: "assistant_message";
}

export interface ToolUseMessage extends WebSocketMessage {
  data: {
    tool_use_id: string;
    name: string;
    input: Record<string, unknown>;
  };
  type: "tool_use";
}

export interface QueryCompleteMessage extends WebSocketMessage {
  data: {
    messages: Message[];
    duration_ms: number;
    summary?: Record<string, unknown>;
  };
  type: "query_complete";
}

// =============================================================================
// Durable Object State Models
// =============================================================================

export interface SessionState {
  created_at: number;
  current_prompt?: string;
  last_active_at: number;
  modal_sandbox_id?: string;
  modal_sandbox_url?: string;
  session_id: string;
  session_key?: string;
  status: "idle" | "executing" | "waiting_approval" | "error";
  tenant_id?: string;
  user_id?: string;
}

export interface SessionMessage {
  content: MessageContent[];
  created_at: number;
  id: string;
  role: "user" | "assistant";
  session_id: string;
}

export interface PromptQueueEntry {
  agent_type: string;
  id: string;
  priority: number;
  question: string;
  queued_at: number;
  session_id: string;
  user_id?: string;
}

export interface ConnectionInfo {
  connected_at: number;
  connection_id: string;
  ip?: string;
  last_ping_at: number;
  session_ids: string[];
  tenant_id?: string;
  user_id?: string;
}

export interface PresenceUpdateMessage extends WebSocketMessage {
  data: {
    users_online: string[];
    connection_count: number;
    session_ids: string[];
    user_joined?: string;
    user_left?: string;
  };
  type: "presence_update";
}

export interface SubscribeSessionMessage extends WebSocketMessage {
  data: {
    session_id: string;
  };
  type: "subscribe_session";
}

export interface UnsubscribeSessionMessage extends WebSocketMessage {
  data: {
    session_id: string;
  };
  type: "unsubscribe_session";
}

export interface JobEventMessage extends WebSocketMessage {
  data: {
    job_id: string;
    status?: JobStatusResponse["status"];
    user_id?: string;
    tenant_id?: string;
    payload?: unknown;
  };
  type: "job_submitted" | "job_status";
}

// =============================================================================
// Modal Backend Integration
// =============================================================================

export interface ModalSandboxInfo {
  created_at: number;
  sandbox_id: string;
  sandbox_name: string;
  status: "running" | "terminated";
  url: string;
}

export interface ModalBackendRequest {
  body?: unknown;
  endpoint: string;
  headers?: Record<string, string>;
  method: "GET" | "POST" | "DELETE";
}

export interface ModalBackendResponse {
  data?: unknown;
  error?: string;
  ok: boolean;
  status: number;
}

// =============================================================================
// Authentication & Authorization
// =============================================================================

export interface SessionToken {
  expires_at: number;
  issued_at: number;
  session_id?: string;
  session_ids?: string[];
  tenant_id?: string;
  user_id?: string;
}

export interface AuthContext {
  expires_at: number;
  issued_at: number;
  session_ids?: string[];
  tenant_id?: string;
  user_id?: string;
}

export interface InternalAuthToken {
  expires_at: number;
  issued_at: number;
  service: "cloudflare-worker";
}

export interface ArtifactAccessToken {
  artifact_id: string;
  artifact_path: string;
  expires_at: number;
  issued_at: number;
  job_id: string;
  service: "cloudflare-worker-artifact";
  session_id: string;
  token_id: string;
}

export interface RateLimitResult {
  limit?: number;
  remaining?: number;
  reset?: number;
  success: boolean;
}

export interface RateLimitBinding {
  limit: (options: { key: string }) => Promise<RateLimitResult>;
}

// =============================================================================
// SSE Event Types (for Modal backend compatibility)
// =============================================================================

export interface SSEEvent {
  data: string; // JSON stringified
  event: "assistant" | "tool_use" | "result" | "done" | "error";
}
