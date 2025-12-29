"""
Structured Logging Configuration for Mogul AI Agent
Provides JSON logging for production and pretty logging for development.
"""

import logging
import json
import sys
import os
from datetime import datetime
from typing import Optional
from contextvars import ContextVar

# Context variable for request tracking across async calls
request_id_var: ContextVar[Optional[str]] = ContextVar('request_id', default=None)
user_id_var: ContextVar[Optional[str]] = ContextVar('user_id', default=None)


class JSONFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.
    Outputs logs in JSON format for easy parsing by log aggregators
    (DataDog, CloudWatch, Stackdriver, ELK, etc.)
    """
    
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add request context if available
        request_id = request_id_var.get()
        if request_id:
            log_obj["request_id"] = request_id
            
        user_id = user_id_var.get()
        if user_id:
            log_obj["user_id"] = user_id
        
        # Add any extra fields passed to the logger
        if hasattr(record, 'extra_fields'):
            log_obj.update(record.extra_fields)
        
        # Add exception info if present
        if record.exc_info:
            log_obj["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": self.formatException(record.exc_info),
            }
        
        return json.dumps(log_obj, default=str)


class PrettyFormatter(logging.Formatter):
    """
    Colored, human-readable formatter for local development.
    """
    
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
    }
    RESET = '\033[0m'
    
    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        
        # Build prefix with request ID if available
        request_id = request_id_var.get()
        prefix = f"[{request_id[:8]}] " if request_id else ""
        
        # Format timestamp
        timestamp = datetime.now().strftime('%H:%M:%S')
        
        # Build the log line
        base = f"{color}{timestamp} | {record.levelname:8}{self.RESET} | {prefix}{record.getMessage()}"
        
        # Add exception if present
        if record.exc_info:
            base += f"\n{self.formatException(record.exc_info)}"
        
        return base


class ContextLogger(logging.LoggerAdapter):
    """
    Logger adapter that automatically includes context fields.
    """
    
    def process(self, msg, kwargs):
        # Merge extra fields
        extra = kwargs.get('extra', {})
        
        # Add request context
        request_id = request_id_var.get()
        if request_id:
            extra['request_id'] = request_id
            
        user_id = user_id_var.get()
        if user_id:
            extra['user_id'] = user_id
        
        kwargs['extra'] = extra
        return msg, kwargs


def setup_logging(
    name: str = "mogul",
    level: str = None,
    json_format: bool = None
) -> logging.Logger:
    """
    Configure and return a logger instance.
    
    Args:
        name: Logger name
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: If True, use JSON format. If None, auto-detect from environment.
    
    Returns:
        Configured logger instance
    """
    # Determine log level
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO").upper()
    
    # Determine format (JSON for production, pretty for dev)
    if json_format is None:
        # Use JSON in production (when not in DEBUG mode and not in TTY)
        is_production = os.getenv("ENVIRONMENT", "development") == "production"
        is_tty = sys.stdout.isatty()
        json_format = is_production or not is_tty
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level))
    
    # Remove existing handlers
    logger.handlers.clear()
    
    # Create handler with appropriate formatter
    handler = logging.StreamHandler(sys.stdout)
    
    if json_format:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(PrettyFormatter())
    
    logger.addHandler(handler)
    
    # Prevent propagation to root logger
    logger.propagate = False
    
    return logger


def get_logger(name: str = "mogul") -> logging.Logger:
    """Get or create a logger with the given name."""
    return logging.getLogger(name)


# Convenience function for logging with extra fields
def log_with_context(
    logger: logging.Logger,
    level: int,
    message: str,
    **extra_fields
):
    """
    Log a message with additional context fields.
    
    Usage:
        log_with_context(logger, logging.INFO, "User action", action="login", user_id="123")
    """
    record = logger.makeRecord(
        logger.name,
        level,
        "(unknown)",
        0,
        message,
        (),
        None
    )
    record.extra_fields = extra_fields
    logger.handle(record)