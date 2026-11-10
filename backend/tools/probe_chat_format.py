"""对话模板变体探针。

原先这个脚本对照两个变体：模型自带模板与强制 chatml。
自写推理服务端（world-work-cpp）固定用权重自带的 tokenizer.chat_template，
没有模板开关，第二个变体因此不再存在：这里只跑自带模板这一个变体。
"""

import sys
import time
from pathlib import Path

import httpx

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR.parent))

from bench_local import HOST, free_port  # noqa: E402
from multi_turn import SYSTEM, TURNS, start_server, stop_server, wait_ready  # noqa: E402


def probe(model: str, extra_args: list[str]) -> dict:
    port = free_port()
    base = f"http://{HOST}:{port}"
    proc, log_path, handle = start_server(model, extra_args, port)
    out = {'model': model, 'args': extra_args}
    try:
        if not wait_ready(base, proc, timeout=300.0):
            out['error'] = '服务启动超时'
            return out
        time.sleep(1.0)
        resp = httpx.post(
            f"{base}/v1/chat/completions",
            json={
                "model": "local",
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": TURNS[0]}],
                "max_tokens": 400,
                "temperature": 0.8,
                "seed": 42,
            },
            timeout=600.0,
        )
        body = resp.json()
        choice = (body.get('choices') or [{}])[0]
        out['content'] = str(choice.get('message', {}).get('content') or '')
        out['finish_reason'] = choice.get('finish_reason')
        out['usage'] = body.get('usage')
    except Exception as exc:  # noqa: BLE001
        out['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        stop_server(proc, log_path, handle)
    return out


if __name__ == '__main__':
    model = sys.argv[1]
    # 只有一个变体：自写服务端没有模板开关，权重自带模板即全部。
    variants = [
        ['--ctx-size', '4096', '--n-gpu-layers', '999'],
    ]
    for extra in variants:
        result = probe(model, extra)
        print('---', result.get('args'), result.get('error') or '')
        print('   finish:', result.get('finish_reason'), 'usage:', result.get('usage'))
        print('   text:', repr(result.get('content', ''))[:500])
