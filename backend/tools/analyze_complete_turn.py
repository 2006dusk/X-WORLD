"""拆解 complete_turn 的四个子条件，找出哪一条在拖后腿。

complete_turn = 有对白 且 有非对白描写句 且 以句末标点收尾 且 未被长度截断
"""

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
IF = ROOT.parent / 'docs' / '实践情况' / 'data' / 'sweep' / 'instruction_following.json'
SWEEP = ROOT.parent / 'docs' / '实践情况' / 'data' / 'sweep' / 'sweep.json'


def main() -> int:
    rows = json.loads(IF.read_text(encoding='utf-8'))
    print('== 指令遵循实验里的回合完整拆解 ==')
    for row in rows:
        print('==', row.get('name'))
        for vname, vdata in (row.get('variants') or {}).items():
            for s in vdata.get('samples') or []:
                content = s.get('content') or ''
                tail = repr(content[-12:]) if content else '(空)'
                reasons = []
                if not s.get('dialogue_count'):
                    reasons.append('无对白')
                if not s.get('narration_sentences'):
                    reasons.append('无非对白描写句')
                if not s.get('ends_clean'):
                    reasons.append('结尾非句末标点')
                if s.get('truncated'):
                    reasons.append('被长度截断')
                print(f"  {vname} seed{s.get('seed')}: chars={s.get('chars')} dlg={s.get('dialogue_count')}"
                      f"(cjk{s.get('cjk_dialogue_count')}/asc{s.get('ascii_dialogue_count')})"
                      f" narr={s.get('narration_sentences')} ends={s.get('ends_clean')}"
                      f" trunc={s.get('truncated')} fr={s.get('finish_reason')}"
                      f" -> {'完整' if s.get('complete_turn') else '不完整:' + ','.join(reasons)}")
                print(f"      尾: {tail}")
    print()
    print('== 扫描里的 P3 同样拆解（对照） ==')
    for entry in json.loads(SWEEP.read_text(encoding='utf-8')):
        if entry.get('name') not in ('arityflow-4b_q5_km', 'qwen3_5-2b_ud'):
            continue
        m = (entry.get('sample_p3') or {}).get('metrics') or {}
        content = (entry.get('sample_p3') or {}).get('content') or ''
        reasons = []
        if not m.get('dialogue_count'):
            reasons.append('无对白')
        if not m.get('narration_sentences'):
            reasons.append('无非对白描写句')
        if not m.get('ends_clean'):
            reasons.append('结尾非句末标点')
        if m.get('truncated'):
            reasons.append('被长度截断')
        print(f"  {entry.get('name')}: chars={m.get('chars')} dlg={m.get('dialogue_count')}"
              f" narr={m.get('narration_sentences')} ends={m.get('ends_clean')}"
              f" trunc={m.get('truncated')} -> {'完整' if m.get('complete_turn') else '不完整:' + ','.join(reasons)}")
        print(f"      尾: {repr(content[-12:])}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
