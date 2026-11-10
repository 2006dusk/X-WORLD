import json
import urllib.request

from modelscope.hub.api import HubApi

REPOS = [
    'QuantFactory/Peach-2.0-9B-8k-Roleplay-GGUF',
    'acgurl/Muice-Qwen3-14B-gguf',
    'QuantFactory/Qwen2-7B-BD-RP-GGUF',
    'ConicCat/role-mo-V5-32B-gguf',
    'Sukina-Lab_E.C.H.O/Fapseek',
    'bartowski/RolePlayLake-7B-GGUF',
]

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) xworld-research'}


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except Exception as exc:
        return str(exc).encode()


if __name__ == '__main__':
    api = HubApi()
    for repo in REPOS:
        print('=====', repo)
        try:
            files = api.get_model_files(repo, revision='master', recursive=True)
        except Exception as exc:
            print('  list failed:', str(exc)[:160])
            files = []
        ggufs = []
        for f in files or []:
            name = getattr(f, 'Path', None) or getattr(f, 'Name', None)
            size = getattr(f, 'Size', None)
            if not name:
                continue
            if name.lower().endswith('.gguf'):
                ggufs.append((name, size))
        ggufs.sort(key=lambda x: (x[1] or 0))
        for name, size in ggufs[:14]:
            print(f'   {name} | {(size or 0) / 1048576:.0f} MB')
        if not ggufs:
            print('   (no gguf)')
        card = fetch(f'https://www.modelscope.cn/models/{repo}/resolve/master/README.md')
        text = card.decode('utf-8', 'replace')
        head = [line for line in text.splitlines()[:40] if line.strip()][:14]
        for line in head:
            print('   |', line[:150])
