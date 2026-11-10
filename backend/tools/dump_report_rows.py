import json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_sweep_report import deterministic_score, judge_score  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT.parent / 'docs' / '实践情况' / 'data' / 'sweep'

LABELS = {
    'qwen3_5-2b_ud': 'Qwen3.5-2B-UD',
    'qwen3_5-4b_ud': 'Qwen3.5-4B-UD',
    'qwen3_5-9b_ud': 'Qwen3.5-9B-UD',
    'rpg-roleplay-v2-4b_q5_km': 'Qwen3-4B-RPG-Roleplay-V2',
    'nymphaea-4b_q5_km': 'Qwen3-4B-Nymphaea-RP',
    'l3-stheno-8b_q4_km': 'L3-8B-Stheno-v3.2',
    'collective-8b_q4_km': 'collective-v0.1-chinese-roleplay-8b',
    'darkidol-8b_q4_km': 'DarkIdol-Llama-3.1-8B-Instruct-1.2',
    'bdrp-7b_q4_km': 'Qwen2-7B-BD-RP',
    'peach2-9b_q4_km': 'Peach-2.0-9B-8k-Roleplay',
    'rolemo-v3-7b_q6_k': 'role-mo-v3-7B',
}


def yn(flag):
    return '是' if flag else '否'


def main() -> int:
    entries = json.loads((DATA / 'sweep.json').read_text(encoding='utf-8'))
    payload = json.loads((DATA / 'judge.json').read_text(encoding='utf-8'))
    judge = payload.get('scores') or {}
    for entry in entries:
        name = entry.get('name') or ''
        if not any(k in name for k in LABELS):
            continue
        p1 = entry['sample_p1']['metrics']
        p2 = entry['sample_p2']['metrics']
        p3 = entry['sample_p3']['metrics']
        jscore, reason = judge_score(entry, judge)
        label = LABELS.get(name, name)
        size = entry.get('size_mb')
        decode = entry.get('short', {}).get('decode_tok_per_s')
        prefill = entry.get('long', {}).get('prefill_tok_per_s')
        ttft = entry.get('ttft', {}).get('ttft_s')
        print(f'### {label} | {entry.get("params")} | {entry.get("quant")}')
        print(f'| {label} | {entry.get("params")} | {entry.get("quant")} | {size} MB | {decode} | {prefill} | {ttft} |')
        det = deterministic_score(p3)
        print(f'| {label} | {entry.get("quant")} | {p1.get("chars")} | {yn(p1.get("complete_turn"))} | '
              f'{p2.get("chars")} | {yn(p2.get("complete_turn"))} | {p3.get("chars")} | '
              f'{yn(p3.get("in_length_band"))} | {yn(p3.get("complete_turn"))} | {p3.get("dialogue_count")} | '
              f'{p3.get("wall_s")} | {det} | {jscore if jscore is not None else "无"} |')
        print(f'理由: {reason}')
        print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
