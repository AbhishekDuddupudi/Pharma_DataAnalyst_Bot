"""
Custom middleware – request-id propagation.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger

logger = get_logger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    Assigns a unique ``X-Request-Id`` to every request.

    * If the client sends one, it is reused.
    * The id is added to the response headers **and** injected into log
      records via ``request.state``.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        request.state.request_id = request_id

        logger.info(
            "%s %s",
            request.method,
            request.url.path,
            extra={"request_id": request_id},
        )

        response: Response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response
