import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
MT = ROOT.parent / 'docs' / '实践情况' / 'data' / 'sweep' / 'multi_turn.json'

data = json.loads(MT.read_text(encoding='utf-8'))
print('type:', type(data).__name__)
if isinstance(data, dict):
    print('keys:', list(data.keys()))
    for k, v in data.items():
        print('---', k, type(v).__name__)
        print(json.dumps(v, ensure_ascii=False)[:1400])
        break
else:
    print(json.dumps(data[0], ensure_ascii=False)[:1800])
