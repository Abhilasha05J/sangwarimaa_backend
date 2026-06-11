"""
Simple in-memory rate limiting for sensitive endpoints.
For production, replace with Redis-backed slowapi.
"""

import time
from collections import defaultdict
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

# Track requests: {ip: [(timestamp, endpoint), ...]}
_request_log: dict = defaultdict(list)

# Rate limit rules: endpoint_path → (max_requests, window_seconds)
RATE_LIMITS = {
    "/auth/send-otp":    (3, 300),    # 3 per 5 minutes
    "/auth/verify-otp":  (5, 300),    # 5 per 5 minutes
    "/admin/login":      (5, 600),    # 5 per 10 minutes
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        client_ip = request.client.host if request.client else "unknown"

        # Check if this path has a rate limit
        for pattern, (max_req, window) in RATE_LIMITS.items():
            if path.endswith(pattern):
                now = time.time()
                key = f"{client_ip}:{pattern}"

                # Clean old entries
                _request_log[key] = [
                    ts for ts in _request_log[key]
                    if now - ts < window
                ]

                if len(_request_log[key]) >= max_req:
                    raise HTTPException(
                        status_code=429,
                        detail={
                            "success": False,
                            "data": None,
                            "error": {
                                "code": "RATE_LIMIT_EXCEEDED",
                                "message": "Too many requests. Please try again later.",
                                "details": {"retry_after_seconds": window},
                            }
                        }
                    )

                _request_log[key].append(now)
                break

        return await call_next(request)