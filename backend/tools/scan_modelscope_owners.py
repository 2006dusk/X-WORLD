import json
import urllib.request

from modelscope.hub.api import HubApi

OWNERS = [
    'acgurl',
    'ConicCat',
    'Sukina-Lab_E.C.H.O',
    'yuyouyu',
    'Moemuu',
    'LuoMoJinShu',
    'TouchNight',
    'ClosedCharacter',
]

KEYS = ('roleplay', 'role-play', 'rp', 'character', 'silly', 'story', 'novel', 'dialog', 'chat', 'sft', 'tuned')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) xworld-research'}


def files_of(repo):
    url = f'https://www.modelscope.cn/api/v1/models/{repo}/repo/files?Revision=master&Root='
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception:
        return []
    items = data.get('Data') if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = items.get('Files') or []
    out = []
    for item in items or []:
        path = item.get('Path') or item.get('Name') or ''
        size = int(item.get('Size') or 0)
        if path.lower().endswith('.gguf'):
            out.append((path, size))
    return out


if __name__ == '__main__':
    api = HubApi()
    for owner in OWNERS:
        try:
            payload = api.list_models(owner, page_number=1, page_size=100)
        except Exception as exc:
            print(f'== {owner}: list failed {str(exc)[:90]}')
            continue
        items = payload.get('Models') if isinstance(payload, dict) else payload
        names = []
        for item in items or []:
            if isinstance(item, dict):
                name = item.get('ModelId') or item.get('Name')
                dl = item.get('Downloads') or 0
            else:
                name, dl = str(item), 0
            if not name:
                continue
            low = name.lower()
            if any(k in low for k in KEYS):
                names.append((name, dl))
        print(f'== {owner}: {len(items or [])} 个仓库，命中 {len(names)}')
        for name, dl in names[:25]:
            ggufs = files_of(name)
            sizes = sorted((s for _, s in ggufs), reverse=True)[:1]
            q4 = [f'{p.split("/")[-1]} {s / 1048576:.0f}MB' for p, s in ggufs
                  if 'Q4_K_M' in p or 'Q4_0' in p or 'Q5_K_M' in p][:3]
            print(f'   {name} | dl={dl} | gguf {len(ggufs)} | {" ; ".join(q4)}')
