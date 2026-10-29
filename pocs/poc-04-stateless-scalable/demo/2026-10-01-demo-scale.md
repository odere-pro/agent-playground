# PoC-4 demo: kill a replica, drain a replica, reload the config (Compose, project poc04)

Recorded 2026-10-01T21:27:50Z by pocs/poc-04-stateless-scalable/demo/demo.sh.
Docker 29.7.2, Darwin arm64.

Command, from the repo root, on the images of 2026-10-01 21:04:47 to 21:05:22 UTC (`deploy/compose/scale.sh build`), with every other agent stopped: `pocs/poc-04-stateless-scalable/demo/demo.sh`. Exit code 0, last line `result: every step ok`. Everything below "Summary" is the script's output as written, unedited.

## Summary (added by hand after the run)

What was shown:

- **A killed replica loses nothing, and the retry gets the same result.** Two pairs of echo-python ran under 20 workers of retried load. Pair 2 (chassis and workload) was SIGKILLed after 10 s. Every call succeeded: 3,284 sent, 3,284 ok, 24 retried (`lost: 0`). Each of the 24 keys that needed a retry was sent again and returned the same envelope with `Idempotent-Replayed: true` (24 of 24). Only pair 1 was alive by then, so that was another replica. A control sample of 20 untroubled keys replayed the same way.
- **A drained replica fails nothing.** On a fresh pair 2, the chassis got SIGTERM under load with no client retry. It exited 0 in 4.0 s, and 1,997 of 1,997 calls succeeded with 0 failed.
- **A config change in MinIO takes effect with no restart.** Before the change, `budget.max_tokens: 5000` was allowed (config `f5430e1a61c8`). After `spec.limits.max_tokens_max: 4000` was uploaded, it was refused with 400 on both replicas (config `4a3a45d84b38`). A bad document was then refused (`config refused, keeping version=4a3a45d84b38` in both chassis logs), and the 4000 limit stayed. `StartedAt` and `RestartCount 0` did not change. The seed document was restored at the end.

What was not shown, or only in part:

- **Throughput at 1, 2, 4 pairs per engine, and the sidecar's CPU, memory, and hop per replica.** The load matrix is not rerun in the demo; it takes about 40 minutes. The record is `notes/2026-10-01-load-results.md`, quiet pass. It shows 4 of 8 growth steps grow, and 4 are strict xfails. **4 pairs do not fit this 7.9 GiB Docker Desktop VM** without starving it. The chassis figures: 150 to 182 MiB; 0.105 to 0.129 vCPU at 10 RPS; 0.6 to 1.3 vCPU per 100 RPS at saturation. The hop is below that method's 1 ms resolution and not isolated.
- **The kill is SIGKILL by `docker kill`, not a node loss.** The kind rolling-restart drill is a separate record (`deploy/kind/run.sh`).
- **Only echo-python.** The other engines ran the drills' smoke path in the read-only check (`notes/2026-10-01-drills.md`, section 5) and the load matrix, not the kill and drain drills.
- **The fenced claim (a retryable 409 when a replica cannot renew)** was not triggered here. The kill drill's retries arrived after the claim's owner was dead, and they replayed finished results.

```console

## 1. Two pairs of echo-python

$ /Users/oleksandrderechei/git/agent-orchestration/deploy/compose/scale.sh up echo-python 2
scale.sh: generated secrets in .env.poc04 (mode 600, git-ignored)
scale.sh: echo-python, 2 pair(s), events none, config scale.yaml
 Network poc04 Creating 
 Network poc04 Created 
poc04-chassis-1-1	running	Up 5 seconds (healthy)
poc04-chassis-2-1	running	Up 5 seconds (healthy)
poc04-fake-model-server-1	running	Up 11 seconds (healthy)
poc04-minio-1	running	Up 11 seconds
poc04-traefik-1	running	Up 11 seconds
poc04-valkey-1	running	Up 11 seconds (healthy)
poc04-workload-1-1	running	Up 5 seconds (healthy)
poc04-workload-2-1	running	Up 5 seconds (healthy)

## 2. Kill drill: SIGKILL pair 2 under load; the client retries with the same key

    "url": "http://127.0.0.1:18080",
    "mode": "retry",
    "concurrency": 20,
    "duration_s": 40.0,
    "sent": 3284,
    "ok": 3284,
    "failed": 0,
    "attempts": 3314,
    "retried": 24,
    "p50_ms": 177.02,
    "p95_ms": 421.9,
    "failures": []
  },
  "replay_retried": {
    "checked": 24,
    "same": 24,
    "differs": 0,
    "not_marked_replayed": 0,
    "failed": 0,
    "mismatches": []
  },
  "replay_control": {
    "checked": 20,
    "same": 20,
    "differs": 0,
    "not_marked_replayed": 0,
    "failed": 0,
    "mismatches": []
  },
  "lost": 0,
  "passed": true
}

## 3. Graceful drill: a fresh pair 2, SIGTERM its chassis under load, no retry

$ /Users/oleksandrderechei/git/agent-orchestration/deploy/compose/scale.sh up echo-python 2
scale.sh: echo-python, 2 pair(s), events none, config scale.yaml
poc04-chassis-1-1	running	Up 5 seconds (healthy)
poc04-chassis-2-1	running	Up 5 seconds (healthy)
poc04-fake-model-server-1	running	Up 11 seconds (healthy)
poc04-minio-1	running	Up 11 seconds
poc04-traefik-1	running	Up 11 seconds
poc04-valkey-1	running	Up 11 seconds (healthy)
poc04-workload-1-1	running	Up 5 seconds (healthy)
poc04-workload-2-1	running	Up 5 seconds (healthy)
{
  "mode": "graceful",
  "pair": 2,
  "actions": [
    "$ docker kill -s TERM poc04-chassis-2-1 -> exit 0 (0.1 s)",
    "$ docker wait poc04-chassis-2-1 -> exit 0 (4.0 s)",
    "$ docker stop poc04-workload-2-1 -> exit 0 (0.5 s)"
  ],
  "load": {
    "url": "http://127.0.0.1:18080",
    "mode": "no-retry",
    "concurrency": 20,
    "duration_s": 40.0,
    "sent": 1997,
    "ok": 1997,
    "failed": 0,
    "attempts": 1997,
    "retried": 0,
    "p50_ms": 390.99,
    "p95_ms": 615.69,
    "failures": []
  },
  "passed": true
}

## 4. Config reload: a good document, then a bad one, in MinIO; no restart

$ uv run python /Users/oleksandrderechei/git/agent-orchestration/pocs/poc-04-stateless-scalable/load/reload_drill.py
seed:      max_tokens 5000 -> 200, config f5430e1a61c8
uploaded:  spec.limits.max_tokens_max 4000
after good: max_tokens 5000 -> 400: {"detail":"budget.max_tokens: 5000 is above this chassis's limit of 4000; ask for at most 4000"}
after good: max_tokens 100 -> config 4a3a45d84b38
uploaded:  max_tokens_max -1 and an unknown field
after bad: max_tokens 5000 -> 400: {"detail":"budget.max_tokens: 5000 is above this chassis's limit of 4000; ask for at most 4000"}
after bad: max_tokens 100 -> config 4a3a45d84b38
poc04-chassis-1-1: config refused, keeping version=4a3a45d84b38: reason=invalid paths=spec.limits.max_tokens_max,spec.limits.not_a_field
poc04-chassis-1-1: started/restarts 2026-10-01T21:28:54.916969763Z 0 -> 2026-10-01T21:28:54.916969763Z 0
restored:  the seed document
reload drill: passed

## 5. Down

$ /Users/oleksandrderechei/git/agent-orchestration/deploy/compose/scale.sh down
 Network poc04 Removing 
 Network poc04 Removed 

result: every step ok
```
