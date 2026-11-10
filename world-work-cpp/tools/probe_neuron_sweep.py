# Sweep single-neuron control vectors on the running server.
#
# The server exposes POST /v1/control-vector, so a candidate vector can be
# swapped without a restart. Each candidate is applied at several scales and
# probed with a persona prompt and two capability prompts.

import json
import os
import sys
import time
import urllib.request
# Repo root, derived from this file so the script carries no local path.
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))

PORT = int(os.environ.get('WWC_PORT', '8080'))
DATA = os.path.join(REPO, "..", 'docs', '理论支持', 'data', 'neuron')

PERSONA = [{'role': 'user', 'content': '\u671b\u541b\u662f\u8c01\uff1f'}]
MATH = [{'role': 'user', 'content': '18+7-9 \u7b49\u4e8e\u591a\u5c11\uff1f'}]
KNOW = [{'role': 'user', 'content': '\u7528\u4e00\u53e5\u8bdd\u8bf4\u660e\u5149\u5408\u4f5c\u7528\u3002'}]


def post(path, payload, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (PORT, path), data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


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


def ask(messages, max_tokens=110):
    body = {'messages': messages, 'max_tokens': max_tokens, 'temperature': 0.0, 'seed': 7}
    resp = post('/v1/chat/completions', body)
    return (resp.get('choices') or [{}])[0].get('message', {}).get('content', '')


def main():
    cands = []
    for arg in sys.argv[1:]:
        path, scales = arg.split('=')
        for s in scales.split(','):
            cands.append((path, float(s)))
    if not wait_ready():
        print('server not ready')
        return 1

    results = []
    for path, scale in cands:
        post('/v1/control-vector', {'path': path, 'scale': scale})
        entry = {'file': os.path.basename(path), 'scale': scale, 'answers': {}}
        for key, msgs in (('persona', PERSONA), ('math', MATH), ('know', KNOW)):
            text = ask(msgs)
            entry['answers'][key] = text
            print('%-24s scale=%-7s %-8s %s' % (entry['file'], scale, key,
                  text.replace(chr(10), ' ')[:160]), flush=True)
        results.append(entry)

    out = os.path.join(DATA, 'neuron_sweep.json')
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    print('saved', out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
