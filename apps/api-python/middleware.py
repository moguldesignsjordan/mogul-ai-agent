"""
FastAPI Middleware for Mogul AI Agent
Includes: Request Tracing, Rate Limiting, Authentication
"""

import os
import time
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Callable, Tuple
from collections import defaultdict
import asyncio

from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from logging_config import request_id_var, user_id_var, get_logger

logger = get_logger("mogul.middleware")


# =====================================================
# REQUEST TRACING MIDDLEWARE
# =====================================================

class RequestTracingMiddleware(BaseHTTPMiddleware):
    """
    Adds unique request ID to every request for tracing.
    - Generates a unique ID for each request
    - Adds it to response headers
    - Makes it available via context var for logging
    """
    
    async def dispatch(self, request: Request, call_next):
        # Generate or extract request ID
        request_id = request.headers.get("X-Request-ID")
        if not request_id:
            request_id = secrets.token_hex(8)
        
        # Store in context var for logging
        token = request_id_var.set(request_id)
        
        # Store on request state for handlers
        request.state.request_id = request_id
        
        # Log request start
        start_time = time.perf_counter()
        logger.info(
            f"→ {request.method} {request.url.path}",
            extra={
                "event": "request_started",
                "method": request.method,
                "path": request.url.path,
                "query": str(request.query_params),
                "client_ip": self._get_client_ip(request),
            }
        )
        
        try:
            response = await call_next(request)
            
            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000
            
            # Add headers
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"
            
            # Log request completion
            logger.info(
                f"← {response.status_code} ({duration_ms:.0f}ms)",
                extra={
                    "event": "request_completed",
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                }
            )
            
            return response
            
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                f"✗ Request failed: {e}",
                extra={
                    "event": "request_failed",
                    "error": str(e),
                    "duration_ms": round(duration_ms, 2),
                },
                exc_info=True
            )
            raise
            
        finally:
            request_id_var.reset(token)
    
    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP, handling proxies."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"


# =====================================================
# RATE LIMITING
# =====================================================

class RateLimiter:
    """
    In-memory sliding window rate limiter.
    
    For production, replace with Redis-based implementation:
    - Use Redis MULTI/EXEC for atomic operations
    - Share rate limit state across multiple instances
    """
    
    def __init__(self):
        self._requests: Dict[str, list] = defaultdict(list)
        self._lock = asyncio.Lock()
    
    async def is_allowed(
        self,
        key: str,
        limit: int,
        window_seconds: int
    ) -> Tuple[bool, Dict[str, int]]:
        """
        Check if request is allowed under rate limit.
        
        Args:
            key: Unique identifier (IP, user ID, API key)
            limit: Maximum requests allowed
            window_seconds: Time window in seconds
            
        Returns:
            Tuple of (is_allowed, rate_limit_info)
        """
        now = time.time()
        window_start = now - window_seconds
        
        async with self._lock:
            # Clean old requests
            self._requests[key] = [
                ts for ts in self._requests[key]
                if ts > window_start
            ]
            
            current_count = len(self._requests[key])
            
            if current_count >= limit:
                # Calculate retry after
                oldest = min(self._requests[key]) if self._requests[key] else now
                retry_after = int(oldest + window_seconds - now) + 1
                
                return False, {
                    "limit": limit,
                    "remaining": 0,
                    "reset": int(oldest + window_seconds),
                    "retry_after": retry_after,
                }
            
            # Add this request
            self._requests[key].append(now)
            
            return True, {
                "limit": limit,
                "remaining": limit - current_count - 1,
                "reset": int(now + window_seconds),
            }
    
    async def cleanup(self):
        """Remove expired entries to prevent memory growth."""
        now = time.time()
        async with self._lock:
            expired_keys = []
            for key, timestamps in self._requests.items():
                # If all timestamps are old, mark for removal
                if all(ts < now - 3600 for ts in timestamps):  # 1 hour old
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self._requests[key]


# Global rate limiter instance
rate_limiter = RateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware with configurable limits per endpoint.
    """
    
    # Rate limits: (requests, seconds)
    LIMITS = {
        "/v1/chat": (30, 60),       # 30 requests per minute
        "/v1/stt": (20, 60),        # 20 transcriptions per minute
        "/v1/tts": (20, 60),        # 20 TTS requests per minute
        "default": (100, 60),       # 100 requests per minute default
    }
    
    # Paths to skip rate limiting
    SKIP_PATHS = {"/healthz", "/favicon.ico", "/"}
    
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        
        # Skip rate limiting for certain paths
        if path in self.SKIP_PATHS or path.startswith("/ui"):
            return await call_next(request)
        
        # Get rate limit for this endpoint
        limit, window = self.LIMITS.get(path, self.LIMITS["default"])
        
        # Create rate limit key (IP-based, or user-based if authenticated)
        client_ip = self._get_client_ip(request)
        user_id = getattr(request.state, "user_id", None)
        key = f"{user_id or client_ip}:{path}"
        
        # Check rate limit
        allowed, info = await rate_limiter.is_allowed(key, limit, window)
        
        if not allowed:
            logger.warning(
                f"Rate limit exceeded for {key}",
                extra={
                    "event": "rate_limit_exceeded",
                    "key": key,
                    "path": path,
                }
            )
            
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": "Too many requests. Please slow down.",
                    "retry_after": info["retry_after"],
                },
                headers={
                    "X-RateLimit-Limit": str(info["limit"]),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(info["reset"]),
                    "Retry-After": str(info["retry_after"]),
                }
            )
        
        # Process request
        response = await call_next(request)
        
        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(info["limit"])
        response.headers["X-RateLimit-Remaining"] = str(info["remaining"])
        response.headers["X-RateLimit-Reset"] = str(info["reset"])
        
        return response
    
    def _get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"


# =====================================================
# AUTHENTICATION
# =====================================================

class SessionAuth:
    """
    Simple session-based authentication.
    
    For production, consider:
    - JWT tokens with proper signing
    - OAuth2 / OpenID Connect
    - Integration with Auth0, Firebase Auth, etc.
    """
    
    def __init__(self, secret_key: str = None):
        self.secret_key = secret_key or os.getenv("SESSION_SECRET", "")
        if not self.secret_key:
            logger.warning("⚠️ SESSION_SECRET not set - auth disabled in development")
        
        # In-memory session store (use Redis in production)
        self._sessions: Dict[str, dict] = {}
    
    def create_session(self, user_id: str, metadata: dict = None) -> str:
        """Create a new session token."""
        token = secrets.token_urlsafe(32)
        
        self._sessions[token] = {
            "user_id": user_id,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(days=7)).isoformat(),
            "metadata": metadata or {},
        }
        
        return token
    
    def validate_session(self, token: str) -> Optional[dict]:
        """Validate a session token and return session data."""
        if not token:
            return None
        
        session = self._sessions.get(token)
        if not session:
            return None
        
        # Check expiration
        expires_at = datetime.fromisoformat(session["expires_at"])
        if datetime.utcnow() > expires_at:
            del self._sessions[token]
            return None
        
        return session
    
    def invalidate_session(self, token: str):
        """Invalidate a session token."""
        self._sessions.pop(token, None)
    
    def generate_api_key(self, user_id: str) -> str:
        """Generate an API key for programmatic access."""
        # Create a deterministic but secure key
        payload = f"{user_id}:{secrets.token_hex(16)}"
        signature = hmac.new(
            self.secret_key.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()[:16]
        
        return f"mda_{signature}_{secrets.token_hex(8)}"


# Global auth instance
session_auth = SessionAuth()


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Authentication middleware.
    
    Supports:
    - Session tokens (X-Session-Token header)
    - API keys (Authorization: Bearer mda_xxx)
    - Optional auth mode for gradual rollout
    """
    
    # Paths that don't require authentication
    PUBLIC_PATHS = {
        "/",
        "/healthz",
        "/config",
        "/favicon.ico",
        "/twilio/sms",  # Twilio validates via signature
    }
    
    def __init__(self, app, require_auth: bool = None):
        super().__init__(app)
        # Default: require auth in production only
        if require_auth is None:
            require_auth = os.getenv("REQUIRE_AUTH", "false").lower() == "true"
        self.require_auth = require_auth
    
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        
        # Skip auth for public paths and static files
        if path in self.PUBLIC_PATHS or path.startswith("/ui"):
            return await call_next(request)
        
        # Try to authenticate
        user_id = None
        auth_method = None
        
        # Check for session token
        session_token = request.headers.get("X-Session-Token")
        if session_token:
            session = session_auth.validate_session(session_token)
            if session:
                user_id = session["user_id"]
                auth_method = "session"
        
        # Check for API key
        if not user_id:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer mda_"):
                api_key = auth_header[7:]  # Remove "Bearer "
                # In production, validate against stored API keys
                # For now, we'll accept any properly formatted key
                if len(api_key) > 20:
                    user_id = f"api_user_{api_key[:8]}"
                    auth_method = "api_key"
        
        # Store auth info on request
        request.state.user_id = user_id
        request.state.auth_method = auth_method
        
        # Set context var for logging
        if user_id:
            user_id_var.set(user_id)
        
        # Require auth if enabled
        if self.require_auth and not user_id:
            logger.warning(
                f"Unauthorized request to {path}",
                extra={"event": "auth_failed", "path": path}
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": "Authentication required",
                }
            )
        
        return await call_next(request)


# =====================================================
# SECURITY HEADERS MIDDLEWARE
# =====================================================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""
    
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Only add CSP for HTML responses
        if "text/html" in response.headers.get("content-type", ""):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data: https:; "
                "media-src 'self' blob:; "
                "connect-src 'self' https://api.cal.com https://cal.com; "
                "frame-src https://cal.com;"
            )
        
        return response