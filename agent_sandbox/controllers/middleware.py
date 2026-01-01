"""Middleware for the FastAPI controller."""

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Middleware that adds request ID tracking to all requests.

    Reads X-Request-Id from incoming request headers or generates a new UUID.
    Attaches the request ID to request.state and includes it in the response headers.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process the request and add request ID tracking.

        Args:
            request: The incoming request.
            call_next: The next middleware or route handler.

        Returns:
            Response with X-Request-Id header.
        """
        request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response
