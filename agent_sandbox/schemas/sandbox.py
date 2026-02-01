"""Sandbox-related request/response schemas."""

from typing import Literal
from uuid import UUID

from pydantic import field_validator

from agent_sandbox.schemas.base import BaseSchema


def _validate_job_id(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("job_id must be a valid UUID") from exc


class QueryBody(BaseSchema):
    """Request body for agent queries."""

    question: str
    agent_type: str = "default"  # Agent type: "default", "marketing", "research", etc.
    session_id: str | None = None
    session_key: str | None = None
    fork_session: bool = False
    job_id: str | None = None
    user_id: str | None = None  # For statistics tracking
    warm_id: str | None = None  # Pre-warm correlation ID from POST /warm

    @field_validator("job_id")
    @classmethod
    def validate_job_id(cls, value: str | None) -> str | None:
        return _validate_job_id(value)


# =============================================================================
# Pre-warm API Schemas
# =============================================================================
# These schemas support the speculative sandbox pre-warming feature.
# Clients call POST /warm when users start typing to begin sandbox preparation.
# =============================================================================


class WarmRequest(BaseSchema):
    """Request body for sandbox pre-warming.

    Use this endpoint when users start typing to begin sandbox preparation
    before the actual query arrives.
    """

    sandbox_type: Literal["agent_sdk"] = "agent_sdk"
    session_id: str | None = None  # Enable session restoration
    job_id: str | None = None  # Enable job workspace setup

    @field_validator("job_id")
    @classmethod
    def validate_job_id(cls, value: str | None) -> str | None:
        return _validate_job_id(value)


class WarmResponse(BaseSchema):
    """Response from sandbox pre-warming request.

    Contains the warm_id to pass with the subsequent query for correlation.
    """

    warm_id: str
    status: Literal["warming", "ready", "error"]
    sandbox_type: str
    expires_at: int  # Unix timestamp when pre-warm expires
    message: str | None = None  # Human-readable status message


class WarmStatusResponse(BaseSchema):
    """Response for pre-warm status endpoint.

    Shows current state of pre-warm requests.
    """

    enabled: bool
    total: int
    warming: int
    ready: int
    claimed: int
    expired: int
    timeout_seconds: int  # Configured pre-warm timeout


# =============================================================================
# Session Stop/Cancel API Schemas
# =============================================================================
# These schemas support graceful termination of agent sessions mid-execution.
# Clients call POST /session/{id}/stop to request cancellation.
# =============================================================================


class SessionStopRequest(BaseSchema):
    """Request body for stopping a session mid-execution.

    All fields are optional. The session_id is provided in the URL path.

    Stop Modes:
        - "graceful" (default): Sets cancellation flag, agent stops at next tool call
        - "immediate": Calls client.interrupt() for near-instant termination
    """

    mode: Literal["graceful", "immediate"] = "graceful"  # Stop mode
    reason: str | None = None  # Human-readable reason for stopping
    requested_by: str | None = None  # Identifier of who requested the stop


class SessionStopResponse(BaseSchema):
    """Response from session stop request.

    Contains the cancellation entry details and current status.
    """

    ok: bool
    session_id: str
    status: Literal["requested", "acknowledged", "not_found", "disabled"]
    requested_at: int | None = None  # Unix timestamp when stop was requested
    expires_at: int | None = None  # Unix timestamp when cancellation flag expires
    reason: str | None = None  # The provided reason for stopping
    requested_by: str | None = None  # Who requested the stop
    message: str | None = None  # Human-readable status message


class SessionCancellationStatusResponse(BaseSchema):
    """Response for session cancellation status endpoint.

    Shows current state of cancellation requests across all sessions.
    """

    enabled: bool
    total: int
    requested: int
    acknowledged: int
    expired: int
    expiry_seconds: int  # Configured cancellation expiry time


# =============================================================================
# Prompt Queue API Schemas
# =============================================================================
# These schemas support the follow-up prompt queue feature.
# Clients can queue prompts while a session is executing, and they will be
# processed sequentially after the current query completes.
# =============================================================================


class QueuedPromptEntry(BaseSchema):
    """A single queued prompt entry.

    Represents a prompt that is waiting in the queue to be processed.
    """

    prompt_id: str  # Unique identifier for this prompt
    question: str  # The prompt text
    user_id: str | None = None  # Who submitted the prompt
    queued_at: int  # Unix timestamp when queued
    expires_at: int  # Unix timestamp when prompt expires
    position: int | None = None  # Position in queue (1-indexed)


class QueuePromptRequest(BaseSchema):
    """Request body for queueing a prompt.

    Use this to add a follow-up prompt to a session's queue while
    the session is executing another query.
    """

    question: str  # The prompt text to queue
    user_id: str | None = None  # Who is submitting the prompt


class QueuePromptResponse(BaseSchema):
    """Response from adding a prompt to the queue.

    Contains status of the queue operation and prompt details.
    """

    ok: bool
    queued: bool
    session_id: str
    prompt_id: str | None = None  # ID if queued successfully
    position: int | None = None  # Position in queue (1-indexed)
    queue_size: int = 0  # Total prompts in queue
    expires_at: int | None = None  # When this prompt expires
    error: str | None = None  # Error message if not queued
    message: str | None = None  # Human-readable status


class PromptQueueListResponse(BaseSchema):
    """Response for listing a session's queued prompts.

    Contains all pending prompts in the queue.
    """

    ok: bool
    session_id: str
    is_executing: bool  # Whether session is currently executing
    queue_size: int  # Number of pending prompts
    prompts: list[QueuedPromptEntry]  # List of queued prompts
    max_queue_size: int  # Configured limit


class PromptQueueClearResponse(BaseSchema):
    """Response from clearing a session's prompt queue.

    Contains the number of prompts that were cleared.
    """

    ok: bool
    session_id: str
    cleared_count: int  # Number of prompts cleared
    message: str | None = None


class PromptQueueStatusResponse(BaseSchema):
    """Response for prompt queue status endpoint.

    Shows current state of prompt queues across all sessions.
    """

    enabled: bool
    sessions_with_queues: int  # Number of sessions with pending prompts
    total_queued_prompts: int  # Total prompts across all sessions
    active_prompts: int  # Non-expired prompts
    expired_prompts: int  # Prompts past expiry
    max_queue_size: int  # Configured limit per session
    entry_expiry_seconds: int  # Configured expiry time


# =============================================================================
# Multiplayer Session Schemas
# =============================================================================
# These schemas support multiplayer session collaboration where multiple users
# can interact with the same session. Sessions track ownership, authorized users,
# and message history with user attribution.
# =============================================================================


class MessageHistoryEntry(BaseSchema):
    """A single message in session history with user attribution.

    Represents either a user query or agent response in the conversation.
    """

    message_id: str  # Unique identifier for this message
    role: Literal["user", "assistant"]  # Who sent the message
    content: str  # Message content (query or response summary)
    user_id: str | None = None  # Who sent the message (for user role)
    timestamp: int  # Unix timestamp when message was recorded
    turn_number: int | None = None  # Conversation turn number
    tokens_used: int | None = None  # Tokens consumed (for assistant)


class SessionMetadata(BaseSchema):
    """Metadata about a session including ownership and access control.

    Tracks who created the session, who can access it, and conversation history.
    """

    session_id: str  # The session identifier
    owner_id: str | None = None  # User who created the session
    created_at: int  # Unix timestamp when session was created
    updated_at: int  # Unix timestamp of last activity
    name: str | None = None  # Human-readable session name
    description: str | None = None  # Session description
    authorized_users: list[str] = []  # Users with access (excludes owner)
    message_count: int = 0  # Total messages in history
    is_shared: bool = False  # Whether session has been shared


class SessionShareRequest(BaseSchema):
    """Request body for sharing a session with another user.

    The session_id is provided in the URL path.
    """

    user_id: str  # User to share with
    requested_by: str | None = None  # Who is sharing (for audit)


class SessionShareResponse(BaseSchema):
    """Response from session sharing request.

    Contains the updated list of authorized users.
    """

    ok: bool
    session_id: str
    shared_with: str  # User ID that was granted access
    authorized_users: list[str]  # All authorized users (excludes owner)
    message: str | None = None


class SessionUnshareRequest(BaseSchema):
    """Request body for revoking a user's access to a session.

    The session_id is provided in the URL path.
    """

    user_id: str  # User to revoke access from
    requested_by: str | None = None  # Who is revoking (for audit)


class SessionUnshareResponse(BaseSchema):
    """Response from session unshare request.

    Contains the updated list of authorized users.
    """

    ok: bool
    session_id: str
    revoked_from: str  # User ID whose access was revoked
    authorized_users: list[str]  # Remaining authorized users
    message: str | None = None


class SessionMetadataResponse(BaseSchema):
    """Response for retrieving session metadata.

    Contains full session metadata including access control info.
    """

    ok: bool
    session_id: str
    owner_id: str | None = None
    created_at: int | None = None
    updated_at: int | None = None
    name: str | None = None
    description: str | None = None
    authorized_users: list[str] = []
    message_count: int = 0
    is_shared: bool = False
    is_executing: bool = False  # Current execution state
    has_snapshot: bool = False  # Whether a filesystem snapshot exists
    message: str | None = None


class SessionHistoryResponse(BaseSchema):
    """Response for retrieving session message history.

    Contains the conversation history with user attribution.
    """

    ok: bool
    session_id: str
    message_count: int
    messages: list[MessageHistoryEntry]
    has_more: bool = False  # For pagination
    message: str | None = None


class SessionUsersResponse(BaseSchema):
    """Response for listing users with access to a session.

    Contains owner and all authorized users.
    """

    ok: bool
    session_id: str
    owner_id: str | None = None
    authorized_users: list[str]
    total_users: int  # owner + authorized users
    message: str | None = None


class MultiplayerStatusResponse(BaseSchema):
    """Response for multiplayer session status endpoint.

    Shows current state of multiplayer sessions across the system.
    """

    enabled: bool
    total_sessions: int  # Total sessions with metadata
    shared_sessions: int  # Sessions shared with at least one user
    total_messages: int  # Total messages tracked
    max_history_per_session: int  # Configured limit
