"""
Conversation Management
Handles context window limits, token counting, and message history.
"""

import json
from typing import List, Dict, Any, Optional
from logging_config import get_logger

logger = get_logger("mogul.conversation")


# =====================================================
# TOKEN COUNTING
# =====================================================

# Approximate token counts per model
MODEL_CONTEXT_LIMITS = {
    "gpt-4o-mini": 128000,
    "gpt-4o": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 16385,
}

# Reserve tokens for response
RESPONSE_TOKEN_RESERVE = 4096

# Maximum tokens for user input (safety limit)
MAX_USER_MESSAGE_TOKENS = 8000


def estimate_tokens(text: str) -> int:
    """
    Estimate token count for a string.
    
    This is a rough approximation. For production accuracy,
    use tiktoken: pip install tiktoken
    
    Approximation rules:
    - ~4 characters per token for English
    - ~3.5 characters per token for mixed content
    """
    if not text:
        return 0
    
    # Rough estimation: 1 token ≈ 4 characters
    char_count = len(text)
    estimated = char_count // 4
    
    # Add overhead for whitespace and special characters
    whitespace_count = text.count(' ') + text.count('\n')
    estimated += whitespace_count // 4
    
    return max(1, estimated)


def count_message_tokens(message: Dict[str, Any]) -> int:
    """
    Count tokens in a single message.
    
    Message format: {"role": "...", "content": "...", ...}
    """
    tokens = 4  # Base overhead per message
    
    # Count content
    content = message.get("content", "")
    if isinstance(content, str):
        tokens += estimate_tokens(content)
    elif isinstance(content, list):
        # Multi-modal content (text + images)
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    tokens += estimate_tokens(part.get("text", ""))
                elif part.get("type") == "image_url":
                    tokens += 85  # Base image token cost
    
    # Count tool calls
    if "tool_calls" in message:
        for tc in message.get("tool_calls", []):
            tokens += estimate_tokens(tc.get("function", {}).get("name", ""))
            tokens += estimate_tokens(tc.get("function", {}).get("arguments", ""))
    
    # Count function/tool response
    if message.get("role") == "tool":
        tokens += estimate_tokens(message.get("content", ""))
    
    return tokens


def count_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """Count total tokens in a message list."""
    total = 3  # Base overhead for message array
    for msg in messages:
        total += count_message_tokens(msg)
    return total


# =====================================================
# TRY TO USE TIKTOKEN IF AVAILABLE
# =====================================================

try:
    import tiktoken
    
    _encoders = {}
    
    def get_encoder(model: str):
        """Get or create tiktoken encoder for model."""
        if model not in _encoders:
            try:
                _encoders[model] = tiktoken.encoding_for_model(model)
            except KeyError:
                # Fall back to cl100k_base for unknown models
                _encoders[model] = tiktoken.get_encoding("cl100k_base")
        return _encoders[model]
    
    def estimate_tokens(text: str, model: str = "gpt-4o-mini") -> int:
        """Accurate token count using tiktoken."""
        if not text:
            return 0
        encoder = get_encoder(model)
        return len(encoder.encode(text))
    
    logger.info("✅ Using tiktoken for accurate token counting")
    
except ImportError:
    logger.info("ℹ️ tiktoken not installed, using estimated token counts")


# =====================================================
# CONTEXT WINDOW MANAGEMENT
# =====================================================

def trim_conversation_history(
    messages: List[Dict[str, Any]],
    model: str = "gpt-4o-mini",
    max_tokens: Optional[int] = None,
    preserve_system: bool = True,
    preserve_recent: int = 4,
) -> List[Dict[str, Any]]:
    """
    Trim conversation history to fit within token limits.
    
    Strategy:
    1. Always keep system message (if preserve_system=True)
    2. Always keep most recent N messages (preserve_recent)
    3. Remove oldest messages first until under limit
    
    Args:
        messages: List of message dicts
        model: Model name for context limit lookup
        max_tokens: Override context limit (default: model's limit - reserve)
        preserve_system: Keep system messages regardless of position
        preserve_recent: Minimum number of recent messages to keep
    
    Returns:
        Trimmed message list
    """
    if not messages:
        return messages
    
    # Determine token limit
    if max_tokens is None:
        model_limit = MODEL_CONTEXT_LIMITS.get(model, 8192)
        max_tokens = model_limit - RESPONSE_TOKEN_RESERVE
    
    # Separate system messages and others
    system_msgs = []
    other_msgs = []
    
    for msg in messages:
        if msg.get("role") == "system" and preserve_system:
            system_msgs.append(msg)
        else:
            other_msgs.append(msg)
    
    # Count system message tokens (always included)
    system_tokens = count_messages_tokens(system_msgs)
    available_tokens = max_tokens - system_tokens
    
    if available_tokens <= 0:
        logger.warning("System prompt exceeds available context")
        return system_msgs
    
    # Always keep the most recent messages
    preserved = other_msgs[-preserve_recent:] if len(other_msgs) > preserve_recent else other_msgs[:]
    older = other_msgs[:-preserve_recent] if len(other_msgs) > preserve_recent else []
    
    # Calculate tokens for preserved messages
    preserved_tokens = count_messages_tokens(preserved)
    
    # If preserved messages already exceed limit, we have a problem
    if preserved_tokens > available_tokens:
        logger.warning(
            f"Recent messages ({preserved_tokens} tokens) exceed available context ({available_tokens})"
        )
        # Still return what we can
        return system_msgs + preserved
    
    # Add older messages from most recent to oldest until we hit the limit
    remaining_tokens = available_tokens - preserved_tokens
    kept_older = []
    
    for msg in reversed(older):
        msg_tokens = count_message_tokens(msg)
        if msg_tokens <= remaining_tokens:
            kept_older.insert(0, msg)
            remaining_tokens -= msg_tokens
        else:
            break  # Stop adding older messages
    
    # Calculate how many messages we dropped
    dropped = len(older) - len(kept_older)
    if dropped > 0:
        logger.info(
            f"Trimmed {dropped} messages to fit context window",
            extra={
                "dropped_messages": dropped,
                "kept_messages": len(system_msgs) + len(kept_older) + len(preserved),
                "total_tokens": max_tokens - remaining_tokens,
            }
        )
    
    return system_msgs + kept_older + preserved


def summarize_for_context(
    messages: List[Dict[str, Any]],
    max_summary_tokens: int = 500,
) -> str:
    """
    Create a brief summary of older messages for context preservation.
    This can be used as a "memory" when trimming isn't enough.
    
    Note: This is a simple extraction. For production, you might
    use an LLM to generate a proper summary.
    """
    if not messages:
        return ""
    
    # Extract key information
    topics = []
    user_requests = []
    
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        
        if role == "user" and content:
            # Take first sentence or first 100 chars
            snippet = content[:100].split('.')[0]
            if snippet and snippet not in user_requests:
                user_requests.append(snippet)
    
    if not user_requests:
        return ""
    
    # Build summary
    summary_parts = ["Previous conversation covered:"]
    for req in user_requests[:5]:  # Limit to 5 topics
        summary_parts.append(f"- {req}")
    
    summary = "\n".join(summary_parts)
    
    # Trim if too long
    if estimate_tokens(summary) > max_summary_tokens:
        summary = summary[:max_summary_tokens * 4]  # Rough char limit
    
    return summary


# =====================================================
# MESSAGE VALIDATION
# =====================================================

def validate_message(message: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    Validate a single message.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    role = message.get("role")
    content = message.get("content")
    
    # Check role
    valid_roles = {"system", "user", "assistant", "tool"}
    if role not in valid_roles:
        return False, f"Invalid role: {role}"
    
    # User messages must have content
    if role == "user":
        if not content or not str(content).strip():
            return False, "User message cannot be empty"
        
        # Check token limit
        tokens = estimate_tokens(str(content))
        if tokens > MAX_USER_MESSAGE_TOKENS:
            return False, f"Message too long ({tokens} tokens, max {MAX_USER_MESSAGE_TOKENS})"
    
    # Tool messages need tool_call_id
    if role == "tool" and not message.get("tool_call_id"):
        return False, "Tool message requires tool_call_id"
    
    return True, None


def validate_messages(messages: List[Dict[str, Any]]) -> tuple[bool, List[str]]:
    """
    Validate a list of messages.
    
    Returns:
        Tuple of (all_valid, list_of_errors)
    """
    errors = []
    
    if not messages:
        return False, ["Messages list cannot be empty"]
    
    for i, msg in enumerate(messages):
        is_valid, error = validate_message(msg)
        if not is_valid:
            errors.append(f"Message {i}: {error}")
    
    return len(errors) == 0, errors


# =====================================================
# CONVERSATION STORAGE HELPERS
# =====================================================

class ConversationBuffer:
    """
    In-memory conversation buffer with automatic trimming.
    
    For production, extend this to persist to Redis or database.
    """
    
    def __init__(
        self,
        max_messages: int = 100,
        max_tokens: int = 50000,
        model: str = "gpt-4o-mini",
    ):
        self.max_messages = max_messages
        self.max_tokens = max_tokens
        self.model = model
        self._messages: List[Dict[str, Any]] = []
        self._token_count = 0
    
    def add(self, message: Dict[str, Any]):
        """Add a message to the buffer."""
        self._messages.append(message)
        self._token_count += count_message_tokens(message)
        
        # Auto-trim if needed
        self._trim_if_needed()
    
    def add_many(self, messages: List[Dict[str, Any]]):
        """Add multiple messages."""
        for msg in messages:
            self.add(msg)
    
    def get_messages(self) -> List[Dict[str, Any]]:
        """Get all messages in buffer."""
        return self._messages.copy()
    
    def get_for_completion(self, system_prompt: str = None) -> List[Dict[str, Any]]:
        """Get messages ready for API call, with optional system prompt."""
        messages = []
        
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        messages.extend(self._messages)
        
        return trim_conversation_history(
            messages,
            model=self.model,
            max_tokens=self.max_tokens,
        )
    
    def clear(self):
        """Clear all messages."""
        self._messages = []
        self._token_count = 0
    
    @property
    def token_count(self) -> int:
        """Current token count."""
        return self._token_count
    
    @property
    def message_count(self) -> int:
        """Current message count."""
        return len(self._messages)
    
    def _trim_if_needed(self):
        """Trim buffer if exceeding limits."""
        # Trim by message count
        while len(self._messages) > self.max_messages:
            removed = self._messages.pop(0)
            self._token_count -= count_message_tokens(removed)
        
        # Trim by token count
        while self._token_count > self.max_tokens and len(self._messages) > 1:
            removed = self._messages.pop(0)
            self._token_count -= count_message_tokens(removed)