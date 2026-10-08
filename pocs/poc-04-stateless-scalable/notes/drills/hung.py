"""Hung-workload drill: pause workload-1, watch chassis-1 /ready and Traefik routing."""

import subprocess
import time

import httpx


def ready(n: int) -> str:
    code = (
        "import socket,urllib.request,urllib.error;a=socket.gethostbyname(socket.gethostname())\n"
        "try:\n r=urllib.request.urlopen(f'http://{a}:8080/ready',timeout=5);print(r.status,r.read().decode())\n"
        "except urllib.error.HTTPError as e: print(e.code,e.read().decode())"
    )
    p = subprocess.run(
        ["docker", "exec", f"poc04-chassis-{n}-1", "python", "-c", code],
        capture_output=True,
        text=True,
        timeout=20,
    )
    return (p.stdout or p.stderr).strip()


def burst(label: str, n: int = 40) -> None:
    ok = bad = 0
    lat = []
    with httpx.Client(base_url="http://127.0.0.1:18080", timeout=10) as c:
        for _ in range(n):
            t = time.monotonic()
            try:
                r = c.post("/v1/run", json={"input": {"text": "simplify: hi"}})
                ok += r.status_code == 200
                bad += r.status_code != 200
            except httpx.HTTPError:
                bad += 1
            lat.append((time.monotonic() - t) * 1000)
    lat.sort()
    print(
        f"{label}: {n} sequential calls via Traefik -> ok {ok}, not ok {bad}, max {lat[-1]:.0f} ms"
    )


def calls(n: int) -> str:
    return subprocess.run(
        ["docker", "logs", "--since", "0s", "poc04-chassis-1-1"], capture_output=True, text=True
    ).stdout


print("before: chassis-1 /ready ->", ready(1))
burst("before")
subprocess.run(["docker", "pause", "poc04-workload-1-1"], check=True)
t0 = time.monotonic()
print("$ docker pause poc04-workload-1-1")
for _ in range(30):
    r = ready(1)
    if r.startswith("503"):
        print(f"after {time.monotonic() - t0:.1f} s: chassis-1 /ready ->", r)
        break
    time.sleep(1)
print("chassis-2 /ready ->", ready(2))
time.sleep(3)
burst("paused")
subprocess.run(["docker", "unpause", "poc04-workload-1-1"], check=True)
t0 = time.monotonic()
print("$ docker unpause poc04-workload-1-1")
for _ in range(30):
    r = ready(1)
    if r.startswith("200"):
        print(f"after {time.monotonic() - t0:.1f} s: chassis-1 /ready ->", r)
        break
    time.sleep(1)
time.sleep(3)
burst("unpaused")
