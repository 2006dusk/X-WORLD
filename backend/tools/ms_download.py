import argparse
import json
import pathlib
import subprocess
import time
import urllib.request

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) xworld-research'}


def expected_size(repo: str, filename: str) -> int:
    url = f'https://www.modelscope.cn/api/v1/models/{repo}/repo/files?Revision=master&Root='
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as resp:
        data = json.loads(resp.read())
    items = data.get('Data') if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = items.get('Files') or []
    for item in items or []:
        path = item.get('Path') or item.get('Name') or ''
        if path.split('/')[-1] == filename:
            return int(item.get('Size') or 0)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('repo')
    ap.add_argument('filename')
    ap.add_argument('dest')
    ap.add_argument('--attempts', type=int, default=40)
    args = ap.parse_args()

    total = expected_size(args.repo, args.filename)
    if not total:
        print(f'未找到文件 {args.filename}', flush=True)
        return 1
    url = f'https://www.modelscope.cn/models/{args.repo}/resolve/master/{args.filename}'
    print(f'目标 {args.filename} | {total / 1048576:.1f} MB', flush=True)

    for attempt in range(1, args.attempts + 1):
        try:
            cur = pathlib.Path(args.dest).stat().st_size
        except OSError:
            cur = 0
        if cur >= total:
            print(f'DONE {cur} 字节', flush=True)
            return 0
        cmd = ['curl.exe', '-L', '--http1.1', '--max-time', '300', '--retry', '3',
               '--retry-delay', '3', '-C', '-', '-o', args.dest, url]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            after = pathlib.Path(args.dest).stat().st_size
        except OSError:
            after = 0
        print(f'第 {attempt} 次尝试 | {after / 1048576:.1f}/{total / 1048576:.1f} MB', flush=True)
        if after == cur:
            time.sleep(5)
    print('未完成', flush=True)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
