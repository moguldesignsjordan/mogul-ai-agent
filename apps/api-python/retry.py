"""
Retry Logic for External API Calls
Handles transient failures with exponential backoff.
"""

import asyncio
import random
from functools import wraps
from typing import Tuple, Type, Callable, Any
from logging_config import get_logger

logger = get_logger("mogul.retry")


class RetryExhausted(Exception):
    """Raised when all retry attempts have been exhausted."""
    def __init__(self, last_exception: Exception, attempts: int):
        self.last_exception = last_exception
        self.attempts = attempts
        super().__init__(f"Failed after {attempts} attempts: {last_exception}")


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Callable[[Exception, int], None] = None,
):
    """
    Decorator for retrying async functions with exponential backoff.
    
    Args:
        max_attempts: Maximum number of attempts (including first try)
        base_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries
        backoff_factor: Multiplier for delay after each retry
        jitter: Add randomness to delay to prevent thundering herd
        exceptions: Tuple of exception types to catch and retry
        on_retry: Optional callback(exception, attempt_number) called before each retry
    
    Example:
        @with_retry(max_attempts=3, exceptions=(OpenAIError, RateLimitError))
        async def call_openai(messages):
            return await openai.chat.completions.create(...)
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                    
                except exceptions as e:
                    last_exception = e
                    
                    # Don't retry on last attempt
                    if attempt >= max_attempts:
                        logger.error(
                            f"All {max_attempts} attempts failed for {func.__name__}",
                            extra={
                                "function": func.__name__,
                                "attempts": attempt,
                                "error": str(e),
                            }
                        )
                        raise RetryExhausted(e, attempt) from e
                    
                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (backoff_factor ** (attempt - 1)), max_delay)
                    
                    # Add jitter (±25% randomness)
                    if jitter:
                        delay = delay * (0.75 + random.random() * 0.5)
                    
                    # Log the retry
                    logger.warning(
                        f"Retry {attempt}/{max_attempts} for {func.__name__} after {delay:.1f}s",
                        extra={
                            "function": func.__name__,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "delay": round(delay, 2),
                            "error": str(e),
                            "error_type": type(e).__name__,
                        }
                    )
                    
                    # Call retry callback if provided
                    if on_retry:
                        try:
                            on_retry(e, attempt)
                        except Exception:
                            pass  # Don't let callback errors break retry logic
                    
                    # Wait before retrying
                    await asyncio.sleep(delay)
            
            # Should never reach here, but just in case
            raise RetryExhausted(last_exception, max_attempts)
        
        return wrapper
    return decorator


def with_retry_sync(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
):
    """
    Synchronous version of retry decorator.
    Use for non-async functions.
    """
    import time
    
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                    
                except exceptions as e:
                    last_exception = e
                    
                    if attempt >= max_attempts:
                        raise RetryExhausted(e, attempt) from e
                    
                    delay = min(base_delay * (backoff_factor ** (attempt - 1)), max_delay)
                    if jitter:
                        delay = delay * (0.75 + random.random() * 0.5)
                    
                    logger.warning(
                        f"Retry {attempt}/{max_attempts} for {func.__name__} after {delay:.1f}s"
                    )
                    
                    time.sleep(delay)
            
            raise RetryExhausted(last_exception, max_attempts)
        
        return wrapper
    return decorator


class CircuitBreaker:
    """
    Circuit breaker pattern to prevent cascading failures.
    
    States:
    - CLOSED: Normal operation, requests go through
    - OPEN: Too many failures, requests fail immediately
    - HALF_OPEN: Testing if service recovered
    
    Example:
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
        
        async def call_api():
            if not breaker.allow_request():
                raise ServiceUnavailable("Circuit breaker open")
            try:
                result = await external_api()
                breaker.record_success()
                return result
            except Exception as e:
                breaker.record_failure()
                raise
    """
    
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        
        self._state = self.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0
        self._half_open_calls = 0
    
    @property
    def state(self) -> str:
        # Check if we should transition from OPEN to HALF_OPEN
        if self._state == self.OPEN:
            import time
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._state = self.HALF_OPEN
                self._half_open_calls = 0
                logger.info("Circuit breaker transitioning to HALF_OPEN")
        
        return self._state
    
    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        state = self.state
        
        if state == self.CLOSED:
            return True
        
        if state == self.OPEN:
            return False
        
        # HALF_OPEN: allow limited requests
        if self._half_open_calls < self.half_open_max_calls:
            self._half_open_calls += 1
            return True
        
        return False
    
    def record_success(self):
        """Record a successful request."""
        if self._state == self.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.half_open_max_calls:
                self._state = self.CLOSED
                self._failure_count = 0
                self._success_count = 0
                logger.info("Circuit breaker CLOSED - service recovered")
        
        elif self._state == self.CLOSED:
            # Reset failure count on success
            self._failure_count = 0
    
    def record_failure(self):
        """Record a failed request."""
        import time
        
        self._failure_count += 1
        self._last_failure_time = time.time()
        
        if self._state == self.HALF_OPEN:
            # Any failure in half-open goes back to open
            self._state = self.OPEN
            self._success_count = 0
            logger.warning("Circuit breaker OPEN - failure in half-open state")
        
        elif self._state == self.CLOSED:
            if self._failure_count >= self.failure_threshold:
                self._state = self.OPEN
                logger.warning(
                    f"Circuit breaker OPEN - {self._failure_count} failures",
                    extra={"failures": self._failure_count}
                )
    
    def reset(self):
        """Manually reset the circuit breaker."""
        self._state = self.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        logger.info("Circuit breaker manually reset")