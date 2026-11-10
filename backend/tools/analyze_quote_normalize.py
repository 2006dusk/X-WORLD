"""引号归一后重算回合完整率。

问题定位：complete_turn 的 ends_clean 判定只看中文句末标点，
而 ArityFlow 用 ASCII 直引号收尾（"...他说。"），被判成结尾非句末标点。
客户端侧本来就有引号归一逻辑（backend/app/inference/normalize.py），
这里对同一批样例做同样的归一，看判定结果如何变化。

用法：
    python tools\analyze_quote_normalize.py
"""

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
IF = ROOT.parent / 'docs' / '实践情况' / 'data' / 'sweep' / 'instruction_following.json'
SWEEP = ROOT.parent / 'docs' / '实践情况' / 'data' / 'sweep' / 'sweep.json'

ASCII_DQ = re.compile(r'"([^"\n]{1,400})"')
CJK_END = '。！？!?…”』」）'


def normalize(text: str) -> str:
    """ASCII 直引号转中文引号，并把结尾的引号移到句末标点之后。"""
    out = []
    open_q = True
    for ch in text:
        if ch == '"':
            out.append('“' if open_q else '”')
            open_q = not open_q
        else:
            out.append(ch)
    return ''.join(out)


def ends_clean_fixed(text: str) -> bool:
    t = text.rstrip()
    if not t:
        return False
    if t[-1] in CJK_END:
        return True
    # 结尾是引号，看引号前一个字符是不是句末标点
    if t[-1] in '”』」':
        return len(t) > 1 and t[-2] in '。！？!?…'
    return False


def main() -> int:
    print('== 引号归一前后的回合完整率 ==')
    for label, path, key in (('指令实验', IF, 'variants'), ('扫描 P3', SWEEP, 'sample_p3')):
        data = json.loads(path.read_text(encoding='utf-8'))
        if key == 'variants':
            items = []
            for row in data:
                for vname, vdata in (row.get('variants') or {}).items():
                    for s in vdata.get('samples') or []:
                        items.append((f"{row.get('name')} {vname}", s.get('content') or ''))
        else:
            items = [(e.get('name'), (e.get('sample_p3') or {}).get('content') or '')
                     for e in data if e.get('sample_p3')]
        groups = {}
        for name, content in items:
            if not content:
                continue
            base = name.split()[0]
            norm = normalize(content)
            has_dlg = bool(re.search(r'[“「『][^”」』]*', norm) or ASCII_DQ.search(content))
            groups.setdefault(base, []).append((has_dlg, ends_clean_fixed(norm)))
        for base, vals in sorted(groups.items()):
            before = sum(1 for _, ec in vals if ec)
            print(f'  {label} {base:32s} 归一后结尾合格 {before}/{len(vals)}')
    print()
    print('== 关键样例：结尾形态 ==')
    for entry in json.loads(SWEEP.read_text(encoding='utf-8')):
        if entry.get('name') not in ('arityflow-4b_q5_km', 'qwen3_5-2b_ud', 'nqlsg-14b_q4_km'):
            continue
        content = (entry.get('sample_p3') or {}).get('content') or ''
        print(f"  {entry.get('name')}: 原尾 {repr(content[-8:])} -> 归一后 {repr(normalize(content)[-8:])} "
              f"-> 合格 {ends_clean_fixed(normalize(content))}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
