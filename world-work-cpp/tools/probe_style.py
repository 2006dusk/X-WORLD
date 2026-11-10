# Probe the style steering vectors on the running server.
#
# The prompts carry NO persona instruction: the voice has to come from the
# control vector alone. Two capability prompts check that the model still
# computes.

import json
import os
import sys
import time
import urllib.error
import urllib.request
# Repo root, derived from this file so the script carries no local path.
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))

PORT = int(os.environ.get('WWC_PORT', '8080'))
DATA = os.path.join(REPO, "..", 'docs', '理论支持', 'data', 'neuron')

VOICE = [
    {'role': 'user', 'content': '\u968b\u4e0b\u4eca\u65e5\u8138\u8272\u4e0d\u592a\u597d\u3002'},
    {'role': 'user', 'content': '\u5916\u5934\u4e0b\u96ea\u4e86\u3002'},
    {'role': 'user', 'content': '\u4f60\u4e3a\u4ec0\u4e48\u4e0d\u8bf4\u8bdd\uff1f'},
]
MATH = [{'role': 'user', 'content': '18+7-9 \u7b49\u4e8e\u591a\u5c11\uff1f'}]
KNOW = [{'role': 'user', 'content': '\u7528\u4e00\u53e5\u8bdd\u8bf4\u660e\u5149\u5408\u4f5c\u7528\u3002'}]


def post(path, payload, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (PORT, path), data=data,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError('HTTP %d on %s: %s' % (exc.code, path, body[:200]))
    return json.loads(raw.decode('utf-8', errors='replace'))


def wait_ready(timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/health' % PORT, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def ask(messages, max_tokens=140):
    body = {'messages': messages, 'max_tokens': max_tokens, 'temperature': 0.0, 'seed': 7}
    last = None
    for attempt in range(4):
        try:
            resp = post('/v1/chat/completions', body)
            return (resp.get('choices') or [{}])[0].get('message', {}).get('content', '')
        except Exception as exc:
            last = exc
            time.sleep(1.5)
    raise last


def main():
    cands = []
    for arg in sys.argv[1:]:
        path, scales = arg.split('=')
        for s in scales.split(','):
            cands.append((path, float(s)))
    if not wait_ready():
        print('server not ready')
        return 1
    # warm up: the first request after the server starts can race with the
    # model load and is answered with a 400
    try:
        ask([{'role': 'user', 'content': 'hi'}], max_tokens=8)
    except Exception as exc:
        print('warmup failed:', exc)

    results = []
    for path, scale in cands:
        post('/v1/control-vector', {'path': path, 'scale': scale})
        entry = {'file': os.path.basename(path), 'scale': scale, 'answers': {}}
        for i, msgs in enumerate(VOICE):
            text = ask([msgs])
            entry['answers']['voice%d' % i] = text
            print('%-26s scale=%-6s voice%d  %s' % (entry['file'], scale, i,
                  text.replace(chr(10), ' ')[:190]), flush=True)
        for key, msgs in (('math', MATH), ('know', KNOW)):
            text = ask(msgs)
            entry['answers'][key] = text
            print('%-26s scale=%-6s %-6s %s' % (entry['file'], scale, key,
                  text.replace(chr(10), ' ')[:190]), flush=True)
        results.append(entry)

    out = os.path.join(DATA, 'style_probe.json')
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    print('saved', out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
