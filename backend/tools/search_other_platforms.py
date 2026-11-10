import json
import re
import urllib.parse
import urllib.request
import urllib.error

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) xworld-research'}


def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b''
    except Exception as exc:
        return 'ERR', str(exc).encode()


def kaggle_search(term, page_size=30):
    url = ('https://www.kaggle.com/api/v1/models/list?search=' + urllib.parse.quote(term)
           + f'&pageSize={page_size}&sortBy=voteCount&group=everyone')
    status, body = get(url)
    if status != 200:
        return status, []
    try:
        data = json.loads(body)
    except ValueError:
        return status, []
    rows = []
    for item in data.get('models', []):
        rows.append({
            'ref': item.get('ref'),
            'votes': item.get('voteCountNullable'),
            'downloads': item.get('downloadCountNullable'),
            'size': item.get('totalBytesNullable'),
            'license': (item.get('licenseName') or ''),
        })
    return status, rows


def ollama_search(term):
    status, body = get('https://ollama.com/search?q=' + urllib.parse.quote(term))
    if status != 200:
        return status, []
    text = body.decode('utf-8', 'replace')
    names = []
    for m in re.finditer(r'href="/library/([a-z0-9._-]+)"', text):
        if m.group(1) not in names:
            names.append(m.group(1))
    return status, names


def github_repos(query, per_page=25):
    url = ('https://api.github.com/search/repositories?q=' + urllib.parse.quote(query)
           + f'&per_page={per_page}&sort=stars')
    status, body = get(url)
    if status != 200:
        return status, []
    try:
        data = json.loads(body)
    except ValueError:
        return status, []
    return status, [{'full_name': it.get('full_name'), 'stars': it.get('stargazers_count'),
                     'desc': (it.get('description') or '')[:110]} for it in data.get('items', [])]


if __name__ == '__main__':
    print('===== KAGGLE =====')
    for term in ['roleplay', 'sillytavern', 'role play model', 'rp llama']:
        status, rows = kaggle_search(term)
        print(f'-- {term} ({status}) {len(rows)}')
        for row in rows[:12]:
            print(f"   {row['ref']} | votes={row['votes']} dl={row['downloads']} | size={row['size']} | {row['license']}")
    print()
    print('===== OLLAMA =====')
    for term in ['roleplay', 'rp', 'role play']:
        status, names = ollama_search(term)
        print(f'-- {term} ({status}) {len(names)}: {names[:30]}')
    print()
    print('===== GITHUB =====')
    for query in ['roleplay gguf', 'sillytavern model gguf', 'roleplay model release gguf']:
        status, rows = github_repos(query)
        print(f'-- {query} ({status}) {len(rows)}')
        for row in rows[:15]:
            print(f"   {row['full_name']} | stars={row['stars']} | {row['desc']}")
