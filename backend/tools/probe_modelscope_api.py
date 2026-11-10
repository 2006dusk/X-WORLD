import json
import urllib.error
import urllib.parse
import urllib.request

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) xworld-research', 'Content-Type': 'application/json'}

GETS = [
    'https://modelscope.cn/api/v1/dolphin/models?PageSize=10&PageNumber=1&Name=roleplay',
    'https://modelscope.cn/api/v1/models?PageSize=10&PageNumber=1&SortBy=Default&Target=&Name=roleplay&ViewType=model',
    'https://modelscope.cn/api/v1/models?PageSize=10&PageNumber=1&Task=text-generation&Name=roleplay',
    'https://modelscope.cn/api/v1/search?keyword=roleplay&pageSize=10',
    'https://modelscope.cn/api/v1/models/search?keyword=roleplay&pageSize=10',
    'https://modelscope.cn/api/v1/dolphin/models?PageSize=10&PageNumber=1&Search=roleplay',
    'https://modelscope.cn/api/v1/models?PageSize=10&PageNumber=1&Name=roleplay&Target=llm',
    'https://www.modelscope.cn/api/v1/models?PageSize=10&PageNumber=1&Name=roleplay&SortBy=Default&Target=',
]

POSTS = [
    ('https://modelscope.cn/api/v1/dolphin/models', {'PageSize': 10, 'PageNumber': 1, 'Name': 'roleplay'}),
    ('https://modelscope.cn/api/v1/models/search', {'PageSize': 10, 'PageNumber': 1, 'Name': 'roleplay'}),
    ('https://modelscope.cn/api/v1/search', {'keyword': 'roleplay', 'pageSize': 10}),
]


def try_get(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.status, resp.read(300)
    except urllib.error.HTTPError as exc:
        return exc.code, b''
    except Exception as exc:
        return 'ERR', str(exc)[:100].encode()


def try_post(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=UA, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.status, resp.read(300)
    except urllib.error.HTTPError as exc:
        return exc.code, b''
    except Exception as exc:
        return 'ERR', str(exc)[:100].encode()


if __name__ == '__main__':
    for url in GETS:
        status, body = try_get(url)
        print(f'GET {status} {url[:95]}')
        if status == 200:
            print('   ', body[:250])
    for url, payload in POSTS:
        status, body = try_post(url, payload)
        print(f'POST {status} {url[:80]} {payload}')
        if status == 200:
            print('   ', body[:250])
