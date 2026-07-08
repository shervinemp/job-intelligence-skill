import json, urllib.request
cfg = json.load(open(r'C:\Users\sherv\.ji\chrome-config.json'))
port = cfg['CDP_PORT']
print(f'Port: {port}')
resp = urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=5)
d = json.loads(resp.read())
print(f'Browser: {d.get("Browser", "?")}')
resp2 = urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=5)
pages = json.loads(resp2.read())
for p in pages[:5]:
    title = p.get('title', '?')[:60]
    url = p.get('url', '?')[:60]
    print(f'  {title} | {url}')
