"""打印多种子方差结果：每档的裁判分分布、维度分布与逐条样例。

用法：
    python tools\dump_variance_rows.py [配置名子串 ...]
"""

import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
VAR = ROOT.parent / 'docs' / '实践情况' / 'data' / 'sweep' / 'variance.json'
DIMS = ("character", "narrative", "chinese", "format", "clean", "pov")


def main() -> int:
    prefix = sys.argv[1] if len(sys.argv) > 1 else ''
    rows = json.loads(VAR.read_text(encoding='utf-8'))
    for row in rows:
        name = str(row.get('name'))
        if prefix and prefix not in name:
            continue
        samples = row.get('samples') or []
        print('==', name, row.get('params'), row.get('quant'))
        print('   summary', json.dumps(row.get('summary'), ensure_ascii=False))
        totals = []
        for dim in DIMS:
            vals = [float((s.get('judge') or {}).get(dim) or 0) for s in samples if not (s.get('judge') or {}).get('error')]
            if vals:
                print(f'   {dim:10s} mean {statistics.fmean(vals):.2f} min {min(vals)} max {max(vals)}')
        for s in samples:
            j = s.get('judge') or {}
            total = sum(float(j.get(k) or 0) for k in DIMS)
            totals.append(total)
            m = s.get('metrics') or {}
            print(f"   seed {s.get('seed'):>6} {m.get('chars'):>4}字 dlg{m.get('dialogue_count')} "
                  f"band={m.get('in_length_band')} total={total:.0f} reason={str(j.get('reason'))[:60]}")
        if totals:
            print(f'   TOTAL mean {statistics.fmean(totals):.2f} min {min(totals)} max {max(totals)}')
        print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
