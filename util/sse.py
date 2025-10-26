from typing import AsyncGenerator

# Reserved for future streaming (SSE) helpers.
# For now, the /v1/chat endpoint returns JSON non-streaming.

async def sse_event_stream(gen) -> AsyncGenerator[str, None]:
    async for chunk in gen:
        yield f"data: {chunk}\n\n"
