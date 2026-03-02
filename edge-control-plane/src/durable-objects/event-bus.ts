/**
 * EventBus Durable Object
 *
 * Real-time event broadcasting and WebSocket connection management.
 * Handles:
 * - User-tagged WebSocket connections
 * - Multi-session event fan-out
 * - Presence tracking
 * - Cross-session notifications
 *
 * Architecture:
 * - One EventBus DO per user or tenant (derived from DO name)
 * - DO manages all WebSocket connections for that scope
 * - DO broadcasts events to relevant connections based on session filters
 * - SessionAgent DOs publish events to EventBus for fan-out
 */

import { DurableObject } from "cloudflare:workers";
import type {
  ConnectionInfo,
  Env,
  PresenceUpdateMessage,
  WebSocketMessage,
} from "../types";

interface TaggedWebSocket extends WebSocket {
  __connectionId?: string;
  __sessionIds?: string[];
}

interface BroadcastFilter {
  session_ids?: string[];
  tenant_ids?: string[];
  user_ids?: string[];
}

export class EventBus extends DurableObject<Env> {
  private readonly connections: Map<string, WebSocket> = new Map();
  private readonly connectionInfo: Map<string, ConnectionInfo> = new Map();

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);

    // Load connection info from storage
    this.ctx.blockConcurrencyWhile(async () => {
      await this.loadConnectionInfo();
    });
  }

  /**
   * Load connection info from durable storage
   */
  private async loadConnectionInfo(): Promise<void> {
    const stored =
      await this.ctx.storage.get<Record<string, ConnectionInfo>>("connections");
    if (stored) {
      for (const [connId, info] of Object.entries(stored)) {
        this.connectionInfo.set(connId, info);
      }
    }
  }

  /**
   * Save connection info to durable storage
   */
  private async saveConnectionInfo(): Promise<void> {
    const data: Record<string, ConnectionInfo> = {};
    for (const [connId, info] of this.connectionInfo) {
      data[connId] = info;
    }
    await this.ctx.storage.put("connections", data);
  }

  private async ensureAlarmScheduled(): Promise<void> {
    const current = await this.ctx.storage.getAlarm();
    if (current === null) {
      await this.ctx.storage.setAlarm(Date.now() + 60_000);
    }
  }

  private buildPresenceSnapshot(): PresenceUpdateMessage["data"] {
    const users = new Set<string>();
    const sessions = new Set<string>();
    for (const info of this.connectionInfo.values()) {
      if (info.user_id) {
        users.add(info.user_id);
      }
      for (const sessionId of info.session_ids) {
        sessions.add(sessionId);
      }
    }
    return {
      users_online: Array.from(users.values()),
      connection_count: this.connectionInfo.size,
      session_ids: Array.from(sessions.values()),
    };
  }

  private broadcastPresenceUpdate(options?: {
    userJoined?: string;
    userLeft?: string;
  }): void {
    const message: PresenceUpdateMessage = {
      type: "presence_update",
      session_id: "",
      timestamp: Date.now(),
      data: {
        ...this.buildPresenceSnapshot(),
        user_joined: options?.userJoined,
        user_left: options?.userLeft,
      },
    };

    const msgStr = JSON.stringify(message);
    for (const [connId, ws] of this.connections) {
      try {
        ws.send(msgStr);
      } catch (error) {
        console.error("Failed to send presence update:", error);
        this.connections.delete(connId);
        this.connectionInfo.delete(connId);
      }
    }
  }

  /**
   * Handle HTTP requests to this DO
   */
  fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    // Handle WebSocket upgrade requests
    if (request.headers.get("Upgrade") === "websocket") {
      return Promise.resolve(this.handleWebSocketUpgrade(request, url));
    }

    // REST API endpoints
    try {
      switch (path) {
        case "/broadcast":
          return this.handleBroadcast(request);
        case "/connections":
          return Promise.resolve(this.handleGetConnections(request));
        case "/presence":
          return Promise.resolve(this.handleGetPresence(request));
        default:
          return Promise.resolve(new Response("Not found", { status: 404 }));
      }
    } catch (error) {
      console.error("EventBus error:", error);
      return Promise.resolve(
        new Response(
          JSON.stringify({
            ok: false,
            error: error instanceof Error ? error.message : "Unknown error",
          }),
          { status: 500, headers: { "Content-Type": "application/json" } }
        )
      );
    }
  }

  private getTaggedSocket(ws: WebSocket): TaggedWebSocket {
    return ws as TaggedWebSocket;
  }

  private extractSessionIdFromMessage(
    message: WebSocketMessage
  ): string | undefined {
    if (!message.data || typeof message.data !== "object") {
      return undefined;
    }
    const data = message.data as { session_id?: unknown };
    return typeof data.session_id === "string" ? data.session_id : undefined;
  }

  private connectionMatchesFilter(
    info: ConnectionInfo,
    filter: BroadcastFilter | undefined
  ): boolean {
    if (!filter) {
      return true;
    }
    if (
      filter.session_ids &&
      !filter.session_ids.some((sid) => info.session_ids.includes(sid))
    ) {
      return false;
    }
    if (
      filter.user_ids &&
      !(info.user_id && filter.user_ids.includes(info.user_id))
    ) {
      return false;
    }
    if (
      filter.tenant_ids &&
      !(info.tenant_id && filter.tenant_ids.includes(info.tenant_id))
    ) {
      return false;
    }
    return true;
  }

  /**
   * Handle WebSocket upgrade with session filtering
   */
  private handleWebSocketUpgrade(request: Request, url: URL): Response {
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);

    // Extract connection metadata from query params
    const connectionId = crypto.randomUUID();
    const userId = url.searchParams.get("user_id") || undefined;
    const tenantId = url.searchParams.get("tenant_id") || undefined;
    const sessionIds = url.searchParams.get("session_ids")?.split(",") || [];
    const singleSession = url.searchParams.get("session_id");
    if (singleSession && !sessionIds.includes(singleSession)) {
      sessionIds.push(singleSession);
    }
    const ip =
      request.headers.get("CF-Connecting-IP") ||
      request.headers.get("X-Forwarded-For") ||
      undefined;

    // Accept the WebSocket connection using Hibernation API
    this.ctx.acceptWebSocket(server);

    // Store connection info
    const info: ConnectionInfo = {
      connection_id: connectionId,
      user_id: userId,
      tenant_id: tenantId,
      session_ids: sessionIds,
      connected_at: Date.now(),
      last_ping_at: Date.now(),
      ip,
    };

    this.connections.set(connectionId, server);
    this.connectionInfo.set(connectionId, info);
    this.ctx.waitUntil(this.saveConnectionInfo());

    // Tag the WebSocket with connection ID for later reference
    const taggedServer = this.getTaggedSocket(server);
    taggedServer.__connectionId = connectionId;
    taggedServer.__sessionIds = [...sessionIds];

    // Send connection acknowledgment
    server.send(
      JSON.stringify({
        type: "connection_ack",
        session_id: "",
        timestamp: Date.now(),
        data: {
          connection_id: connectionId,
          session_ids: sessionIds,
        },
      } satisfies WebSocketMessage)
    );

    this.broadcastPresenceUpdate({ userJoined: userId });
    this.ctx.waitUntil(this.ensureAlarmScheduled());

    return new Response(null, {
      status: 101,
      webSocket: client,
    });
  }

  /**
   * Handle incoming WebSocket messages (Hibernation API)
   */
  async webSocketMessage(
    ws: WebSocket,
    message: string | ArrayBuffer
  ): Promise<void> {
    try {
      const connectionId = this.getTaggedSocket(ws).__connectionId;
      if (!connectionId) {
        return;
      }

      const data =
        typeof message === "string"
          ? message
          : new TextDecoder().decode(message);
      const msg = JSON.parse(data) as WebSocketMessage;

      // Update last ping time
      const info = this.connectionInfo.get(connectionId);
      if (info) {
        info.last_ping_at = Date.now();
        this.connectionInfo.set(connectionId, info);
      }

      // Handle ping/pong
      if (msg.type === "ping") {
        ws.send(
          JSON.stringify({
            type: "pong",
            session_id: "",
            timestamp: Date.now(),
            data: {},
          })
        );
      }

      // Handle session subscription updates
      if (msg.type === "subscribe_session") {
        const sessionId = this.extractSessionIdFromMessage(msg);
        if (info && sessionId && !info.session_ids.includes(sessionId)) {
          info.session_ids.push(sessionId);
          this.connectionInfo.set(connectionId, info);
          await this.saveConnectionInfo();
          this.broadcastPresenceUpdate();
        }
      }

      if (msg.type === "unsubscribe_session") {
        const sessionId = this.extractSessionIdFromMessage(msg);
        if (info && sessionId) {
          info.session_ids = info.session_ids.filter((id) => id !== sessionId);
          this.connectionInfo.set(connectionId, info);
          await this.saveConnectionInfo();
          this.broadcastPresenceUpdate();
        }
      }
    } catch (error) {
      console.error("WebSocket message error:", error);
    }
  }

  /**
   * Handle WebSocket close (Hibernation API)
   */
  async webSocketClose(
    ws: WebSocket,
    code: number,
    reason: string,
    wasClean: boolean
  ): Promise<void> {
    const connectionId = this.getTaggedSocket(ws).__connectionId;
    let userLeft: string | undefined;
    if (connectionId) {
      const info = this.connectionInfo.get(connectionId);
      this.connections.delete(connectionId);
      this.connectionInfo.delete(connectionId);
      await this.saveConnectionInfo();
      if (info?.user_id) {
        const remaining = Array.from(this.connectionInfo.values()).filter(
          (existing) => existing.user_id === info.user_id
        );
        if (remaining.length === 0) {
          userLeft = info.user_id;
        }
      }
      this.broadcastPresenceUpdate({ userLeft });
    }
    console.log(`WebSocket closed: ${code} ${reason} (clean: ${wasClean})`);
  }

  /**
   * Broadcast message to filtered connections
   */
  private async handleBroadcast(request: Request): Promise<Response> {
    const body = (await request.json()) as {
      message: WebSocketMessage;
      filter?: BroadcastFilter;
    };

    const { message, filter } = body;
    let broadcastCount = 0;

    // Broadcast to filtered connections
    for (const [connId, ws] of this.connections) {
      const info = this.connectionInfo.get(connId);
      if (!info) {
        continue;
      }

      if (!this.connectionMatchesFilter(info, filter)) {
        continue;
      }

      // Send message
      try {
        ws.send(JSON.stringify(message));
        broadcastCount++;
      } catch (error) {
        console.error("Failed to send broadcast:", error);
        this.connections.delete(connId);
        this.connectionInfo.delete(connId);
      }
    }

    await this.saveConnectionInfo();

    return new Response(
      JSON.stringify({
        ok: true,
        broadcast_count: broadcastCount,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }

  /**
   * Get all active connections
   */
  private handleGetConnections(_request: Request): Response {
    const connections = Array.from(this.connectionInfo.values());

    return new Response(
      JSON.stringify({
        ok: true,
        connections,
        total: connections.length,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }

  /**
   * Get presence information (who's online)
   */
  private handleGetPresence(request: Request): Response {
    const url = new URL(request.url);
    const sessionId = url.searchParams.get("session_id");

    // Filter connections by session
    const relevantConnections = Array.from(this.connectionInfo.values()).filter(
      (conn) => !sessionId || conn.session_ids.includes(sessionId)
    );

    // Group by user
    const userPresence: Record<
      string,
      {
        user_id: string;
        connection_count: number;
        session_ids: string[];
        last_active: number;
      }
    > = {};

    for (const conn of relevantConnections) {
      if (!conn.user_id) {
        continue;
      }

      if (!userPresence[conn.user_id]) {
        userPresence[conn.user_id] = {
          user_id: conn.user_id,
          connection_count: 0,
          session_ids: [],
          last_active: 0,
        };
      }

      userPresence[conn.user_id].connection_count++;
      userPresence[conn.user_id].last_active = Math.max(
        userPresence[conn.user_id].last_active,
        conn.last_ping_at
      );

      for (const sid of conn.session_ids) {
        if (!userPresence[conn.user_id].session_ids.includes(sid)) {
          userPresence[conn.user_id].session_ids.push(sid);
        }
      }
    }

    return new Response(
      JSON.stringify({
        ok: true,
        presence: Object.values(userPresence),
      }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  }

  /**
   * Cleanup stale connections (called periodically via alarm)
   */
  async alarm(): Promise<void> {
    const now = Date.now();
    const staleThreshold = 5 * 60 * 1000; // 5 minutes
    let removed = false;

    for (const [connId, info] of this.connectionInfo) {
      if (now - info.last_ping_at > staleThreshold) {
        const ws = this.connections.get(connId);
        if (ws) {
          try {
            ws.close(1000, "Connection timeout");
          } catch (error) {
            console.error("Failed to close stale connection:", error);
          }
        }
        this.connections.delete(connId);
        this.connectionInfo.delete(connId);
        removed = true;
      }
    }

    await this.saveConnectionInfo();
    if (removed) {
      this.broadcastPresenceUpdate();
    }

    // Schedule next cleanup
    await this.ctx.storage.setAlarm(Date.now() + 60_000); // 1 minute
  }
}
