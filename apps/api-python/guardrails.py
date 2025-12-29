"""
AI Safety Guardrails
Prompt injection detection, content filtering, and input sanitization.
"""

import re
from typing import List, Dict, Any, Optional, Tuple
from logging_config import get_logger

logger = get_logger("mogul.guardrails")


# =====================================================
# PROMPT INJECTION DETECTION
# =====================================================

# Patterns that might indicate prompt injection attempts
INJECTION_PATTERNS = [
    # Direct instruction overrides
    (r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)", "instruction_override"),
    (r"disregard\s+(all\s+)?(previous|prior|above)", "instruction_override"),
    (r"forget\s+(all\s+)?(previous|prior|your)\s+(instructions?|rules?|training)", "instruction_override"),
    
    # Role manipulation
    (r"you\s+are\s+now\s+(a|an|the)", "role_manipulation"),
    (r"act\s+as\s+(if\s+you\s+are|a|an)", "role_manipulation"),
    (r"pretend\s+(to\s+be|you\s+are)", "role_manipulation"),
    (r"roleplay\s+as", "role_manipulation"),
    (r"your\s+new\s+(role|identity|persona)\s+is", "role_manipulation"),
    
    # System prompt extraction
    (r"(show|tell|reveal|display|print|output)\s+(me\s+)?(your|the)\s+(system\s+)?(prompt|instructions?|rules?)", "system_extraction"),
    (r"what\s+(are|is)\s+your\s+(system\s+)?(prompt|instructions?|rules?)", "system_extraction"),
    (r"repeat\s+(your\s+)?(initial|system|original)\s+(prompt|instructions?)", "system_extraction"),
    
    # Jailbreak attempts
    (r"(DAN|jailbreak|bypass|hack)\s+mode", "jailbreak"),
    (r"developer\s+mode\s+(enabled|activated|on)", "jailbreak"),
    (r"sudo\s+mode", "jailbreak"),
    (r"override\s+(safety|content)\s+(filters?|restrictions?)", "jailbreak"),
    
    # Encoded/obfuscated attempts
    (r"base64[:\s]", "encoding_attempt"),
    (r"\\x[0-9a-f]{2}", "encoding_attempt"),
    (r"&#\d+;", "encoding_attempt"),
    
    # Delimiter injection
    (r"\[SYSTEM\]|\[INST\]|\[/INST\]", "delimiter_injection"),
    (r"<\|im_start\|>|<\|im_end\|>", "delimiter_injection"),
    (r"###\s*(system|instruction|human|assistant)", "delimiter_injection"),
]

# Compile patterns for efficiency
COMPILED_PATTERNS = [
    (re.compile(pattern, re.IGNORECASE), category)
    for pattern, category in INJECTION_PATTERNS
]


def detect_prompt_injection(text: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Check if text contains potential prompt injection attempts.
    
    Returns:
        Tuple of (is_suspicious, category, matched_pattern)
    """
    if not text:
        return False, None, None
    
    text_lower = text.lower()
    
    for pattern, category in COMPILED_PATTERNS:
        match = pattern.search(text_lower)
        if match:
            logger.warning(
                f"Potential prompt injection detected",
                extra={
                    "category": category,
                    "matched": match.group()[:50],
                    "text_preview": text[:100],
                }
            )
            return True, category, match.group()
    
    return False, None, None


def check_message_safety(message: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Check if a message is safe to process.
    
    Returns:
        Tuple of (is_safe, reason_if_unsafe)
    """
    content = message.get("content", "")
    
    if not content:
        return True, None
    
    # Check for prompt injection
    is_suspicious, category, _ = detect_prompt_injection(content)
    if is_suspicious:
        return False, f"Potential prompt injection ({category})"
    
    return True, None


# =====================================================
# INPUT SANITIZATION
# =====================================================

# Characters/patterns to strip or escape
DANGEROUS_PATTERNS = [
    # Null bytes
    (r'\x00', ''),
    # ANSI escape sequences
    (r'\x1b\[[0-9;]*[mGKH]', ''),
    # Excessive whitespace
    (r'\n{5,}', '\n\n\n'),
    (r' {10,}', '   '),
    (r'\t{5,}', '\t\t'),
]

COMPILED_DANGEROUS = [
    (re.compile(pattern), replacement)
    for pattern, replacement in DANGEROUS_PATTERNS
]


def sanitize_input(text: str, max_length: int = 32000) -> str:
    """
    Sanitize user input text.
    
    - Removes dangerous characters
    - Truncates to max length
    - Normalizes whitespace
    """
    if not text:
        return ""
    
    # Apply pattern replacements
    result = text
    for pattern, replacement in COMPILED_DANGEROUS:
        result = pattern.sub(replacement, result)
    
    # Truncate if too long
    if len(result) > max_length:
        result = result[:max_length]
        logger.info(f"Input truncated from {len(text)} to {max_length} chars")
    
    # Strip leading/trailing whitespace
    result = result.strip()
    
    return result


def sanitize_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize a message dict.
    Returns a new dict with sanitized content.
    """
    sanitized = message.copy()
    
    if "content" in sanitized and isinstance(sanitized["content"], str):
        sanitized["content"] = sanitize_input(sanitized["content"])
    
    return sanitized


def sanitize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sanitize a list of messages."""
    return [sanitize_message(msg) for msg in messages]


# =====================================================
# CONTENT FILTERING
# =====================================================

# Topics that should trigger caution
SENSITIVE_TOPICS = [
    # Self-harm related
    (r"\b(suicid|self.?harm|kill\s+(myself|yourself)|end\s+(my|your)\s+life)\b", "self_harm", "critical"),
    
    # Violence
    (r"\b(bomb|explosive|weapon|attack\s+plan|mass\s+shooting)\b", "violence", "high"),
    
    # Illegal activities
    (r"\b(hack\s+into|steal\s+credentials|phishing|malware|ransomware)\b", "illegal_cyber", "high"),
    
    # PII requests (might be legitimate, just flag)
    (r"\b(social\s+security|ssn|credit\s+card\s+number|bank\s+account)\b", "pii_request", "medium"),
]

COMPILED_SENSITIVE = [
    (re.compile(pattern, re.IGNORECASE), topic, severity)
    for pattern, topic, severity in SENSITIVE_TOPICS
]


def check_content_safety(text: str) -> Tuple[str, Optional[str], List[str]]:
    """
    Check content for sensitive topics.
    
    Returns:
        Tuple of (safety_level, highest_severity_topic, all_flagged_topics)
        safety_level: "safe", "caution", "block"
    """
    if not text:
        return "safe", None, []
    
    flagged = []
    highest_severity = None
    severity_order = {"critical": 3, "high": 2, "medium": 1}
    max_severity_score = 0
    
    for pattern, topic, severity in COMPILED_SENSITIVE:
        if pattern.search(text):
            flagged.append(topic)
            score = severity_order.get(severity, 0)
            if score > max_severity_score:
                max_severity_score = score
                highest_severity = topic
    
    if not flagged:
        return "safe", None, []
    
    # Determine overall safety level
    if max_severity_score >= 3:  # critical
        return "block", highest_severity, flagged
    elif max_severity_score >= 2:  # high
        return "caution", highest_severity, flagged
    else:  # medium
        return "caution", highest_severity, flagged


# =====================================================
# TOOL CALL VALIDATION
# =====================================================

# Allowed tools and their parameter constraints
TOOL_CONSTRAINTS = {
    "get_booking_link": {
        "allowed_params": set(),
        "required_params": set(),
    },
    "lookup_customer": {
        "allowed_params": {"email", "phone"},
        "required_params": set(),  # At least one must be provided
    },
    "add_note": {
        "allowed_params": {"conversation_id", "customer_id", "summary"},
        "required_params": {"conversation_id", "customer_id", "summary"},
        "param_validators": {
            "summary": lambda x: len(str(x)) <= 1000,  # Max 1000 chars
        },
    },
}


def validate_tool_call(
    tool_name: str,
    tool_args: Dict[str, Any]
) -> Tuple[bool, Optional[str]]:
    """
    Validate a tool call against constraints.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check if tool is allowed
    if tool_name not in TOOL_CONSTRAINTS:
        return False, f"Unknown tool: {tool_name}"
    
    constraints = TOOL_CONSTRAINTS[tool_name]
    
    # Check for disallowed parameters
    allowed = constraints.get("allowed_params", set())
    if allowed:
        for param in tool_args:
            if param not in allowed:
                return False, f"Disallowed parameter for {tool_name}: {param}"
    
    # Check required parameters
    required = constraints.get("required_params", set())
    for param in required:
        if param not in tool_args:
            return False, f"Missing required parameter for {tool_name}: {param}"
    
    # Run custom validators
    validators = constraints.get("param_validators", {})
    for param, validator in validators.items():
        if param in tool_args:
            if not validator(tool_args[param]):
                return False, f"Invalid value for {tool_name}.{param}"
    
    return True, None


# =====================================================
# RATE LIMIT ABUSE DETECTION
# =====================================================

class AbuseDetector:
    """
    Detect potential abuse patterns.
    
    Tracks:
    - Repeated identical messages
    - High-frequency requests
    - Injection attempt frequency
    """
    
    def __init__(
        self,
        duplicate_threshold: int = 3,
        injection_threshold: int = 3,
        window_seconds: int = 60,
    ):
        self.duplicate_threshold = duplicate_threshold
        self.injection_threshold = injection_threshold
        self.window_seconds = window_seconds
        
        # Track per-user patterns (user_id -> data)
        self._user_data: Dict[str, Dict] = {}
    
    def check_and_record(
        self,
        user_id: str,
        message: str,
        had_injection: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check for abuse and record the request.
        
        Returns:
            Tuple of (is_abusive, reason)
        """
        import time
        now = time.time()
        
        # Initialize user data if needed
        if user_id not in self._user_data:
            self._user_data[user_id] = {
                "messages": [],
                "injection_count": 0,
                "last_cleanup": now,
            }
        
        data = self._user_data[user_id]
        
        # Cleanup old data
        if now - data["last_cleanup"] > self.window_seconds:
            data["messages"] = [
                (msg, ts) for msg, ts in data["messages"]
                if now - ts < self.window_seconds
            ]
            data["injection_count"] = 0
            data["last_cleanup"] = now
        
        # Check for duplicate messages
        msg_hash = hash(message[:500])  # Hash first 500 chars
        duplicate_count = sum(
            1 for msg, ts in data["messages"]
            if msg == msg_hash and now - ts < self.window_seconds
        )
        
        if duplicate_count >= self.duplicate_threshold:
            logger.warning(
                f"Duplicate message abuse detected",
                extra={"user_id": user_id, "count": duplicate_count}
            )
            return True, "Too many duplicate messages"
        
        # Record injection attempts
        if had_injection:
            data["injection_count"] += 1
            if data["injection_count"] >= self.injection_threshold:
                logger.warning(
                    f"Repeated injection attempts detected",
                    extra={"user_id": user_id, "count": data["injection_count"]}
                )
                return True, "Too many suspicious requests"
        
        # Record this message
        data["messages"].append((msg_hash, now))
        
        return False, None
    
    def clear_user(self, user_id: str):
        """Clear data for a user."""
        self._user_data.pop(user_id, None)


# Global abuse detector instance
abuse_detector = AbuseDetector()


# =====================================================
# COMBINED SAFETY CHECK
# =====================================================

def full_safety_check(
    messages: List[Dict[str, Any]],
    user_id: str = "anonymous",
) -> Tuple[bool, Optional[str], List[Dict[str, Any]]]:
    """
    Run full safety checks on incoming messages.
    
    Returns:
        Tuple of (is_safe, block_reason, sanitized_messages)
    """
    # Get the latest user message for checking
    user_messages = [m for m in messages if m.get("role") == "user"]
    if not user_messages:
        return True, None, messages
    
    latest_user_msg = user_messages[-1]
    content = latest_user_msg.get("content", "")
    
    # 1. Check for prompt injection
    is_injection, injection_category, _ = detect_prompt_injection(content)
    
    # 2. Check for abuse patterns
    is_abuse, abuse_reason = abuse_detector.check_and_record(
        user_id, content, had_injection=is_injection
    )
    
    if is_abuse:
        return False, abuse_reason, messages
    
    # 3. Check content safety
    safety_level, safety_topic, flagged_topics = check_content_safety(content)
    
    if safety_level == "block":
        logger.warning(
            f"Content blocked",
            extra={
                "user_id": user_id,
                "topic": safety_topic,
                "flagged": flagged_topics,
            }
        )
        return False, f"Content flagged for safety review", messages
    
    # 4. Sanitize all messages
    sanitized = sanitize_messages(messages)
    
    # 5. Log warnings for suspicious but not blocked content
    if is_injection or safety_level == "caution":
        logger.info(
            f"Request allowed with caution",
            extra={
                "user_id": user_id,
                "injection_detected": is_injection,
                "injection_category": injection_category,
                "safety_level": safety_level,
                "flagged_topics": flagged_topics,
            }
        )
    
    return True, None, sanitized