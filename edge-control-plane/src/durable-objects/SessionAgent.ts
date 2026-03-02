/**
 * SessionAgent Durable Object
 * 
 * Per-session state management and orchestration.
 * Each session gets its own DO instance with:
 * - Durable SQLite storage for messages, parts, and queue
 * - Session metadata and execution state
 * - Prompt queueing and sequential processing
 * - WebSocket connection management for real-time updates
 * 
 * Architecture:
 * - One SessionAgent DO per session_id (derived from DO name)
 * - DO persists session state across worker restarts
 * - DO coordinates with Modal backend for execution
 * - DO broadcasts updates via EventBus DO
 */

import { DurableObject } from "cloudflare:workers";
import { buildInternalAuthToken } from "../auth/internalAuth";
import type {
  Env,
  Message,
  ModalBackendRequest,
  ModalBackendResponse,
  PromptQueueEntry,
  QueryCompleteMessage,
  QueryRequest,
  QueryResponse,
  SessionMessage,
  SessionState,
  SessionUpdateMessage,
  WebSocketMessage
} from "../types";

export class SessionAgent extends DurableObject<Env> {
  private sessionState: SessionState | null = null;
  private webSockets: Set<WebSocket> = new Set();
  private queueDrainInProgress = false;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    
    // Initialize database schema on first access
    this.ctx.blockConcurrencyWhile(async () => {
      await this.initializeSchema();
      await this.loadSessionState();
    });
  }

  /**
   * Initialize SQLite schema for session storage
   */
  private async initializeSchema(): Promise<void> {
    const sql = this.ctx.storage.sql;
    
    // Session metadata table
    sql.exec(`
      CREATE TABLE IF NOT EXISTS session_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
      )
    `);
    
    // Messages table
    sql.exec(`
      CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at INTEGER NOT NULL
      )
    `);
    
    // Prompt queue table
    sql.exec(`
      CREATE TABLE IF NOT EXISTS prompt_queue (
        id TEXT PRIMARY KEY,
        question TEXT NOT NULL,
        agent_type TEXT NOT NULL,
        user_id TEXT,
        queued_at INTEGER NOT NULL,
        priority INTEGER NOT NULL DEFAULT 0
      )
    `);
    
    // Execution state table
    sql.exec(`
      CREATE TABLE IF NOT EXISTS execution_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL
      )
    `);
  }

  /**
   * Load session state from durable storage
   */
  private async loadSessionState(): Promise<void> {
    const sql = this.ctx.storage.sql;
    
    const rows = sql.exec(`
      SELECT key, value FROM session_metadata
    `).toArray() as Array<{ key: string; value: string }>;
    
    if (rows.length === 0) {
      // Initialize new session
      const sessionId = this.ctx.id.toString();
      this.sessionState = {
        session_id: sessionId,
        created_at: Date.now(),
        last_active_at: Date.now(),
        status: "idle"
      };
      await this.saveSessionState();
    } else {
      // Reconstruct session state from rows
      const metadata: Record<string, string> = {};
      for (const row of rows) {
        metadata[row.key] = row.value;
      }
      
      this.sessionState = {
        session_id: metadata.session_id,
        session_key: metadata.session_key,
        user_id: metadata.user_id,
        tenant_id: metadata.tenant_id,
        created_at: parseInt(metadata.created_at),
        last_active_at: parseInt(metadata.last_active_at),
        status: metadata.status as SessionState["status"],
        current_prompt: metadata.current_prompt,
        modal_sandbox_id: metadata.modal_sandbox_id,
        modal_sandbox_url: metadata.modal_sandbox_url
      };
    }
  }

  /**
   * Save session state to durable storage
   */
  private async saveSessionState(): Promise<void> {
    if (!this.sessionState) return;
    
    const sql = this.ctx.storage.sql;
    const metadata = this.sessionState;
    
    const requiredEntries: [string, string][] = [
      ["session_id", metadata.session_id],
      ["created_at", metadata.created_at.toString()],
      ["last_active_at", metadata.last_active_at.toString()],
      ["status", metadata.status]
    ];

    const optionalEntries: Array<[string, string | undefined]> = [
      ["session_key", metadata.session_key],
      ["user_id", metadata.user_id],
      ["tenant_id", metadata.tenant_id],
      ["current_prompt", metadata.current_prompt],
      ["modal_sandbox_id", metadata.modal_sandbox_id],
      ["modal_sandbox_url", metadata.modal_sandbox_url]
    ];

    for (const [key, value] of requiredEntries) {
      sql.exec(
        `INSERT OR REPLACE INTO session_metadata (key, value) VALUES (?, ?)`,
        key,
        value
      );
    }

    for (const [key, value] of optionalEntries) {
      if (value && value.length > 0) {
        sql.exec(
          `INSERT OR REPLACE INTO session_metadata (key, value) VALUES (?, ?)`,
          key,
          value
        );
      } else {
        // Clear stale optional metadata values (e.g. current_prompt after completion).
        sql.exec(`DELETE FROM session_metadata WHERE key = ?`, key);
      }
    }
  }

  private getQueueConfig(): { maxQueueSize: number; expirySeconds: number } {
    const maxQueueRaw = this.env.MAX_QUEUED_PROMPTS_PER_SESSION;
    const expiryRaw = this.env.PROMPT_QUEUE_ENTRY_EXPIRY_SECONDS;

    const maxQueueSize = Number.isFinite(Number(maxQueueRaw))
      ? Math.max(1, Number(maxQueueRaw))
      : 10;
    const expirySeconds = Number.isFinite(Number(expiryRaw))
      ? Math.max(60, Number(expiryRaw))
      : 3600;

    return { maxQueueSize, expirySeconds };
  }

  private async pruneExpiredQueueEntries(): Promise<void> {
    const { expirySeconds } = this.getQueueConfig();
    const cutoff = Date.now() - expirySeconds * 1000;
    const sql = this.ctx.storage.sql;
    sql.exec(`DELETE FROM prompt_queue WHERE queued_at < ?`, cutoff);
  }

  private extractSessionInfoFromUrl(request: Request): {
    sessionId?: string;
    sessionKey?: string;
    userId?: string;
    tenantId?: string;
  } {
    const url = new URL(request.url);
    const getParam = (name: string): string | undefined => {
      const value = url.searchParams.get(name);
      return value && value.length > 0 ? value : undefined;
    };

    return {
      sessionId: getParam("session_id"),
      sessionKey: getParam("session_key"),
      userId: getParam("user_id"),
      tenantId: getParam("tenant_id")
    };
  }

  private async reconcileSessionIdentity(params: {
    sessionId?: string | null;
    sessionKey?: string | null;
    userId?: string | null;
    tenantId?: string | null;
  }): Promise<void> {
    if (!this.sessionState) return;

    let changed = false;
    const incomingSessionId = params.sessionId ?? undefined;
    const doId = this.ctx.id.toString();

    if (incomingSessionId) {
      const currentSessionId = this.sessionState.session_id;
      if (!currentSessionId || currentSessionId === doId) {
        if (currentSessionId !== incomingSessionId) {
          this.sessionState.session_id = incomingSessionId;
          changed = true;
        }
      } else if (currentSessionId !== incomingSessionId) {
        console.warn("Session ID mismatch; keeping stored value", {
          stored_session_id: currentSessionId,
          incoming_session_id: incomingSessionId,
          do_id: doId
        });
      }
    }

    if (params.sessionKey && params.sessionKey !== this.sessionState.session_key) {
      this.sessionState.session_key = params.sessionKey;
      changed = true;
    }

    if (params.userId && params.userId !== this.sessionState.user_id) {
      this.sessionState.user_id = params.userId;
      changed = true;
    }

    if (params.tenantId && params.tenantId !== this.sessionState.tenant_id) {
      this.sessionState.tenant_id = params.tenantId;
      changed = true;
    }

    if (changed) {
      await this.saveSessionState();
    }
  }

  /**
   * Handle HTTP requests to this DO
   */
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;
    
    // Handle WebSocket upgrade requests
    if (request.headers.get("Upgrade") === "websocket") {
      return this.handleWebSocketUpgrade(request);
    }
    
    // REST API endpoints
    try {
      if (path.startsWith("/queue/")) {
        return this.handleQueueItem(request, path);
      }
      switch (path) {
        case "/query":
          return this.handleQuery(request);
        case "/queue":
          if (request.method === "GET") {
            return this.handleGetQueue(request);
          }
          if (request.method === "DELETE") {
            return this.handleClearQueue(request);
          }
          if (request.method === "POST") {
            return this.handleQueuePrompt(request);
          }
          return new Response("Method not allowed", { status: 405 });
        case "/state":
          return this.handleGetState(request);
        case "/messages":
          return this.handleGetMessages(request);
        case "/stop":
          return this.handleStop(request);
        default:
          return new Response("Not found", { status: 404 });
      }
    } catch (error) {
      console.error("SessionAgent error:", error);
      return new Response(
        JSON.stringify({ 
          ok: false, 
          error: error instanceof Error ? error.message : "Unknown error" 
        }),
        { status: 500, headers: { "Content-Type": "application/json" } }
      );
    }
  }

  /**
   * Handle WebSocket upgrade for real-time session updates
   */
  private async handleWebSocketUpgrade(request: Request): Promise<Response> {
    const { sessionId, sessionKey, userId, tenantId } = this.extractSessionInfoFromUrl(request);
    await this.reconcileSessionIdentity({
      sessionId,
      sessionKey,
      userId,
      tenantId
    });

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    
    // Accept the WebSocket connection using Hibernation API
    this.ctx.acceptWebSocket(server);
    this.webSockets.add(server);
    
    // Send connection acknowledgment
    server.send(JSON.stringify({
      type: "connection_ack",
      session_id: this.getCurrentSessionId(),
      timestamp: Date.now(),
      data: { status: this.sessionState?.status }
    } satisfies WebSocketMessage));
    
    return new Response(null, {
      status: 101,
      webSocket: client
    });
  }

  /**
   * Handle incoming WebSocket messages (Hibernation API)
   */
  async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    try {
      const data = typeof message === "string" ? message : new TextDecoder().decode(message);
      const msg = JSON.parse(data) as Record<string, unknown>;

      if (msg.type === "stop") {
        const stopped = await this.stopExecution();
        ws.send(JSON.stringify({
          type: "execution_state",
          session_id: this.sessionState?.session_id || "",
          timestamp: Date.now(),
          data: { event: "stop", ok: stopped }
        }));
        return;
      }

      // Handle ping/pong
      if (msg.type === "ping") {
        ws.send(JSON.stringify({
          type: "pong",
          session_id: this.getCurrentSessionId(),
          timestamp: Date.now(),
          data: {}
        }));
        return;
      }

      const queryBody = this.extractQueryRequest(msg);
      if (queryBody) {
        await this.handleStreamingQuery(ws, queryBody);
        return;
      }
    } catch (error) {
      console.error("WebSocket message error:", error);
    }
  }

  /**
   * Handle WebSocket close (Hibernation API)
   */
  async webSocketClose(ws: WebSocket, code: number, reason: string, wasClean: boolean): Promise<void> {
    this.webSockets.delete(ws);
    console.log(`WebSocket closed: ${code} ${reason} (clean: ${wasClean})`);
  }

  /**
   * Broadcast message to all connected WebSocket clients
   */
  private broadcastToWebSockets(message: WebSocketMessage): void {
    const msgStr = JSON.stringify(message);
    for (const ws of this.webSockets) {
      try {
        ws.send(msgStr);
      } catch (error) {
        console.error("Failed to send WebSocket message:", error);
        this.webSockets.delete(ws);
      }
    }
  }

  private getCurrentSessionId(): string {
    return this.sessionState?.session_id ?? this.ctx.id.toString();
  }

  private parseNullableString(value: unknown): string | null | undefined {
    if (value === undefined) return undefined;
    if (value === null) return null;
    if (typeof value === "string") return value;
    return undefined;
  }

  private parseQueryRequestPayload(payload: unknown): QueryRequest | null {
    if (!payload || typeof payload !== "object") {
      return null;
    }

    const record = payload as Record<string, unknown>;
    if (typeof record.question !== "string") {
      return null;
    }

    const parsed: QueryRequest = { question: record.question };

    if (typeof record.agent_type === "string") {
      parsed.agent_type = record.agent_type;
    }
    if (typeof record.fork_session === "boolean") {
      parsed.fork_session = record.fork_session;
    }

    const sessionId = this.parseNullableString(record.session_id);
    if (sessionId !== undefined) {
      parsed.session_id = sessionId;
    }

    const sessionKey = this.parseNullableString(record.session_key);
    if (sessionKey !== undefined) {
      parsed.session_key = sessionKey;
    }

    const jobId = this.parseNullableString(record.job_id);
    if (jobId !== undefined) {
      parsed.job_id = jobId;
    }

    const userId = this.parseNullableString(record.user_id);
    if (userId !== undefined) {
      parsed.user_id = userId;
    }

    const tenantId = this.parseNullableString(record.tenant_id);
    if (tenantId !== undefined) {
      parsed.tenant_id = tenantId;
    }

    const warmId = this.parseNullableString(record.warm_id);
    if (warmId !== undefined) {
      parsed.warm_id = warmId;
    }

    return parsed;
  }

  private parseQueryResponsePayload(payload: unknown): QueryResponse | null {
    if (!payload || typeof payload !== "object") {
      return null;
    }

    const record = payload as Record<string, unknown>;
    if (!Array.isArray(record.messages) || typeof record.session_id !== "string") {
      return null;
    }

    const response: QueryResponse = {
      ok: typeof record.ok === "boolean" ? record.ok : true,
      session_id: record.session_id,
      messages: record.messages as Message[]
    };

    if (typeof record.error === "string" && record.error.length > 0) {
      response.error = record.error;
    }

    return response;
  }

  private extractErrorMessage(payload: unknown): string | undefined {
    if (!payload || typeof payload !== "object") {
      return undefined;
    }

    const record = payload as Record<string, unknown>;
    if (typeof record.error === "string" && record.error.length > 0) {
      return record.error;
    }
    if (typeof record.message === "string" && record.message.length > 0) {
      return record.message;
    }

    return undefined;
  }

  private broadcastToEventBus(message: WebSocketMessage): void {
    if (!this.sessionState?.session_id) return;

    const busName =
      this.sessionState.tenant_id ||
      this.sessionState.user_id ||
      "anonymous";
    const doId = this.env.EVENT_BUS.idFromName(busName);
    const doStub = this.env.EVENT_BUS.get(doId);

    const payload = {
      message,
      filter: {
        session_ids: [this.sessionState.session_id]
      }
    };

    this.ctx.waitUntil(
      doStub.fetch(
        new Request("https://internal/broadcast", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        })
      ).catch(error => {
        console.error("Failed to broadcast to EventBus:", error);
      })
    );
  }

  private extractQueryRequest(msg: Record<string, unknown>): QueryRequest | null {
    const directPayload = this.parseQueryRequestPayload(msg);
    if (directPayload) {
      return directPayload;
    }

    if (msg.type === "query") {
      const nestedPayload = this.parseQueryRequestPayload(msg.data);
      if (nestedPayload) {
        return nestedPayload;
      }
    }

    return null;
  }

  private mapSSEEventToWSType(event: string): WebSocketMessage["type"] {
    if (event === "assistant") return "assistant_message";
    if (event === "tool_use") return "tool_use";
    if (event === "tool_result") return "tool_result";
    if (event === "done") return "query_complete";
    if (event === "error") return "query_error";
    return "execution_state";
  }

  private async handleStreamingQuery(ws: WebSocket, body: QueryRequest): Promise<void> {
    if (!this.sessionState) {
      ws.send(JSON.stringify({
        type: "query_error",
        session_id: "",
        timestamp: Date.now(),
        data: { error: "Session state not initialized" }
      } satisfies WebSocketMessage));
      return;
    }

    await this.reconcileSessionIdentity({
      sessionId: body.session_id,
      sessionKey: body.session_key,
      userId: body.user_id,
      tenantId: body.tenant_id ?? undefined
    });

    if (this.sessionState.status === "executing") {
      ws.send(JSON.stringify({
        type: "query_error",
        session_id: this.sessionState.session_id,
        timestamp: Date.now(),
        data: { error: "Session already executing" }
      } satisfies WebSocketMessage));
      return;
    }

    this.sessionState.status = "executing";
    this.sessionState.current_prompt = body.question;
    this.sessionState.last_active_at = Date.now();
    if (body.user_id) {
      this.sessionState.user_id = body.user_id;
    }
    await this.saveSessionState();

    this.broadcastToWebSockets({
      type: "session_update",
      session_id: this.sessionState.session_id,
      timestamp: Date.now(),
      data: {
        status: "executing",
        current_prompt: body.question
      }
    } satisfies SessionUpdateMessage);
    this.broadcastToEventBus({
      type: "session_update",
      session_id: this.sessionState.session_id,
      timestamp: Date.now(),
      data: {
        status: "executing",
        current_prompt: body.question
      }
    } satisfies SessionUpdateMessage);

    this.broadcastToWebSockets({
      type: "query_start",
      session_id: this.sessionState.session_id,
      timestamp: Date.now(),
      data: {
        question: body.question,
        agent_type: body.agent_type || "default"
      }
    });
    this.broadcastToEventBus({
      type: "query_start",
      session_id: this.sessionState.session_id,
      timestamp: Date.now(),
      data: {
        question: body.question,
        agent_type: body.agent_type || "default"
      }
    });

    const capturedMessages: Message[] = [];
    let receivedDone = false;
    let receivedError = false;
    let latestResult: Record<string, unknown> | null = null;

    const publishWs = (message: WebSocketMessage): void => {
      this.broadcastToWebSockets(message);
      this.broadcastToEventBus(message);
    };

    try {
      await this.streamModalSSE(
        {
          ...body,
          session_id: this.sessionState.session_id
        },
        async (event, data) => {
          const sessionId = this.sessionState?.session_id || "";
          const timestamp = Date.now();

          if (event === "assistant" || event === "user" || event === "system" || event === "result") {
            capturedMessages.push(data as Message);
          }

          if (event === "assistant") {
            if (data && typeof data === "object") {
              const message = data as Record<string, unknown>;
              const content = message.content;
              if (Array.isArray(content)) {
                for (const block of content) {
                  if (!block || typeof block !== "object") {
                    publishWs({
                      type: "execution_state",
                      session_id: sessionId,
                      timestamp,
                      data: { event: "assistant_block", data: block }
                    });
                    continue;
                  }

                  const blockRecord = block as Record<string, unknown>;
                  const blockType = blockRecord.type;

                  if (blockType === "text" && typeof blockRecord.text === "string") {
                    publishWs({
                      type: "assistant_message",
                      session_id: sessionId,
                      timestamp,
                      data: {
                        content: blockRecord.text,
                        partial: false
                      }
                    });
                    continue;
                  }

                  if (blockType === "tool_use") {
                    publishWs({
                      type: "tool_use",
                      session_id: sessionId,
                      timestamp,
                      data: {
                        tool_use_id: blockRecord.id,
                        name: blockRecord.name,
                        input: blockRecord.input
                      }
                    });
                    continue;
                  }

                  if (blockType === "tool_result") {
                    publishWs({
                      type: "tool_result",
                      session_id: sessionId,
                      timestamp,
                      data: {
                        tool_use_id: blockRecord.tool_use_id,
                        content: blockRecord.content,
                        is_error: blockRecord.is_error
                      }
                    });
                    continue;
                  }

                  publishWs({
                    type: "execution_state",
                    session_id: sessionId,
                    timestamp,
                    data: { event: "assistant_block", data: block }
                  });
                }
              } else {
                publishWs({
                  type: "execution_state",
                  session_id: sessionId,
                  timestamp,
                  data: { event: "assistant_message", data }
                });
              }
            } else {
              publishWs({
                type: "execution_state",
                session_id: sessionId,
                timestamp,
                data: { event: "assistant_message", data }
              });
            }
            return;
          }

          if (event === "tool_use" || event === "tool_result") {
            publishWs({
              type: this.mapSSEEventToWSType(event),
              session_id: sessionId,
              timestamp,
              data
            });
            return;
          }

          if (event === "result") {
            if (data && typeof data === "object") {
              latestResult = data as Record<string, unknown>;
            }
            publishWs({
              type: "execution_state",
              session_id: sessionId,
              timestamp,
              data: { event, data }
            });
            return;
          }

          if (event === "done") {
            receivedDone = true;
            const summary = data && typeof data === "object" ? (data as Record<string, unknown>) : {};
            let durationMs = 0;
            const summaryDuration = summary.duration_ms;
            if (typeof summaryDuration === "number" && Number.isFinite(summaryDuration)) {
              durationMs = summaryDuration;
            } else if (latestResult && typeof latestResult.duration_ms === "number") {
              durationMs = latestResult.duration_ms as number;
            }

            publishWs({
              type: "query_complete",
              session_id: sessionId,
              timestamp,
              data: {
                messages: capturedMessages,
                duration_ms: durationMs,
                summary
              }
            });

            const sessionState = this.sessionState;
            if (sessionState) {
              sessionState.status = "idle";
              sessionState.current_prompt = undefined;
              sessionState.last_active_at = Date.now();
              await this.saveSessionState();
            }

            if (capturedMessages.length > 0) {
              await this.storeMessages(capturedMessages);
            }
            return;
          }

          if (event === "error") {
            receivedError = true;
            const errorMessage = (() => {
              if (data && typeof data === "object") {
                const obj = data as Record<string, unknown>;
                if (typeof obj.error === "string") {
                  return obj.error;
                }
              }
              return typeof data === "string" ? data : "Modal streaming error";
            })();

            publishWs({
              type: "query_error",
              session_id: sessionId,
              timestamp,
              data: { error: errorMessage }
            });

            const sessionState = this.sessionState;
            if (sessionState) {
              sessionState.status = "error";
              sessionState.current_prompt = undefined;
              sessionState.last_active_at = Date.now();
              await this.saveSessionState();
            }
            return;
          }

          publishWs({
            type: "execution_state",
            session_id: sessionId,
            timestamp,
            data: { event, data }
          });
        }
      );

      if (!receivedDone && !receivedError) {
        const timestamp = Date.now();
        const sessionId = this.sessionState?.session_id || "";
        publishWs({
          type: "query_error",
          session_id: sessionId,
          timestamp,
          data: { error: "Modal stream ended without completion" }
        });

        if (this.sessionState) {
          this.sessionState.status = "error";
          this.sessionState.current_prompt = undefined;
          this.sessionState.last_active_at = Date.now();
          await this.saveSessionState();
        }
      }
    } catch (error) {
      const sessionState = this.sessionState;
      if (sessionState) {
        sessionState.status = "error";
        sessionState.last_active_at = Date.now();
        await this.saveSessionState();
      }

      const errorMessage = error instanceof Error ? error.message : "Streaming error";
      const errorEvent: WebSocketMessage = {
        type: "query_error",
        session_id: this.getCurrentSessionId(),
        timestamp: Date.now(),
        data: { error: errorMessage }
      };
      this.broadcastToWebSockets(errorEvent);
      this.broadcastToEventBus(errorEvent);
    }
  }

  private async streamModalSSE(
    body: QueryRequest,
    onEvent: (event: string, data: unknown) => Promise<void>
  ): Promise<void> {
    const modalUrl = this.sessionState?.modal_sandbox_url || this.env.MODAL_API_BASE_URL;
    const url = `${modalUrl}/query_stream`;

    const authToken = await buildInternalAuthToken(this.env.INTERNAL_AUTH_SECRET);

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "X-Internal-Auth": authToken
      },
      body: JSON.stringify(body)
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || `Modal streaming failed (${response.status})`);
    }

    if (!response.body) {
      throw new Error("Modal streaming response has no body");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let currentEvent: string | null = null;
    let dataLines: string[] = [];

    const emitEvent = async (): Promise<void> => {
      if (!currentEvent && dataLines.length === 0) {
        return;
      }

      if (dataLines.length === 0) {
        currentEvent = null;
        return;
      }

      const eventName = currentEvent || "message";
      const dataStr = dataLines.join("\n");
      let parsed: unknown = dataStr;
      try {
        parsed = JSON.parse(dataStr);
      } catch {
        console.warn("Failed to parse SSE JSON payload", {
          event: eventName
        });
      }
      await onEvent(eventName, parsed);

      currentEvent = null;
      dataLines = [];
    };

    const handleLine = async (rawLine: string): Promise<void> => {
      const line = rawLine.replace(/\r$/, "");

      if (line === "") {
        await emitEvent();
        return;
      }

      if (line.startsWith(":")) {
        return;
      }

      if (line.startsWith("id:") || line.startsWith("retry:")) {
        return;
      }

      if (line.startsWith("event:")) {
        currentEvent = line.slice(6).trim();
        dataLines = [];
        return;
      }

      if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const rawLine of lines) {
        await handleLine(rawLine);
      }
    }

    if (buffer.length > 0) {
      await handleLine(buffer);
    }

    await emitEvent();
  }

  private async executeQuery(body: QueryRequest): Promise<QueryResponse> {
    const startedAt = Date.now();
    await this.reconcileSessionIdentity({
      sessionId: body.session_id,
      sessionKey: body.session_key,
      userId: body.user_id,
      tenantId: body.tenant_id ?? undefined
    });

    if (!this.sessionState) {
      throw new Error("Session state not initialized");
    }

    if (this.sessionState.status === "executing") {
      throw new Error("Session already executing");
    }

    this.sessionState.status = "executing";
    this.sessionState.current_prompt = body.question;
    this.sessionState.last_active_at = Date.now();
    if (body.user_id) {
      this.sessionState.user_id = body.user_id;
    }
    if (body.tenant_id) {
      this.sessionState.tenant_id = body.tenant_id;
    }
    await this.saveSessionState();

    this.broadcastToWebSockets({
      type: "session_update",
      session_id: this.sessionState.session_id,
      timestamp: Date.now(),
      data: {
        status: "executing",
        current_prompt: body.question
      }
    } satisfies SessionUpdateMessage);
    this.broadcastToEventBus({
      type: "session_update",
      session_id: this.sessionState.session_id,
      timestamp: Date.now(),
      data: {
        status: "executing",
        current_prompt: body.question
      }
    } satisfies SessionUpdateMessage);

    this.broadcastToWebSockets({
      type: "query_start",
      session_id: this.sessionState.session_id,
      timestamp: Date.now(),
      data: {
        question: body.question,
        agent_type: body.agent_type || "default"
      }
    });
    this.broadcastToEventBus({
      type: "query_start",
      session_id: this.sessionState.session_id,
      timestamp: Date.now(),
      data: {
        question: body.question,
        agent_type: body.agent_type || "default"
      }
    });

    const modalResponse = await this.callModalBackend({
      endpoint: "/query",
      method: "POST",
      body: {
        ...body,
        session_id: this.sessionState.session_id
      }
    });

    if (!modalResponse.ok) {
      this.sessionState.status = "error";
      this.sessionState.current_prompt = undefined;
      this.sessionState.last_active_at = Date.now();
      await this.saveSessionState();

      this.broadcastToWebSockets({
        type: "query_error",
        session_id: this.sessionState.session_id,
        timestamp: Date.now(),
        data: { error: modalResponse.error || "Modal error" }
      });
      this.broadcastToEventBus({
        type: "query_error",
        session_id: this.sessionState.session_id,
        timestamp: Date.now(),
        data: { error: modalResponse.error || "Modal error" }
      });

      throw new Error(modalResponse.error || "Modal error");
    }

    const result = this.parseQueryResponsePayload(modalResponse.data);
    if (!result || !result.ok) {
      const modalError = result?.error || modalResponse.error || "Invalid Modal response";
      this.sessionState.status = "error";
      this.sessionState.current_prompt = undefined;
      this.sessionState.last_active_at = Date.now();
      await this.saveSessionState();

      this.broadcastToWebSockets({
        type: "query_error",
        session_id: this.sessionState.session_id,
        timestamp: Date.now(),
        data: { error: modalError }
      });
      this.broadcastToEventBus({
        type: "query_error",
        session_id: this.sessionState.session_id,
        timestamp: Date.now(),
        data: { error: modalError }
      });

      throw new Error(modalError);
    }

    await this.storeMessages(result.messages);

    this.sessionState.status = "idle";
    this.sessionState.current_prompt = undefined;
    this.sessionState.last_active_at = Date.now();
    await this.saveSessionState();

    const durationMs = Date.now() - startedAt;
    this.broadcastToWebSockets({
      type: "query_complete",
      session_id: this.sessionState.session_id,
      timestamp: Date.now(),
      data: {
        messages: result.messages,
        duration_ms: durationMs
      }
    } satisfies QueryCompleteMessage);
    this.broadcastToEventBus({
      type: "query_complete",
      session_id: this.sessionState.session_id,
      timestamp: Date.now(),
      data: {
        messages: result.messages,
        duration_ms: durationMs
      }
    } satisfies QueryCompleteMessage);

    return result;
  }

  /**
   * Handle query execution
   */
  private async handleQuery(request: Request): Promise<Response> {
    let requestBody: unknown;
    try {
      requestBody = await request.json();
    } catch {
      return new Response(
        JSON.stringify({ ok: false, error: "Invalid JSON request body" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    const body = this.parseQueryRequestPayload(requestBody);
    if (!body) {
      return new Response(
        JSON.stringify({ ok: false, error: "Invalid query request body" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    try {
      const result = await this.executeQuery(body);
      this.ctx.waitUntil(this.drainPromptQueue());
      return new Response(
        JSON.stringify(result),
        { status: 200, headers: { "Content-Type": "application/json" } }
      );
    } catch (error) {
      return new Response(
        JSON.stringify({ ok: false, error: error instanceof Error ? error.message : "Unknown error" }),
        { status: 500, headers: { "Content-Type": "application/json" } }
      );
    }
  }

  private async drainPromptQueue(): Promise<void> {
    if (this.queueDrainInProgress) return;
    if (!this.sessionState) return;

    this.queueDrainInProgress = true;
    try {
      await this.pruneExpiredQueueEntries();
      while (true) {
        const nextPrompt = await this.dequeueNextPrompt();
        if (!nextPrompt) break;

        const body: QueryRequest = {
          question: nextPrompt.question,
          agent_type: nextPrompt.agent_type,
          session_id: this.sessionState.session_id,
          user_id: nextPrompt.user_id ?? undefined,
          tenant_id: this.sessionState.tenant_id ?? undefined
        };

        try {
          await this.executeQuery(body);
        } catch (error) {
          console.error("Failed to process queued prompt", error);
          break;
        }
      }
    } finally {
      this.queueDrainInProgress = false;
    }
  }

  /**
   * Store messages in durable storage
   *
   * Note: Modal backend serializes messages with "type" field ("user", "assistant", "system", "result"),
   * but our schema expects "role". We map user/assistant types to roles and skip system/result messages.
   */
  private async storeMessages(messages: unknown[]): Promise<void> {
    const sql = this.ctx.storage.sql;

    for (const message of messages) {
      const msg = message as Record<string, unknown>;

      const role = this.extractRole(msg);

      if (!role || (role !== "user" && role !== "assistant")) {
        console.warn("Skipping message with invalid role", {
          type: msg.type,
          role: msg.role,
          keys: Object.keys(msg)
        });
        continue; // Skip non-storable message types (system, result, etc.)
      }

      if (msg.content === undefined || msg.content === null) {
        console.warn("Skipping message with missing content", {
          type: msg.type,
          role: msg.role,
          keys: Object.keys(msg)
        });
        continue;
      }

      const contentJson = JSON.stringify(msg.content);
      if (contentJson === undefined) {
        console.warn("Skipping message with non-serializable content", {
          type: msg.type,
          role: msg.role,
          keys: Object.keys(msg)
        });
        continue;
      }

      const id = crypto.randomUUID();
      sql.exec(
        `INSERT INTO messages (id, role, content, created_at) VALUES (?, ?, ?, ?)`,
        id,
        role,
        contentJson,
        Date.now()
      );
    }
  }

  private normalizeRole(value: unknown): "user" | "assistant" | null {
    if (typeof value !== "string") return null;
    const normalized = value.trim().toLowerCase();
    if (normalized === "user" || normalized === "assistant") {
      return normalized;
    }
    return null;
  }

  private extractRole(msg: Record<string, unknown>): "user" | "assistant" | null {
    const roleFromType = this.normalizeRole(msg.type);
    if (roleFromType) {
      return roleFromType;
    }

    const roleFromRole = this.normalizeRole(msg.role);
    if (roleFromRole) {
      return roleFromRole;
    }

    const nested = msg.message;
    if (nested && typeof nested === "object") {
      const nestedRecord = nested as Record<string, unknown>;
      const nestedType = this.normalizeRole(nestedRecord.type);
      if (nestedType) {
        return nestedType;
      }
      const nestedRole = this.normalizeRole(nestedRecord.role);
      if (nestedRole) {
        return nestedRole;
      }
    }

    return null;
  }

  /**
   * Queue a prompt for sequential processing
   */
  private async handleQueuePrompt(request: Request): Promise<Response> {
    let requestBody: unknown;
    try {
      requestBody = await request.json();
    } catch {
      return new Response(
        JSON.stringify({ ok: false, queued: false, error: "Invalid JSON request body" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    const body = this.parseQueryRequestPayload(requestBody);
    if (!body) {
      return new Response(
        JSON.stringify({ ok: false, queued: false, error: "Invalid queue prompt body" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    if (!body.question) {
      return new Response(
        JSON.stringify({ ok: false, queued: false, error: "Missing question" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    await this.reconcileSessionIdentity({
      sessionId: body.session_id,
      sessionKey: body.session_key,
      userId: body.user_id,
      tenantId: body.tenant_id ?? undefined
    });
    await this.pruneExpiredQueueEntries();
    const { maxQueueSize, expirySeconds } = this.getQueueConfig();

    const currentLength = await this.getQueueLength();
    if (currentLength >= maxQueueSize) {
      return new Response(
        JSON.stringify({
          ok: false,
          queued: false,
          session_id: this.sessionState?.session_id || "",
          error: `Queue limit reached (${maxQueueSize})`,
          queue_size: currentLength,
          max_queue_size: maxQueueSize
        }),
        { status: 429, headers: { "Content-Type": "application/json" } }
      );
    }

    const sql = this.ctx.storage.sql;
    const id = crypto.randomUUID();
    const queuedAt = Date.now();
    
    sql.exec(
      `INSERT INTO prompt_queue (id, question, agent_type, user_id, queued_at, priority) 
       VALUES (?, ?, ?, ?, ?, ?)`,
      id,
      body.question,
      body.agent_type || "default",
      body.user_id || null,
      queuedAt,
      0
    );

    const queueLength = await this.getQueueLength();
    const expiresAt = queuedAt + expirySeconds * 1000;
    
    // Broadcast queue update
    const queueMessage: WebSocketMessage = {
      type: "prompt_queued",
      session_id: this.sessionState?.session_id || "",
      timestamp: Date.now(),
      data: { prompt_id: id, queue_length: queueLength }
    };
    this.broadcastToWebSockets(queueMessage);
    this.broadcastToEventBus(queueMessage);
    
    return new Response(
      JSON.stringify({
        ok: true,
        queued: true,
        session_id: this.sessionState?.session_id || "",
        prompt_id: id,
        queue_size: queueLength,
        max_queue_size: maxQueueSize,
        expires_at: expiresAt
      }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }

  private async getQueueEntries(): Promise<PromptQueueEntry[]> {
    await this.pruneExpiredQueueEntries();
    const sql = this.ctx.storage.sql;
    const rows = sql.exec(
      `SELECT id, question, agent_type, user_id, queued_at, priority FROM prompt_queue
       ORDER BY priority DESC, queued_at ASC`
    ).toArray() as Array<{
      id: string;
      question: string;
      agent_type: string;
      user_id: string | null;
      queued_at: number;
      priority: number;
    }>;

    return rows.map(row => ({
      id: row.id,
      session_id: this.sessionState?.session_id || "",
      question: row.question,
      agent_type: row.agent_type,
      user_id: row.user_id || undefined,
      queued_at: row.queued_at,
      priority: row.priority
    }));
  }

  private async handleGetQueue(request: Request): Promise<Response> {
    const { sessionId, sessionKey, userId, tenantId } = this.extractSessionInfoFromUrl(request);
    await this.reconcileSessionIdentity({ sessionId, sessionKey, userId, tenantId });
    const { maxQueueSize, expirySeconds } = this.getQueueConfig();
    const entries = await this.getQueueEntries();
    const prompts = entries.map((entry, index) => ({
      prompt_id: entry.id,
      question: entry.question,
      user_id: entry.user_id,
      queued_at: entry.queued_at,
      expires_at: entry.queued_at + expirySeconds * 1000,
      position: index + 1
    }));
    const isExecuting = this.sessionState?.status === "executing";

    return new Response(
      JSON.stringify({
        ok: true,
        session_id: this.sessionState?.session_id || "",
        is_executing: isExecuting,
        queue_size: prompts.length,
        prompts,
        max_queue_size: maxQueueSize
      }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }

  private async dequeueNextPrompt(): Promise<PromptQueueEntry | null> {
    await this.pruneExpiredQueueEntries();
    const sql = this.ctx.storage.sql;
    const rows = sql.exec(
      `SELECT id, question, agent_type, user_id, queued_at, priority FROM prompt_queue
       ORDER BY priority DESC, queued_at ASC
       LIMIT 1`
    ).toArray() as Array<{
      id: string;
      question: string;
      agent_type: string;
      user_id: string | null;
      queued_at: number;
      priority: number;
    }>;

    if (rows.length === 0) return null;
    const entry = rows[0];
    sql.exec(`DELETE FROM prompt_queue WHERE id = ?`, entry.id);

    return {
      id: entry.id,
      session_id: this.sessionState?.session_id || "",
      question: entry.question,
      agent_type: entry.agent_type,
      user_id: entry.user_id || undefined,
      queued_at: entry.queued_at,
      priority: entry.priority
    };
  }

  private async handleClearQueue(request: Request): Promise<Response> {
    const { sessionId, sessionKey, userId, tenantId } = this.extractSessionInfoFromUrl(request);
    await this.reconcileSessionIdentity({ sessionId, sessionKey, userId, tenantId });

    await this.pruneExpiredQueueEntries();
    const clearedCount = await this.getQueueLength();
    const sql = this.ctx.storage.sql;
    sql.exec(`DELETE FROM prompt_queue`);

    return new Response(
      JSON.stringify({
        ok: true,
        session_id: this.sessionState?.session_id || "",
        cleared_count: clearedCount,
        message: clearedCount > 0 ? `Cleared ${clearedCount} queued prompt(s)` : "Queue was already empty"
      }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }

  private async handleQueueItem(request: Request, path: string): Promise<Response> {
    if (request.method !== "DELETE") {
      return new Response("Method not allowed", { status: 405 });
    }

    const promptId = path.split("/")[2];
    if (!promptId) {
      return new Response("Invalid queue path", { status: 400 });
    }

    const { sessionId, sessionKey, userId, tenantId } = this.extractSessionInfoFromUrl(request);
    await this.reconcileSessionIdentity({ sessionId, sessionKey, userId, tenantId });

    await this.pruneExpiredQueueEntries();
    const sql = this.ctx.storage.sql;
    const rows = sql.exec(`SELECT id FROM prompt_queue WHERE id = ?`, promptId).toArray();
    if (rows.length === 0) {
      return new Response(
        JSON.stringify({
          ok: false,
          removed: false,
          session_id: this.sessionState?.session_id || "",
          prompt_id: promptId,
          error: "Prompt not found in queue"
        }),
        { status: 404, headers: { "Content-Type": "application/json" } }
      );
    }

    sql.exec(`DELETE FROM prompt_queue WHERE id = ?`, promptId);
    return new Response(
      JSON.stringify({
        ok: true,
        removed: true,
        session_id: this.sessionState?.session_id || "",
        prompt_id: promptId
      }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }

  /**
   * Get current queue length
   */
  private async getQueueLength(): Promise<number> {
    await this.pruneExpiredQueueEntries();
    const sql = this.ctx.storage.sql;
    const result = sql.exec(`SELECT COUNT(*) as count FROM prompt_queue`).toArray();
    return (result[0] as { count: number }).count;
  }

  /**
   * Get session state
   */
  private async handleGetState(request: Request): Promise<Response> {
    const { sessionId, sessionKey, userId, tenantId } = this.extractSessionInfoFromUrl(request);
    await this.reconcileSessionIdentity({ sessionId, sessionKey, userId, tenantId });
    return new Response(
      JSON.stringify({ ok: true, state: this.sessionState }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }

  /**
   * Get session messages
   */
  private async handleGetMessages(request: Request): Promise<Response> {
    const { sessionId, sessionKey, userId, tenantId } = this.extractSessionInfoFromUrl(request);
    await this.reconcileSessionIdentity({ sessionId, sessionKey, userId, tenantId });
    const sql = this.ctx.storage.sql;
    const rows = sql.exec(
      `SELECT id, role, content, created_at FROM messages ORDER BY created_at ASC`
    ).toArray() as Array<{ id: string; role: string; content: string; created_at: number }>;
    
    const messages: SessionMessage[] = rows.map(row => ({
      id: row.id,
      session_id: this.sessionState?.session_id || "",
      role: row.role as "user" | "assistant",
      content: JSON.parse(row.content),
      created_at: row.created_at
    }));
    
    return new Response(
      JSON.stringify({ ok: true, messages }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }

  /**
   * Stop current execution
   */
  private async handleStop(request: Request): Promise<Response> {
    if (!this.sessionState) {
      return new Response(
        JSON.stringify({ ok: false, error: "Session not initialized" }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    const { sessionId, sessionKey, userId, tenantId } = this.extractSessionInfoFromUrl(request);
    await this.reconcileSessionIdentity({ sessionId, sessionKey, userId, tenantId });

    const ok = await this.stopExecution();
    return new Response(
      JSON.stringify({ ok }),
      { status: ok ? 200 : 500, headers: { "Content-Type": "application/json" } }
    );
  }

  private async stopExecution(): Promise<boolean> {
    if (!this.sessionState) {
      return false;
    }

    const modalResponse = await this.callModalBackend({
      endpoint: `/session/${this.sessionState.session_id}/stop`,
      method: "POST",
      body: {}
    });

    this.sessionState.status = "idle";
    this.sessionState.current_prompt = undefined;
    this.sessionState.last_active_at = Date.now();
    await this.saveSessionState();

    const update: SessionUpdateMessage = {
      type: "session_update",
      session_id: this.sessionState.session_id,
      timestamp: Date.now(),
      data: { status: "idle" }
    };
    this.broadcastToWebSockets(update);
    this.broadcastToEventBus(update);

    return modalResponse.ok;
  }

  /**
   * Call Modal backend with authentication
   */
  private async callModalBackend(req: ModalBackendRequest): Promise<ModalBackendResponse> {
    const modalUrl = this.sessionState?.modal_sandbox_url || this.env.MODAL_API_BASE_URL;
    const url = `${modalUrl}${req.endpoint}`;
    
    // Generate internal auth token
    const authToken = await buildInternalAuthToken(this.env.INTERNAL_AUTH_SECRET);
    
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "X-Internal-Auth": authToken,
      ...(req.headers || {})
    };
    
    try {
      const response = await fetch(url, {
        method: req.method,
        headers,
        body: req.body ? JSON.stringify(req.body) : undefined
      });

      const rawBody = await response.text();
      let data: unknown = undefined;
      if (rawBody.length > 0) {
        try {
          data = JSON.parse(rawBody);
        } catch {
          data = rawBody;
        }
      }
      const parsedError = this.extractErrorMessage(data);
      const fallbackError =
        typeof data === "string" && data.trim().length > 0 ? data : "Unknown error";
      
      return {
        ok: response.ok,
        status: response.status,
        data: response.ok ? data : undefined,
        error: response.ok ? undefined : parsedError || fallbackError
      };
    } catch (error) {
      return {
        ok: false,
        status: 500,
        error: error instanceof Error ? error.message : "Network error"
      };
    }
  }

}
