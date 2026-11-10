import json
import sys
import urllib.request

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) xworld-research'}


def raw(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except Exception as exc:
        return 'ERR', str(exc).encode()


if __name__ == '__main__':
    repo = sys.argv[1]
    for url in [
        f'https://www.modelscope.cn/api/v1/models/{repo}/repo/files?Revision=master&Root=',
        f'https://modelscope.cn/api/v1/models/{repo}/repo/files?Revision=master&Root=&Recursive=True',
        f'https://www.modelscope.cn/api/v1/models/{repo}/repo/tree?Revision=master',
    ]:
        status, body = raw(url)
        print('---', status, url[:110], len(body))
        if status != 200:
            print('   ', body[:200])
            continue
        try:
            data = json.loads(body)
        except ValueError:
            print('   not json:', body[:200])
            continue
        items = data.get('Data') if isinstance(data, dict) else data
        if isinstance(items, dict):
            items = items.get('Files') or items.get('files') or []
        count = 0
        for it in items or []:
            path = it.get('Path') or it.get('Name') or ''
            size = it.get('Size') or 0
            if path.lower().endswith('.gguf'):
                print(f'   {path} | {size / 1048576:.0f} MB')
                count += 1
        print('   gguf count:', count, 'total items:', len(items or []))
