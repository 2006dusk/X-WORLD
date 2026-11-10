"""三提示档对照：P1 默认行为、P2 强制长回合、P3 目标长度回合。

P1 不给格式要求，P2/P3 给。若某模型只在带格式要求时才对白齐全，
说明它的分数来自指令跟随，不是角色扮演语料。

用法：
    python tools\analyze_prompt_dependence.py
"""

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SWEEP = ROOT.parent / 'docs' / '实践情况' / 'data' / 'sweep' / 'sweep.json'


def main() -> int:
    entries = json.loads(SWEEP.read_text(encoding='utf-8'))
    rows = []
    for entry in entries:
        if 'error' in entry:
            continue
        row = {'name': entry.get('name'), 'params': entry.get('params'), 'quant': entry.get('quant')}
        for key in ('p1', 'p2', 'p3'):
            m = (entry.get('sample_' + key) or {}).get('metrics') or {}
            row[key] = (m.get('chars'), m.get('dialogue_count'), m.get('complete_turn'), m.get('in_length_band'))
        rows.append(row)
    rows.sort(key=lambda r: str(r['name']))
    print(f"{'配置':34s} {'P1 字/对白/完整':>18s} {'P2 字/对白/完整':>18s} {'P3 字/对白/完整':>18s}")
    for r in rows:
        def fmt(k):
            c, d, comp, band = r[k]
            return f"{c}/{d}/{'Y' if comp else 'N'}"
        print(f"{str(r['name']):34s} {fmt('p1'):>18s} {fmt('p2'):>18s} {fmt('p3'):>18s}")
    print()
    p1_ok = [r for r in rows if r['p1'][1] and r['p1'][1] >= 1]
    p3_ok = [r for r in rows if r['p3'][1] and r['p3'][1] >= 1]
    print(f"P1 有对白 {len(p1_ok)}/{len(rows)} 档；P3 有对白 {len(p3_ok)}/{len(rows)} 档")
    gained = [r for r in rows if (not r['p1'][1] or r['p1'][1] < 1) and r['p3'][1] >= 1]
    print(f"P1 无对白但 P3 有对白 {len(gained)} 档：{[r['name'] for r in gained]}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
