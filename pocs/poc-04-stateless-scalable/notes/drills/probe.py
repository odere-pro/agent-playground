import sys

import httpx

c = httpx.Client(base_url="http://127.0.0.1:18080", timeout=10)
r = c.post("/v1/run", json={"input": {"text": "simplify: hi"}, "budget": {"max_tokens": 5000}})
j = r.json()
print(
    sys.argv[1],
    "status",
    r.status_code,
    "config",
    (j.get("versions") or {}).get("config"),
    "error",
    j.get("error") or j.get("detail"),
)
