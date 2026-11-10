"""分析裁判分与确定性闸门的关系：高分档到底是被什么撑起来的。

对每个有 P3 样例的配置，计算三个闸门是否通过：
- 格式闸门：引号对白 >= 1 段
- 字数闸门：100 <= 字数 <= 600
- 重复闸门：重复率 <= 0.08

再看裁判分是否基本等于闸门通过后的固定模板分。

用法：
    python tools\analyze_gates.py
"""

import json
import pathlib
import statistics

ROOT = pathlib.Path(__file__).resolve().parents[2]
SWEEP = ROOT.parent / 'docs' / '实践情况' / 'data' / 'sweep' / 'sweep.json'
JUDGE = ROOT.parent / 'docs' / '实践情况' / 'data' / 'sweep' / 'judge.json'
DIMS = ('character', 'narrative', 'chinese', 'format', 'clean', 'pov')


def main() -> int:
    entries = {str(e.get('name')): e for e in json.loads(SWEEP.read_text(encoding='utf-8'))}
    scores = (json.loads(JUDGE.read_text(encoding='utf-8')).get('scores') or {})
    rows = []
    for name, item in scores.items():
        if not isinstance(item, dict) or 'error' in item or 'skipped' in item:
            continue
        entry = entries.get(name) or {}
        sample = entry.get('sample_p3') or {}
        m = sample.get('metrics') or {}
        total = sum(float(item.get(k) or 0) for k in DIMS)
        gates = {
            'format': int(m.get('dialogue_count') or 0) >= 1,
            'length': 100 <= int(m.get('chars') or 0) <= 600,
            'clean': float(m.get('repeat_rate') or 0) <= 0.08,
        }
        rows.append({
            'name': name, 'total': total, 'gates': gates,
            'all_pass': all(gates.values()),
            'chars': m.get('chars'), 'dlg': m.get('dialogue_count'),
            'pov': float(item.get('pov') or 0),
            'dims': {k: float(item.get(k) or 0) for k in DIMS},
        })
    rows.sort(key=lambda r: -r['total'])
    print('== 闸门与裁判分对照 ==')
    for r in rows:
        flags = ' '.join(f"{k}:{'Y' if v else 'N'}" for k, v in r['gates'].items())
        print(f"{r['total']:5.0f}  {r['name']:34s} {flags}  全过={r['all_pass']}  pov={r['pov']:g}")
    passed = [r for r in rows if r['all_pass']]
    failed = [r for r in rows if not r['all_pass']]
    print()
    if passed:
        totals = [r['total'] for r in passed]
        print(f"三闸全过 {len(passed)} 档，裁判分 {min(totals):.0f}-{max(totals):.0f}，"
              f"中位 {statistics.median(totals):.0f}，标准差 {statistics.pstdev(totals):.2f}")
        povs = [r['pov'] for r in passed]
        print(f"  其中 pov 取值集合 {sorted(set(povs))}；pov=10 档数 {sum(1 for p in povs if p == 10)}")
        for key in DIMS:
            vals = sorted({r['dims'][key] for r in passed})
            print(f"  {key:10s} 取值集合 {vals}")
    if failed:
        totals = [r['total'] for r in failed]
        print(f"未全过 {len(failed)} 档，裁判分 {min(totals):.0f}-{max(totals):.0f}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
