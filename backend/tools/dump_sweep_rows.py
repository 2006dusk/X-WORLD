import json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SWEEP = ROOT.parent / 'docs' / '实践情况' / 'data' / 'sweep' / 'sweep.json'


def main() -> int:
    prefix = sys.argv[1] if len(sys.argv) > 1 else ''
    entries = json.loads(SWEEP.read_text(encoding='utf-8'))
    for e in entries:
        if prefix and not str(e.get('name') or '').startswith(prefix):
            continue
        short = e.get('short') or {}
        long = e.get('long') or {}
        print('==', e.get('name'), e.get('params'), e.get('quant'), 'size_mb', e.get('size_mb'))
        print('   decode short', short.get('decode_tok_per_s'), 'long', long.get('decode_tok_per_s'),
              '| prefill long', long.get('prefill_tok_per_s'),
              '| ttft', (e.get('ttft') or {}).get('ttft_s'))
        for k in ('p1', 'p2', 'p3'):
            m = (e.get('sample_' + k) or {}).get('metrics') or {}
            print(f'   {k}: chars={m.get("chars")} band={m.get("in_length_band")} '
                  f'complete={m.get("complete_turn")} ends_clean={m.get("ends_clean")} '
                  f'dlg={m.get("dialogue_count")} cjk={m.get("cjk_dialogue_count")} '
                  f'ascii={m.get("ascii_dialogue_count")} rep={m.get("repeat_rate")} '
                  f'wall={m.get("wall_s")} garbled={m.get("garbled_chars")}')
        print('   P3 TEXT:', repr((e.get('sample_p3') or {}).get('content', ''))[:1200])
        print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
