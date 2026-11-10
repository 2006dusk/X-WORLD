
import asyncio, json, os, sys
# Repo root, derived from this file so the script carries no local path.
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))
BACKEND = os.path.join(REPO, 'backend')
sys.path.insert(0, BACKEND)
os.environ.setdefault("XWORLD_DATA_DIR", os.path.join(os.environ.get("APPDATA", ""), "X-WORLD"))

from app.contracts import ChatMessage
from app.inference.llamacpp import LlamaCppAdapter
from app.inference.base import InferenceParams

async def main():
    adapter = LlamaCppAdapter(base_url="http://127.0.0.1:8082")
    params = InferenceParams(temperature=0.8, top_p=0.95, max_tokens=64, repetition_penalty=1.1)
    msgs = [
        ChatMessage(role="system", content="You are Li Hanyang, the empress. Answer in short classical Chinese."),
        ChatMessage(role="user", content="Who are you?"),
    ]
    out = {"provider": adapter.provider}
    res = await adapter.chat(msgs, params, profile={"top_k": 40})
    out["non_stream"] = {
        "content": (res.content or "")[:120],
        "reasoning": (res.reasoning or "")[:60],
        "finish_reason": res.finish_reason,
        "prompt_tokens": res.usage.prompt_tokens,
        "completion_tokens": res.usage.completion_tokens,
    }
    events = []
    async for ev in adapter.stream_chat(msgs, params, profile={"top_k": 40}):
        events.append(ev.event)
        if ev.event == "delta":
            out.setdefault("stream_text", "")
            out["stream_text"] += (ev.data.get("content") or "")
        elif ev.event == "reasoning":
            out.setdefault("stream_reasoning", "")
            out["stream_reasoning"] += (ev.data.get("content") or "")
    out["stream_events"] = events[:6] + ["..."] + events[-3:]
    out["stream_text"] = out.get("stream_text", "")[:120]
    out["stream_reasoning_len"] = len(out.get("stream_reasoning", ""))
    print(json.dumps(out, ensure_ascii=True, indent=1))

asyncio.run(main())
