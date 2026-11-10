"""把 sweep.json 的 P3 样例原文导出为 UTF-8 文本，便于人工阅读。

用法：
    python tools\dump_samples_text.py <输出文件> [配置名子串 ...]
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SWEEP = ROOT.parent / 'docs' / '实践情况' / 'data' / 'sweep' / 'sweep.json'


def main() -> int:
    out_path = pathlib.Path(sys.argv[1])
    wanted = [a.lower() for a in sys.argv[2:]]
    entries = json.loads(SWEEP.read_text(encoding='utf-8'))
    lines = []
    for entry in entries:
        name = str(entry.get('name'))
        if wanted and not any(w in name.lower() for w in wanted):
            continue
        sample = entry.get('sample_p3') or {}
        m = sample.get('metrics') or {}
        lines.append('=' * 70)
        lines.append(f"# {name} | {entry.get('params')} {entry.get('quant')} | chars={m.get('chars')} dlg={m.get('dialogue_count')} band={m.get('in_length_band')}")
        lines.append(str(sample.get('content') or ''))
        lines.append('')
    out_path.write_text('\n'.join(lines), encoding='utf-8')
    print(f'wrote {out_path} ({len(lines)} lines)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
