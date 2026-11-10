r"""同步报告第 8 节数据边界与第 10 节后续动作。

用法：
    python tools\update_report_boundaries.py
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPORT = ROOT.parent / 'docs' / '实践情况' / '质量与速度平衡点.md'

OLD_JUDGE = '| 裁判 | 本地裁判模型 Qwen3.5-4B-UD 打分，裁判模型自身那一档自评结果不采信；单次打分，采样噪声未量化 |'
NEW_JUDGE = ('| 裁判 | 本地裁判模型 Qwen3.5-4B-UD 打分，裁判模型自身那一档自评结果不采信；'
             '7.12 节已量化噪声：只换裁判 seed，同一段文本分差平均 10 到 13 分、极差最高 27 分，'
             '大于档位之间的差距，单次打分不足以支撑排名 |')

OLD_UNSEEDED = '| 未覆盖 | 成人向内容表现、不同温度与不同 seed 的方差 |'
NEW_UNSEEDED = ('| 未覆盖 | 成人向内容表现、不同温度的方差 |\n'
                '| 已覆盖 | 生成与裁判两侧的 seed 方差已量化，见 7.12 节 |')

OLD_FAMILY = '| 未覆盖 | 裁判模型与 Qwen3.5 三档同族，通用指令风格天然接近 RP 输出，该族两档的同分可能含同族偏好 |'
NEW_FAMILY = ('| 裁判同族偏好 | 已排除。7.12 节实测：同族的 2B、4B、9B 三档裁判分均值 55.0、55.0、54.9，'
              '与非同族的 ArityFlow 一致，未见同族偏袒 |\n'
              '| 未覆盖 | 裁判量表在顶部饱和，7 档拿同一个 57 分与同一组维度值，质量轴目前只能区分「过闸门」与「不过闸门」 |\n'
              '| 未覆盖 | 异族裁判路线不可用，7.12 节实测 RP 微调模型当评审会退化成续写 |')

OLD_NQM = '| 未覆盖 | 2B 的客户端链路复测只有三轮，每轮四个回合，样本量小；长度下沿与复述的频率尚未量化到可下结论的程度 |'
NEW_NQM = ('| 未覆盖 | 2B 的客户端链路复测只有三轮，每轮四个回合，样本量小；长度下沿与复述的频率尚未量化到可下结论的程度 |\n'
           '| 已覆盖 | 2B、默认档与 4B、9B 的生成侧方差各 8 个 seed，采样区间与目标带命中率见 7.12 节 |')

OLD_ITEM6 = ('6. Qwen3.5-2B-UD 进轻量档候选：体积 1278 MB、解码 115.81 tok/s、单轮等待 1.52 s，裁判分与默认档同分。'
             '客户端链路已复测三轮，见 7.8 节：十二个推进回合里两个低于 150 字下沿，并出现过一次整轮逐字复述。'
             '要挂为第二档，先把长度下沿放宽到 120 字或调高重复惩罚。')
NEW_ITEM6 = ('6. Qwen3.5-2B-UD 进轻量档候选：体积 1278 MB、解码 115.81 tok/s、单轮等待 1.52 s。'
             '7.12 节修正了「裁判分与默认档同分」这个说法：两者的裁判分差异落在裁判噪声内，'
             '2B 的真实长处是指令遵循效率高，客户端把回合要求写死在预设末尾正好命中它。'
             '客户端链路已复测三轮，见 7.8 节：十二个推进回合里两个低于 150 字下沿，并出现过一次整轮逐字复述。'
             '要挂为第二档，先把长度下沿放宽到 120 字或调高重复惩罚。')

OLD_ITEM11 = '10. 中文 RP 微调模型的主要缺陷是体裁而非规模：语料里多用括号动作流，不含引号对白。后续若要接这一类模型，先在预设里补齐对白样例，再谈换模型。'
NEW_ITEM11 = (OLD_ITEM11 + '\n'
              '11. 质量轴要重做，不能再用这一把尺子排档。7.12 节证明裁判分在顶部饱和、'
              '同分档位无法区分、裁判噪声大于档位差距。下一轮先把判据从「六项打分」改成'
              '「结构闸门加确定性指标加人工抽读」，把裁判降级为只做兜底标记。\n'
              '12. 回合完整判据要修 ends_clean。ArityFlow 用 ASCII 直引号收尾，被判成结尾非句末标点，'
              '完整率因此全是 0。按客户端的引号归一逻辑复算后是 12 / 12。'
              '扫描口径表里的「回合完整」定义要补上引号归一这一步，否则会持续误判 ASCII 引号模型。')


def main() -> int:
    text = REPORT.read_text(encoding='utf-8')
    pairs = [
        (OLD_JUDGE, NEW_JUDGE),
        (OLD_UNSEEDED, NEW_UNSEEDED),
        (OLD_FAMILY, NEW_FAMILY),
        (OLD_NQM, NEW_NQM),
        (OLD_ITEM6, NEW_ITEM6),
        (OLD_ITEM11, NEW_ITEM11),
    ]
    missing = []
    for old, new in pairs:
        if old not in text:
            missing.append(old[:40])
            continue
        text = text.replace(old, new, 1)
    REPORT.write_text(text, encoding='utf-8')
    if missing:
        print('未找到以下片段：')
        for m in missing:
            print('  -', m)
        return 1
    print('报告第 8 节与第 10 节已同步')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
