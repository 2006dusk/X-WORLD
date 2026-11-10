import json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
JUDGE = ROOT.parent / 'docs' / '实践情况' / 'data' / 'sweep' / 'judge.json'
KEYS = ('character', 'narrative', 'chinese', 'format', 'clean', 'pov')


def main() -> int:
    data = json.loads(JUDGE.read_text(encoding='utf-8'))
    print('type:', type(data).__name__)
    if isinstance(data, dict):
        print('keys:', list(data.keys())[:20])
        scores = data.get('scores', {})
        rows = scores.items() if isinstance(scores, dict) else []
        for name, rec in rows:
            if not isinstance(rec, dict):
                print(name, rec)
                continue
            if 'skipped' in rec:
                print(name, '| skipped:', rec['skipped'])
                continue
            if 'error' in rec:
                print(name, '| error:', rec['error'])
                continue
            total = sum(float(rec.get(k) or 0) for k in KEYS)
            print(f"{name} | raw={total:.1f}/60 -> {total / 60 * 100:.1f} | {rec.get('reason', '')}")
    else:
        print(json.dumps(data, ensure_ascii=False)[:1500])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
