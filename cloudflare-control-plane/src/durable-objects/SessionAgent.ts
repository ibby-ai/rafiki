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
import type {
  Env,
  Message,
  ModalBackendRequest,
  ModalBackendResponse,
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
    
    const entries: [string, string][] = [
      ["session_id", metadata.session_id],
      ["created_at", metadata.created_at.toString()],
      ["last_active_at", metadata.last_active_at.toString()],
      ["status", metadata.status]
    ];
    
    if (metadata.session_key) entries.push(["session_key", metadata.session_key]);
    if (metadata.user_id) entries.push(["user_id", metadata.user_id]);
    if (metadata.tenant_id) entries.push(["tenant_id", metadata.tenant_id]);
    if (metadata.current_prompt) entries.push(["current_prompt", metadata.current_prompt]);
    if (metadata.modal_sandbox_id) entries.push(["modal_sandbox_id", metadata.modal_sandbox_id]);
    if (metadata.modal_sandbox_url) entries.push(["modal_sandbox_url", metadata.modal_sandbox_url]);
    
    for (const [key, value] of entries) {
      sql.exec(
        `INSERT OR REPLACE INTO session_metadata (key, value) VALUES (?, ?)`,
        key,
        value
      );
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
      switch (path) {
        case "/query":
          return this.handleQuery(request);
        case "/queue":
          return this.handleQueuePrompt(request);
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
  private handleWebSocketUpgrade(request: Request): Response {
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    
    // Accept the WebSocket connection using Hibernation API
    this.ctx.acceptWebSocket(server);
    this.webSockets.add(server);
    
    // Send connection acknowledgment
    server.send(JSON.stringify({
      type: "connection_ack",
      session_id: this.sessionState?.session_id,
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
      const msg = JSON.parse(data) as WebSocketMessage;
      
      // Handle ping/pong
      if (msg.type === "ping") {
        ws.send(JSON.stringify({
          type: "pong",
          session_id: this.sessionState?.session_id,
          timestamp: Date.now(),
          data: {}
        }));
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

  /**
   * Handle query execution
   */
  private async handleQuery(request: Request): Promise<Response> {
    const body = await request.json() as QueryRequest;
    
    // Update session state
    if (!this.sessionState) {
      throw new Error("Session state not initialized");
    }
    
    this.sessionState.status = "executing";
    this.sessionState.current_prompt = body.question;
    this.sessionState.last_active_at = Date.now();
    await this.saveSessionState();
    
    // Broadcast session update
    this.broadcastToWebSockets({
      type: "session_update",
      session_id: this.sessionState.session_id,
      timestamp: Date.now(),
      data: {
        status: "executing",
        current_prompt: body.question
      }
    } satisfies SessionUpdateMessage);
    
    // Forward to Modal backend
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
      await this.saveSessionState();
      
      return new Response(
        JSON.stringify({ ok: false, error: modalResponse.error }),
        { status: modalResponse.status, headers: { "Content-Type": "application/json" } }
      );
    }
    
    const result = modalResponse.data as QueryResponse;
    
    // Store messages
    await this.storeMessages(result.messages);
    
    // Update session state
    this.sessionState.status = "idle";
    this.sessionState.current_prompt = undefined;
    this.sessionState.last_active_at = Date.now();
    await this.saveSessionState();
    
    // Broadcast completion
    this.broadcastToWebSockets({
      type: "query_complete",
      session_id: this.sessionState.session_id,
      timestamp: Date.now(),
      data: {
        messages: result.messages,
        duration_ms: 0 // TODO: track actual duration
      }
    } satisfies QueryCompleteMessage);
    
    return new Response(
      JSON.stringify(result),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }

  /**
   * Store messages in durable storage
   */
  private async storeMessages(messages: Message[]): Promise<void> {
    const sql = this.ctx.storage.sql;
    
    for (const message of messages) {
      const id = crypto.randomUUID();
      sql.exec(
        `INSERT INTO messages (id, role, content, created_at) VALUES (?, ?, ?, ?)`,
        id,
        message.role,
        JSON.stringify(message.content),
        Date.now()
      );
    }
  }

  /**
   * Queue a prompt for sequential processing
   */
  private async handleQueuePrompt(request: Request): Promise<Response> {
    const body = await request.json() as QueryRequest;
    
    const sql = this.ctx.storage.sql;
    const id = crypto.randomUUID();
    
    sql.exec(
      `INSERT INTO prompt_queue (id, question, agent_type, user_id, queued_at, priority) 
       VALUES (?, ?, ?, ?, ?, ?)`,
      id,
      body.question,
      body.agent_type || "default",
      body.user_id || null,
      Date.now(),
      0
    );
    
    // Broadcast queue update
    this.broadcastToWebSockets({
      type: "prompt_queued",
      session_id: this.sessionState?.session_id || "",
      timestamp: Date.now(),
      data: { prompt_id: id, queue_length: this.getQueueLength() }
    });
    
    return new Response(
      JSON.stringify({ ok: true, prompt_id: id }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }

  /**
   * Get current queue length
   */
  private getQueueLength(): number {
    const sql = this.ctx.storage.sql;
    const result = sql.exec(`SELECT COUNT(*) as count FROM prompt_queue`).toArray();
    return (result[0] as { count: number }).count;
  }

  /**
   * Get session state
   */
  private async handleGetState(request: Request): Promise<Response> {
    return new Response(
      JSON.stringify({ ok: true, state: this.sessionState }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }

  /**
   * Get session messages
   */
  private async handleGetMessages(request: Request): Promise<Response> {
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
    
    // Call Modal backend to stop execution
    const modalResponse = await this.callModalBackend({
      endpoint: `/session/${this.sessionState.session_id}/stop`,
      method: "POST",
      body: {}
    });
    
    // Update local state
    this.sessionState.status = "idle";
    this.sessionState.current_prompt = undefined;
    await this.saveSessionState();
    
    return new Response(
      JSON.stringify({ ok: modalResponse.ok }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }

  /**
   * Call Modal backend with authentication
   */
  private async callModalBackend(req: ModalBackendRequest): Promise<ModalBackendResponse> {
    const modalUrl = this.sessionState?.modal_sandbox_url || this.env.MODAL_API_BASE_URL;
    const url = `${modalUrl}${req.endpoint}`;
    
    // Generate internal auth token
    const authToken = await this.generateInternalAuthToken();
    
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
      
      const data = await response.json();
      
      return {
        ok: response.ok,
        status: response.status,
        data: response.ok ? data : undefined,
        error: response.ok ? undefined : data.error || "Unknown error"
      };
    } catch (error) {
      return {
        ok: false,
        status: 500,
        error: error instanceof Error ? error.message : "Network error"
      };
    }
  }

  /**
   * Generate internal authentication token for Modal backend
   */
  private async generateInternalAuthToken(): Promise<string> {
    const payload = {
      service: "cloudflare-worker",
      issued_at: Date.now(),
      expires_at: Date.now() + 300000 // 5 minutes
    };
    
    const payloadStr = JSON.stringify(payload);
    const encoder = new TextEncoder();
    const data = encoder.encode(payloadStr);
    const key = encoder.encode(this.env.INTERNAL_AUTH_SECRET);
    
    // Simple HMAC signing (in production, use proper JWT library)
    const signature = await crypto.subtle.importKey(
      "raw",
      key,
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"]
    ).then(cryptoKey => 
      crypto.subtle.sign("HMAC", cryptoKey, data)
    ).then(sig => 
      btoa(String.fromCharCode(...new Uint8Array(sig)))
    );
    
    return `${btoa(payloadStr)}.${signature}`;
  }
}
