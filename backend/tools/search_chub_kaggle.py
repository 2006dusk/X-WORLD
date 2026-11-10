import json
import urllib.error
import urllib.parse
import urllib.request

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) xworld-research'}


def get(url, timeout=30, headers=None):
    req = urllib.request.Request(url, headers=headers or UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b''
    except Exception as exc:
        return 'ERR', str(exc)[:140].encode()


def kaggle(term):
    url = 'https://www.kaggle.com/api/v1/models/list?search=' + urllib.parse.quote(term)
    status, body = get(url)
    if status != 200:
        return status, []
    data = json.loads(body)
    rows = []
    for item in data.get('models', []):
        rows.append({
            'ref': item.get('ref'),
            'votes': item.get('voteCountNullable'),
            'downloads': item.get('downloadCountNullable'),
            'size': item.get('totalBytesNullable'),
            'license': item.get('licenseName'),
        })
    return status, rows


def chub(term):
    urls = [
        'https://chub.ai/api/models?search=' + urllib.parse.quote(term) + '&first=30',
        'https://api.chub.ai/models?search=' + urllib.parse.quote(term),
        'https://chub.ai/api/search/models?search=' + urllib.parse.quote(term),
        'https://chub.ai/api/models/search?search=' + urllib.parse.quote(term),
    ]
    out = []
    for url in urls:
        status, body = get(url)
        out.append((status, url, body[:200]))
    return out


def github_release_assets(repo):
    status, body = get(f'https://api.github.com/repos/{repo}/releases?per_page=5')
    if status != 200:
        return status, []
    rows = []
    for rel in json.loads(body):
        for asset in rel.get('assets', []):
            rows.append({'tag': rel.get('tag_name'), 'name': asset.get('name'),
                         'size_mb': round((asset.get('size') or 0) / 1048576, 1),
                         'url': asset.get('browser_download_url')})
    return status, rows


if __name__ == '__main__':
    print('===== CHUB =====')
    for status, url, body in chub('roleplay'):
        print(f'{status} {url[:100]}')
        if status == 200:
            print('   ', body[:200])
    print()
    print('===== KAGGLE =====')
    for term in ['roleplay', 'roleplay gguf', 'sillytavern', '角色扮演', 'rp model']:
        status, rows = kaggle(term)
        print(f'-- {term}: {status} {len(rows)}')
        for row in rows[:14]:
            print(f"   {row['ref']} | votes={row['votes']} dl={row['downloads']} | {row['size']} | {row['license']}")
    print()
    print('===== GITHUB RELEASES =====')
    for repo in ['Metrix187/wag', 'lukeckprobierts/Hemmingway-1', 'syfr512/SweetrollLM']:
        status, rows = github_release_assets(repo)
        print(f'-- {repo}: {status} {len(rows)} assets')
        for row in rows[:10]:
            print(f"   {row['tag']} | {row['name']} | {row['size_mb']} MB")
