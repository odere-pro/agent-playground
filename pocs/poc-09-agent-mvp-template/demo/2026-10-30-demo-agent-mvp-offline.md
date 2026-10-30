# PoC-9 demo, offline

Date: 2026-10-30. Command: `bash pocs/poc-09-agent-mvp-template/demo/demo.sh`, exit 0. No key, `UV_OFFLINE=1`.

```text
agentctl: created demo-agent at <tmp> (first commit on main)
next: cd <tmp> && make setup test run
6217bc9 chore: scaffold demo-agent with agentctl
27 passed in 24.79s
Success: no issues found in 10 source files
1 passed in 11.83s
3 passed in 0.06s
4 passed, 1 warning in 11.87s
1 passed in 11.78s
--- break the logic: the eval must fail
make: *** [eval] Error 1
OK: the eval caught it
```

`--live` starts the Compose `fake` profile and calls it from the host. It was not recorded here: `curl` is not allowed in this session. `docker compose up --wait` did report every service healthy on 2026-10-30.
