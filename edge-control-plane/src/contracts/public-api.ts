/**
 * Runtime-validated request and response contracts for the public Worker API.
 *
 * These schemas intentionally model the client-facing Worker contract and the
 * Worker-to-Modal JSON seams that must reject malformed data deterministically.
 *
 * @module contracts/public-api
 */

import { z } from "zod";

const traceIdPattern = /^[A-Za-z0-9._:-]{1,128}$/;

const trimmedString = z.string().trim();
const optionalNullableString = z.string().trim().nullable().optional();
const strictRecord = z.record(z.unknown());

/**
 * Runtime schema for a single content block in a serialized agent message.
 */
export const MessageContentSchema = z
  .object({
    type: z.enum(["text", "thinking", "tool_use", "tool_result"]),
    text: z.string().optional(),
    thinking: z.string().optional(),
    signature: z.string().optional(),
    id: z.string().optional(),
    name: z.string().optional(),
    input: strictRecord.optional(),
    tool_use_id: z.string().optional(),
    content: z.unknown().optional(),
    is_error: z.boolean().optional(),
  })
  .strict();

/**
 * Runtime schema for a serialized agent message from the Modal backend.
 */
export const MessageSchema = z
  .object({
    content: z
      .union([z.array(MessageContentSchema), z.string(), z.null()])
      .optional(),
    role: z.enum(["user", "assistant"]).optional(),
    type: z
      .enum(["user", "assistant", "system", "result", "stream_event"])
      .optional(),
  })
  .passthrough();

/**
 * Runtime schema for the summary block returned with completed query responses.
 */
export const SummarySchema = z
  .object({
    text: z.string().nullable().optional(),
    is_complete: z.boolean(),
    trace_id: z.string().nullable().optional(),
    openai_trace_id: z.string().nullable().optional(),
    subtype: z.string().nullable().optional(),
    duration_ms: z.number().int().nullable().optional(),
    duration_api_ms: z.number().int().nullable().optional(),
    is_error: z.boolean().nullable().optional(),
    num_turns: z.number().int().nullable().optional(),
    session_id: z.string().nullable().optional(),
    total_cost_usd: z.number().nullable().optional(),
    usage: strictRecord.nullable().optional(),
    result: z.string().nullable().optional(),
    structured_output: z.unknown().nullable().optional(),
  })
  .strict();

/**
 * Runtime schema for public `/query` and internal Modal query responses.
 */
export const QueryResponseSchema = z
  .object({
    ok: z.boolean(),
    messages: z.array(MessageSchema),
    summary: SummarySchema,
    session_id: z.string().nullable().optional(),
  })
  .strict();

/**
 * Runtime schema for public query requests.
 */
export const QueryRequestSchema = z
  .object({
    question: trimmedString.max(20_000),
    agent_type: trimmedString.optional(),
    fork_session: z.boolean().optional(),
    job_id: optionalNullableString,
    session_id: optionalNullableString,
    session_key: optionalNullableString,
    tenant_id: optionalNullableString,
    trace_id: z.string().regex(traceIdPattern).nullable().optional(),
    user_id: optionalNullableString,
    warm_id: optionalNullableString,
  })
  .strict();

/**
 * Runtime schema for authenticated WebSocket query messages.
 *
 * Actor scope for streaming queries is derived from the authenticated
 * connection context, so message payloads must not carry identity fields.
 */
export const StreamingQueryRequestSchema = z
  .object({
    question: trimmedString.max(20_000),
    agent_type: trimmedString.optional(),
    fork_session: z.boolean().optional(),
    job_id: optionalNullableString,
    trace_id: z.string().regex(traceIdPattern).nullable().optional(),
    warm_id: optionalNullableString,
  })
  .strict();

/**
 * Runtime schema for queued prompt requests routed through the SessionAgent DO.
 */
export const QueuePromptRequestSchema = z
  .object({
    question: trimmedString.max(20_000),
    agent_type: trimmedString.optional(),
  })
  .strict();

/**
 * Runtime schema for webhook callback configuration included on job and schedule requests.
 */
export const WebhookConfigSchema = z
  .object({
    url: z.string().url(),
    headers: z.record(z.string()).nullable().optional(),
    signing_secret: z.string().nullable().optional(),
    secret_ref: z.string().nullable().optional(),
    timeout_seconds: z.number().int().positive().nullable().optional(),
    max_attempts: z.number().int().positive().nullable().optional(),
  })
  .strict();

/**
 * Runtime schema for public job submission requests.
 */
export const JobSubmitRequestSchema = z
  .object({
    question: trimmedString.max(20_000),
    agent_type: z.string().nullable().optional(),
    session_id: optionalNullableString,
    session_key: optionalNullableString,
    job_id: optionalNullableString,
    tenant_id: optionalNullableString,
    user_id: optionalNullableString,
    schedule_at: z.number().int().nullable().optional(),
    webhook: WebhookConfigSchema.nullable().optional(),
    metadata: strictRecord.nullable().optional(),
  })
  .strict();

/**
 * Runtime schema for the Worker-owned job enqueue acknowledgement.
 */
export const JobSubmitResponseSchema = z
  .object({
    ok: z.boolean(),
    job_id: z.string(),
  })
  .strict();

/**
 * Runtime schema for a single artifact entry returned by the Modal backend.
 */
export const ArtifactEntrySchema = z
  .object({
    path: z.string(),
    size_bytes: z.number().int().nonnegative().nullable().optional(),
    content_type: z.string().nullable().optional(),
    checksum_sha256: z.string().nullable().optional(),
    created_at: z.number().int().nullable().optional(),
    modified_at: z.number().int().nullable().optional(),
  })
  .strict();

/**
 * Runtime schema for the artifact manifest returned by the Modal backend.
 */
export const ArtifactManifestSchema = z
  .object({
    root: z.string().nullable().optional(),
    files: z.array(ArtifactEntrySchema),
  })
  .strict();

/**
 * Runtime schema for webhook delivery metadata included in job status responses.
 */
export const WebhookStatusSchema = z
  .object({
    url: z.string().url().nullable().optional(),
    secret_ref: z.string().nullable().optional(),
    attempts: z.number().int().nullable().optional(),
    last_status: z.number().int().nullable().optional(),
    last_error: z.string().nullable().optional(),
    delivered_at: z.number().int().nullable().optional(),
  })
  .strict();

/**
 * Runtime schema for job status responses returned by the Modal backend.
 *
 * `session_id` is mandatory because Worker ownership checks fail closed when
 * the backend omits authoritative actor-scope identity.
 */
export const JobStatusResponseSchema = z
  .object({
    ok: z.boolean().default(true),
    job_id: z.string(),
    status: z.enum(["queued", "running", "complete", "failed", "canceled"]),
    result: QueryResponseSchema.nullable().optional(),
    error: z.string().nullable().optional(),
    created_at: z.number().int().nullable().optional(),
    updated_at: z.number().int().nullable().optional(),
    canceled_at: z.number().int().nullable().optional(),
    question: z.string().nullable().optional(),
    attempts: z.number().int().nullable().optional(),
    tenant_id: z.string().nullable().optional(),
    user_id: z.string().nullable().optional(),
    schedule_at: z.number().int().nullable().optional(),
    webhook: WebhookStatusSchema.nullable().optional(),
    artifacts: ArtifactManifestSchema.nullable().optional(),
    metadata: strictRecord.nullable().optional(),
    started_at: z.number().int().nullable().optional(),
    completed_at: z.number().int().nullable().optional(),
    queue_latency_ms: z.number().int().nullable().optional(),
    duration_ms: z.number().int().nullable().optional(),
    agent_duration_ms: z.number().int().nullable().optional(),
    agent_duration_api_ms: z.number().int().nullable().optional(),
    usage: strictRecord.nullable().optional(),
    total_cost_usd: z.number().nullable().optional(),
    num_turns: z.number().int().nullable().optional(),
    session_id: z.string(),
    tool_call_count: z.number().int().nullable().optional(),
    models: z.array(z.string()).nullable().optional(),
    sandbox_id: z.string().nullable().optional(),
  })
  .strict();

/**
 * Runtime schema for the public job artifact listing response.
 */
export const ArtifactListResponseSchema = z
  .object({
    ok: z.boolean().default(true),
    job_id: z.string(),
    artifacts: ArtifactManifestSchema,
  })
  .strict();

/**
 * Runtime schema for supported schedule kinds.
 */
export const ScheduleTypeSchema = z.enum(["one_off", "cron"]);

/**
 * Runtime schema for public schedule creation requests.
 */
export const ScheduleCreateRequestSchema = z
  .object({
    name: trimmedString,
    question: trimmedString.max(20_000),
    agent_type: z.string().nullable().optional(),
    schedule_type: ScheduleTypeSchema,
    run_at: z.number().int().nullable().optional(),
    cron: z.string().nullable().optional(),
    timezone: z.string().nullable().optional(),
    enabled: z.boolean().optional(),
    webhook: WebhookConfigSchema.nullable().optional(),
    metadata: strictRecord.nullable().optional(),
  })
  .strict();

/**
 * Runtime schema for public partial schedule updates.
 */
export const ScheduleUpdateRequestSchema = z
  .object({
    name: z.string().nullable().optional(),
    question: z.string().max(20_000).nullable().optional(),
    agent_type: z.string().nullable().optional(),
    run_at: z.number().int().nullable().optional(),
    cron: z.string().nullable().optional(),
    timezone: z.string().nullable().optional(),
    enabled: z.boolean().nullable().optional(),
    webhook: WebhookConfigSchema.nullable().optional(),
    metadata: strictRecord.nullable().optional(),
  })
  .strict();

/**
 * Runtime schema for schedule resources returned by the Modal backend.
 */
export const ScheduleResponseSchema = z
  .object({
    schedule_id: z.string(),
    name: z.string(),
    question: z.string(),
    agent_type: z.string().nullable().optional(),
    schedule_type: ScheduleTypeSchema,
    run_at: z.number().int().nullable().optional(),
    cron: z.string().nullable().optional(),
    timezone: z.string(),
    enabled: z.boolean(),
    webhook: WebhookConfigSchema.nullable().optional(),
    metadata: strictRecord.nullable().optional(),
    user_id: z.string().nullable().optional(),
    tenant_id: z.string().nullable().optional(),
    created_at: z.number().int(),
    updated_at: z.number().int(),
    last_run_at: z.number().int().nullable().optional(),
    next_run_at: z.number().int().nullable().optional(),
    last_job_id: z.string().nullable().optional(),
    last_error: z.string().nullable().optional(),
  })
  .strict();

/**
 * Runtime schema for schedule list responses returned by the Modal backend.
 */
export const ScheduleListResponseSchema = z
  .object({
    ok: z.boolean(),
    schedules: z.array(ScheduleResponseSchema),
  })
  .strict();

/**
 * Runtime schema for supported session stop modes.
 */
export const SessionStopModeSchema = z.enum(["graceful", "immediate"]);

/**
 * Runtime schema for public session stop requests.
 */
export const SessionStopRequestSchema = z
  .object({
    mode: SessionStopModeSchema.default("graceful"),
    reason: optionalNullableString,
  })
  .strict();

/**
 * Runtime schema for session stop status responses from the Modal backend.
 */
export const SessionStopResponseSchema = z
  .object({
    ok: z.boolean(),
    session_id: z.string(),
    status: z.enum(["requested", "acknowledged", "not_found", "disabled"]),
    requested_at: z.number().int().nullable().optional(),
    expires_at: z.number().int().nullable().optional(),
    reason: z.string().nullable().optional(),
    requested_by: z.string().nullable().optional(),
    message: z.string().nullable().optional(),
  })
  .strict();

/**
 * TypeScript view of a single serialized content block.
 */
export type MessageContent = z.infer<typeof MessageContentSchema>;
/**
 * TypeScript view of a serialized agent message.
 */
export type Message = z.infer<typeof MessageSchema>;
/**
 * TypeScript view of a completed query summary block.
 */
export type Summary = z.infer<typeof SummarySchema>;
/**
 * TypeScript view of a validated public query request.
 */
export type QueryRequest = z.infer<typeof QueryRequestSchema>;
/**
 * TypeScript view of an authenticated streaming query message.
 */
export type StreamingQueryRequest = z.infer<typeof StreamingQueryRequestSchema>;
/**
 * TypeScript view of a validated successful query response.
 */
export type QueryResponse = z.infer<typeof QueryResponseSchema>;
/**
 * TypeScript view of a validated queued prompt request.
 */
export type QueuePromptRequest = z.infer<typeof QueuePromptRequestSchema>;
/**
 * TypeScript view of webhook configuration shared by jobs and schedules.
 */
export type WebhookConfig = z.infer<typeof WebhookConfigSchema>;
/**
 * TypeScript view of a validated job submission request.
 */
export type JobSubmitRequest = z.infer<typeof JobSubmitRequestSchema>;
/**
 * TypeScript view of a validated job submission acknowledgement.
 */
export type JobSubmitResponse = z.infer<typeof JobSubmitResponseSchema>;
/**
 * TypeScript view of a single artifact entry.
 */
export type ArtifactEntry = z.infer<typeof ArtifactEntrySchema>;
/**
 * TypeScript view of an artifact manifest.
 */
export type ArtifactManifest = z.infer<typeof ArtifactManifestSchema>;
/**
 * TypeScript view of webhook delivery metadata on jobs.
 */
export type WebhookStatus = z.infer<typeof WebhookStatusSchema>;
/**
 * TypeScript view of a validated job status payload.
 */
export type JobStatusResponse = z.infer<typeof JobStatusResponseSchema>;
/**
 * TypeScript view of a validated artifact listing response.
 */
export type ArtifactListResponse = z.infer<typeof ArtifactListResponseSchema>;
/**
 * TypeScript view of supported schedule kinds.
 */
export type ScheduleType = z.infer<typeof ScheduleTypeSchema>;
/**
 * TypeScript view of a validated schedule creation request.
 */
export type ScheduleCreateRequest = z.infer<typeof ScheduleCreateRequestSchema>;
/**
 * TypeScript view of a validated schedule update request.
 */
export type ScheduleUpdateRequest = z.infer<typeof ScheduleUpdateRequestSchema>;
/**
 * TypeScript view of a validated schedule resource.
 */
export type ScheduleResponse = z.infer<typeof ScheduleResponseSchema>;
/**
 * TypeScript view of a validated schedule collection response.
 */
export type ScheduleListResponse = z.infer<typeof ScheduleListResponseSchema>;
/**
 * TypeScript view of supported session stop modes.
 */
export type SessionStopMode = z.infer<typeof SessionStopModeSchema>;
/**
 * TypeScript view of a validated session stop request.
 */
export type SessionStopRequest = z.infer<typeof SessionStopRequestSchema>;
/**
 * TypeScript view of a validated session stop response.
 */
export type SessionStopResponse = z.infer<typeof SessionStopResponseSchema>;
