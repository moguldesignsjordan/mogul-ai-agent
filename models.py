from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Dict, Any

Role = Literal["system", "user", "assistant", "tool"]

class Message(BaseModel):
    role: Role
    content: str | None = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = Field(default=None, alias="tool_call_id")

class ChatRequest(BaseModel):
    messages: List[Message]

class ChatResponse(BaseModel):
    message: Dict[str, Any]
