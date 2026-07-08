import sys, json, urllib.request
sys.path.insert(0, r'C:\Users\sherv\.openclaw\workspace\skills\job_intelligence')
from lib.chrome_manager import start, is_running

if not is_running():
    print('Starting pipeline Chrome...')
    start()
else:
    print('Chrome already running')

cfg = json.load(open(r'C:\Users\sherv\.ji\chrome-config.json'))
port = cfg['CDP_PORT']
resp = urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=5)
data = json.loads(resp.read())
print(f"Browser: {data.get('Browser', '?')[:30]}")
print('Ready: yes')
