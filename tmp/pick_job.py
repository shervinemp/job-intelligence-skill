import sys
sys.path.insert(0, r'C:\Users\sherv\.openclaw\workspace\skills\job_intelligence')
from lib.db import get_conn
conn = get_conn()
rows = conn.execute('SELECT id, title, company FROM jobs WHERE stage = "tailored" AND id != "6adef9ac2f66f744" LIMIT 3').fetchall()
for r in rows:
    print(f'{r["id"]}: {r["title"]} @ {r["company"]}')
