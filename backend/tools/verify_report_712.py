"""核对报告 7.12 节与第 8 节、第 10 节的落地结果。

用法：
    python tools\verify_report_712.py
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPORT = ROOT.parent / 'docs' / '实践情况' / '质量与速度平衡点.md'


def main() -> int:
    text = REPORT.read_text(encoding='utf-8')
    checks = [
        ('7.12 节存在', '### 7.12 为什么 2B 看起来这么强' in text),
        ('闸门分析入节', '三道闸门全过' in text),
        ('裁判噪声入节', '裁判 seed 间分差均值' in text),
        ('异族裁判结论入节', '异族裁判换不了' in text),
        ('指令遵循表入节', 'q3 | 对白加字数' in text),
        ('引号归一结论入节', 'ends_clean 判定失败' in text),
        ('第 8 节写明噪声', '单次打分不足以支撑排名' in text),
        ('第 8 节写明同族偏好已排除', '未见同族偏袒' in text),
        ('第 8 节写明饱和', '裁判量表在顶部饱和' in text),
        ('第 10 节新增质量轴条目', '质量轴要重做' in text),
        ('第 10 节新增判据条目', '回合完整判据要修 ends_clean' in text),
        ('临时文件已清理', not REPORT.with_suffix('.md.tmp_712').exists()),
    ]
    bad = 0
    for name, ok in checks:
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
        if not ok:
            bad += 1
    print()
    outline = [(i + 1, line) for i, line in enumerate(text.splitlines()) if line.startswith('### 7.1')]
    for no, line in outline:
        print(f'  {no}: {line}')
    print()
    print('7.12 节字数', len(re.search(r'### 7\.12.*?(?=\n## 8\.)', text, re.S).group(0)))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
