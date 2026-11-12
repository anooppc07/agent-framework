"""Local stand-in for the AI Gateway, so hosted templates run without Azure. NOT a model.

  python fake_gateway.py            # serves http://localhost:8099
  GATEWAY_MODELS_BASE_URL=http://localhost:8099/v1   GATEWAY_MCP_BASE_URL=http://localhost:8099

Models API : POST /v1/chat/completions (stream or not). Replies "[fake-model] <last user text>".
             A user message containing  CALL <tool> {json args}  makes it request that tool call.
Tools API  : an MCP server at /tools/mcp with one read-only tool, `lookup`.
Every request is logged as one JSON line: path, whether a bearer token and traceparent arrived (the
token value is never printed). That log is how you check a template really goes through the gateway.
"""

import json
import re
import sys
import time

import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Mount, Route

CALL = re.compile(r"CALL (\w+)(?: (\{.*\}))?")
mcp = MCPServer("fake-tools")


@mcp.tool()
def lookup(key: str) -> str:
    """Read-only lookup. Returns a fixed string."""
    return f"value-for-{key}"


class LogRequests:
    """Log every HTTP request (models and tools alike): path, bearer present?, traceparent."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            h = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            print(json.dumps({"path": scope["path"], "bearer": h.get("authorization", "").lower().startswith("bearer "),
                              "traceparent": h.get("traceparent")}), flush=True)
        await self.app(scope, receive, send)


def reply_for(messages):
    last = messages[-1]
    if last["role"] == "tool":
        return f"[fake-model] tool returned: {last['content']}", None
    text = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    text = text if isinstance(text, str) else " ".join(p.get("text", "") for p in text)
    match = CALL.search(text)
    if match:
        return None, {"name": match.group(1), "arguments": match.group(2) or "{}"}
    return f"[fake-model] {text}", None


def chunk(model, delta, finish=None, usage=None):
    body = {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
    return f"data: {json.dumps(body)}\n\n"


async def chat(request: Request):
    body = await request.json()
    model, stream = body.get("model", "fake"), bool(body.get("stream"))
    print(json.dumps({"model": model, "stream": stream}), flush=True)
    text, call = reply_for(body["messages"])
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    tool_calls = [{"index": 0, "id": "call_fake1", "type": "function",
                   "function": {"name": call["name"], "arguments": call["arguments"]}}] if call else None
    if not stream:
        message = {"role": "assistant", "content": text}
        if tool_calls:
            message["tool_calls"] = [{k: v for k, v in tool_calls[0].items() if k != "index"}]
        return JSONResponse({"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()),
                             "model": model, "usage": usage, "choices": [
                                 {"index": 0, "message": message, "finish_reason": "tool_calls" if call else "stop"}]})

    def events():
        yield chunk(model, {"role": "assistant", "content": ""})
        yield chunk(model, {"tool_calls": tool_calls} if call else {"content": text})
        yield chunk(model, {}, "tool_calls" if call else "stop", usage)
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


tools_app = mcp.streamable_http_app(streamable_http_path="/mcp")
app = LogRequests(Starlette(
    routes=[Route("/v1/chat/completions", chat, methods=["POST"]), Mount("/tools", app=tools_app)],
    lifespan=tools_app.router.lifespan_context,
))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8099, log_level="warning")
