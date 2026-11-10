"""把 7.12 节插入报告的第 8 节之前，并更新相关小节。

用法：
    python tools\insert_section_712.py
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPORT = ROOT.parent / 'docs' / '实践情况' / '质量与速度平衡点.md'
DRAFT = REPORT.with_suffix('.md.tmp_712')
MARKER = '## 8. 数据来源与数据边界'


def main() -> int:
    draft = DRAFT.read_text(encoding='utf-8')
    text = REPORT.read_text(encoding='utf-8')
    if '### 7.12' in text:
        print('7.12 节已存在，跳过插入')
        return 0
    idx = text.index(MARKER)
    merged = text[:idx] + draft + text[idx:]
    REPORT.write_text(merged, encoding='utf-8')
    DRAFT.unlink()
    print(f'已插入 7.12 节，报告现 {len(merged)} 字符')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
