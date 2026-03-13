/**
 * Type definitions for Rafiki Control Plane
 *
 * This file defines the TypeScript interfaces and types for:
 * - environment bindings and non-contract runtime types
 * - Durable Object state models
 * - WebSocket message formats
 * - Modal backend integration
 */

import type {
  ArtifactManifest as ContractArtifactManifest,
  JobStatusResponse as ContractJobStatusResponse,
  JobSubmitRequest as ContractJobSubmitRequest,
  JobSubmitResponse as ContractJobSubmitResponse,
  Message as ContractMessage,
  MessageContent as ContractMessageContent,
  QueryRequest as ContractQueryRequest,
  QueryResponse as ContractQueryResponse,
  QueuePromptRequest as ContractQueuePromptRequest,
  ScheduleCreateRequest as ContractScheduleCreateRequest,
  ScheduleListResponse as ContractScheduleListResponse,
  ScheduleResponse as ContractScheduleResponse,
  ScheduleType as ContractScheduleType,
  ScheduleUpdateRequest as ContractScheduleUpdateRequest,
  SessionStopMode as ContractSessionStopMode,
  SessionStopRequest as ContractSessionStopRequest,
  SessionStopResponse as ContractSessionStopResponse,
  StreamingQueryRequest as ContractStreamingQueryRequest,
  Summary as ContractSummary,
  WebhookConfig as ContractWebhookConfig,
} from "./contracts/public-api";

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

export type QueryRequest = ContractQueryRequest;
export type StreamingQueryRequest = ContractStreamingQueryRequest;
export type QueryResponse = ContractQueryResponse;
export type Message = ContractMessage;
export type MessageContent = ContractMessageContent;
export type Summary = ContractSummary;
export type WebhookConfig = ContractWebhookConfig;
export type JobSubmitRequest = ContractJobSubmitRequest;
export type JobSubmitResponse = ContractJobSubmitResponse;
export type JobStatusResponse = ContractJobStatusResponse;
export type QueuePromptRequest = ContractQueuePromptRequest;
export type ScheduleType = ContractScheduleType;
export type ScheduleCreateRequest = ContractScheduleCreateRequest;
export type ScheduleUpdateRequest = ContractScheduleUpdateRequest;
export type ScheduleResponse = ContractScheduleResponse;
export type ScheduleListResponse = ContractScheduleListResponse;
export type SessionStopMode = ContractSessionStopMode;
export type SessionStopRequest = ContractSessionStopRequest;
export type SessionStopResponse = ContractSessionStopResponse;
export type ArtifactManifest = ContractArtifactManifest;

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
    summary?: Summary;
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
