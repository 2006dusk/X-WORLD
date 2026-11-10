import json
import urllib.parse
import urllib.request

from modelscope.hub.api import HubApi

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) xworld-research'}


def kaggle_search(term, page=1):
    url = 'https://www.kaggle.com/api/v1/models/list?search=' + urllib.parse.quote(term) + f'&page={page}'
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        return [], str(exc)[:140]
    rows = []
    for item in data.get('models', []):
        rows.append({
            'ref': item.get('ref'),
            'votes': item.get('voteCountNullable'),
            'downloads': item.get('downloadCountNullable'),
            'size': item.get('totalBytesNullable'),
            'license': item.get('licenseName'),
            'updated': (item.get('lastUpdatedNullable') or '')[:10],
        })
    return rows, None


def ms_owner_models(owner, page_size=100):
    api = HubApi()
    try:
        payload = api.list_models(owner, page_number=1, page_size=page_size)
    except Exception as exc:
        return [], str(exc)[:200]
    rows = []
    if isinstance(payload, dict):
        items = payload.get('Models') or payload.get('models') or []
    else:
        items = payload or []
    for item in items:
        if isinstance(item, dict):
            rows.append({
                'id': item.get('ModelId') or item.get('model_id') or item.get('Name'),
                'name': item.get('Name'),
                'downloads': item.get('Downloads'),
                'likes': item.get('Likes'),
                'framework': item.get('Framework'),
            })
        else:
            rows.append({'id': str(item), 'name': None, 'downloads': None, 'likes': None, 'framework': None})
    return rows, None


if __name__ == '__main__':
    print('===== MODELSCOPE ORG LIST =====')
    for owner in ['AI-ModelScope', 'LuoMoJinShu', 'iic']:
        rows, err = ms_owner_models(owner)
        print(f'-- {owner}: {len(rows)} {err or ""}')
        for row in rows[:8]:
            print(f"   {row['id']} | dl={row['downloads']} likes={row['likes']} | {row['framework']}")
    print()
    print('===== KAGGLE =====')
    for term in ['roleplay', 'sillytavern', 'roleplay gguf', 'rp chat model', 'roleplay model llama']:
        rows, err = kaggle_search(term)
        print(f'-- {term}: {len(rows)} {err or ""}')
        for row in rows[:15]:
            print(f"   {row['ref']} | votes={row['votes']} dl={row['downloads']} | size={row['size']} | {row['license']} | {row['updated']}")
