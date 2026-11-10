r"""同步 RP 模型候选清单与模型清单里的裁判分口径。

7.12 节证明裁判分在顶部饱和、同分档无法区分、裁判噪声大于档位差距，
原先「与默认档同分」的表述要改成「差异落在裁判噪声内」。

用法：
    python tools\sync_candidate_docs.py
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
CAND = ROOT.parent / 'docs' / '实践情况' / '模型清单.md'
MODELS = ROOT.parent / 'docs' / '实践情况' / '模型清单.md'

CAND_OLD = ('| 轻量档候选 | Qwen3.5-2B-UD Q4_K_XL | 通用指令模型，本机已有权重；1278 MB，115.81 tok/s，'
            '单轮等待 1.52 s，裁判分与默认档同分 | 客户端链路复测三轮：十二个推进回合里两个低于 150 字下沿，'
            '出现过一次整轮逐字复述；非 RP 微调模型，依赖客户端写死回合要求；许可与基座需另行核实 |')
CAND_NEW = ('| 轻量档候选 | Qwen3.5-2B-UD Q4_K_XL | 通用指令模型，本机已有权重；1278 MB，115.81 tok/s，'
            '单轮等待 1.52 s，裁判分与默认档的差异落在裁判噪声内 | 客户端链路复测三轮：十二个推进回合里两个低于 150 字下沿，'
            '出现过一次整轮逐字复述；非 RP 微调模型，依赖客户端写死回合要求；许可与基座需另行核实 |')

MODELS_OLD = ('再做一轮同族规模对照与新增候选实测。同族对照取 Qwen3.5-UD 的 Q4_K_XL 2B、4B、9B 三个本机已有权重，'
               '同族同量化档只改规模：2B 判 57 分、9B 判 57 分，与默认档同分，9B 的回合偏长（311 字）且慢 37%，'
               '2B 快 84% 且四轮稳定 182 到 240 字。结论是参数量不是质量的单调变量，2B 进轻量档候选，'
               '4B 那一档兼作本地裁判模型，其自评结果不采信。2B 另做了客户端链路复测，三轮四轮推进的十二个回合里两个低于 150 字下沿，'
               '并出现过一次整轮逐字复述，因此只作轻量档候选，不替换默认档。')
MODELS_NEW = ('再做一轮同族规模对照与新增候选实测。同族对照取 Qwen3.5-UD 的 Q4_K_XL 2B、4B、9B 三个本机已有权重，'
               '同族同量化档只改规模：2B、4B、9B 三档的单次裁判分都是 57 分，9B 的回合偏长（311 字）且慢 37%，'
               '2B 快 84%。后续复核推翻了「同分即同质」的读法：裁判分在顶部饱和，'
               '七个档位拿到同一个 57 分与同一组维度值，只换裁判 seed 的分差就有 10 到 13 分、极差 27 分，'
               '大于档位之间的差距；同族偏好经交叉验证已排除，三档均值 55.0、55.0、54.9 与非同族的 ArityFlow 一致。'
               '2B 的真实长处是指令遵循效率高，追加「中文引号对白」要求后对白率由 0.667 升到 1.0。'
               '结论仍是参数量不是质量的单调变量，2B 进轻量档候选，4B 那一档兼作本地裁判模型，其自评结果不采信。'
               '2B 另做了客户端链路复测，三轮四轮推进的十二个回合里两个低于 150 字下沿，'
               '并出现过一次整轮逐字复述，因此只作轻量档候选，不替换默认档。详见质量与速度平衡点报告 7.12 节。')


def patch(path: pathlib.Path, old: str, new: str, label: str) -> bool:
    text = path.read_text(encoding='utf-8')
    if old not in text:
        print(f'未找到：{label}')
        return False
    path.write_text(text.replace(old, new, 1), encoding='utf-8')
    print(f'已更新：{label}')
    return True


def main() -> int:
    ok = True
    ok = patch(CAND, CAND_OLD, CAND_NEW, '候选清单轻量档行') and ok
    ok = patch(MODELS, MODELS_OLD, MODELS_NEW, '模型清单同族对照段') and ok
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
