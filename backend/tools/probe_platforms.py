import json
import urllib.request
import urllib.error

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) xworld-research'}

CANDIDATES = [
    ('modelscope-search-1', 'https://modelscope.cn/api/v1/models?PageSize=20&PageNumber=1&Name=roleplay&SortBy=Default&Target='),
    ('modelscope-search-2', 'https://modelscope.cn/api/v1/models?page_size=20&page_number=1&search=roleplay&sort_by=default&target='),
    ('modelscope-search-3', 'https://modelscope.cn/api/v1/models/list?page_size=20&search=roleplay'),
    ('modelscope-search-4', 'https://modelscope.cn/api/v1/search/models?page_size=20&query=roleplay'),
    ('modelscope-dolphin', 'https://modelscope.cn/api/v1/dolphin/models?PageSize=20&PageNumber=1&Name=roleplay'),
    ('modelscope-api-v1-sg', 'https://www.modelscope.cn/api/v1/models?PageSize=20&PageNumber=1&Name=roleplay'),
    ('modelscope-html', 'https://modelscope.cn/models?name=roleplay'),
    ('modelscope-sg-html', 'https://www.modelscope.cn/models?page=1&name=roleplay'),
    ('openxlab', 'https://openxlab.org.cn/api/v1/models?page_size=20&keywords=roleplay'),
    ('modelers', 'https://modelers.cn/api/v1/models?page=1&size=20&name=roleplay'),
    ('modelers-2', 'https://modelers.cn/api/v1/models/search?page=1&size=20&keyword=roleplay'),
    ('ollama-search', 'https://ollama.com/search?q=roleplay'),
    ('ollama-search-rp', 'https://ollama.com/search?q=rp+model'),
    ('github-repos', 'https://api.github.com/search/repositories?q=roleplay+gguf&per_page=20&sort=stars'),
    ('github-repos-st', 'https://api.github.com/search/repositories?q=sillytavern+model+gguf&per_page=20&sort=stars'),
    ('github-code', 'https://api.github.com/search/code?q=roleplay+extension:gguf'),
    ('kaggle', 'https://www.kaggle.com/api/v1/models/list?search=roleplay'),
]


def probe(name, url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read(400)
            return name, resp.status, len(body), body[:200]
    except urllib.error.HTTPError as exc:
        return name, exc.code, 0, str(exc.reason)[:120]
    except Exception as exc:
        return name, 'ERR', 0, str(exc)[:120]


if __name__ == '__main__':
    for name, url in CANDIDATES:
        n, status, size, head = probe(name, url)
        print(f'--- {name}: {status} {size}')
        print('   ', head)
