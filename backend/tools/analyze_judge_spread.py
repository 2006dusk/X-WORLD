"""分析裁判分的饱和度：高分档有多少、多少落在同一个分数上。

用法：
    python tools\analyze_judge_spread.py [judge.json 路径]
"""

import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT = ROOT.parent / 'docs' / '实践情况' / 'data' / 'sweep' / 'judge.json'
DIMS = ('character', 'narrative', 'chinese', 'format', 'clean', 'pov')


def main() -> int:
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    data = json.loads(path.read_text(encoding='utf-8'))
    scores = data.get('scores') or data
    rows = []
    for name, item in scores.items():
        if not isinstance(item, dict) or 'error' in item or 'skipped' in item:
            continue
        total = sum(float(item.get(k) or 0) for k in DIMS)
        rows.append((total, name, item))
    rows.sort(reverse=True)
    print('== 全档裁判总分排序 ==')
    for total, name, item in rows:
        dims = ' '.join(f'{k[:3]}{float(item.get(k) or 0):g}' for k in DIMS)
        print(f'{total:5.0f}  {name:34s} {dims}')
    print()
    hist = collections.Counter(int(total) for total, _, _ in rows)
    print('== 总分直方 ==')
    for key in sorted(hist, reverse=True):
        print(f'  {key:3d} 分: {hist[key]} 档')
    print()
    high = [r for r in rows if r[0] >= 50]
    print(f'== 高分档（>=50）共 {len(high)} 档，总分区间 {min(r[0] for r in high):.0f}-{max(r[0] for r in high):.0f} ==')
    per_dim = {k: sorted({float(r[2].get(k) or 0) for r in high}) for k in DIMS}
    for k in DIMS:
        print(f'  {k:10s} 高分档取值集合 {per_dim[k]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
