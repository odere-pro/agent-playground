# PoC-4 Dapr against the broker client, 2026-10-01

Package P18. Input to ADR-004 and to exit criterion 10 ("The Dapr decision is written down with the measurements"). Plan: `docs/plans/2026-10-01-poc-04-stateless-scalable.md`, section 2 and the P18 row in section 12. PoC scope: `docs/planning/poc/004-*.md`, "The Dapr decision".

## The question

Do result events go out through Dapr pub/sub (a daprd sidecar per replica) or through a broker client inside the chassis (aiokafka), both behind `EventPort`? The PoC scope asks for three measurements: a third container's CPU and memory per replica, the work to close Dapr's localhost API to the workload, and the lines of code for CloudEvents, retries, and a dead-letter topic. The planning doc's suggestion is the broker client.

## Method

- Stack: `deploy/compose/scale.sh` (project `poc04`), 1 pair, `echo-python`, images from `scale.sh build` earlier on 2026-10-01 (chassis and echo-python built about 40 minutes before the first run; not rebuilt).
- Three paths, picked by config only:
  - `none`: `scale.sh up echo-python 1`, `packages/chassis/configs/scale.yaml` (`events: none`). The baseline.
  - `kafka`: `scale.sh up echo-python 1 --events kafka`, `scale-kafka.yaml` (`events: kafka`, `result_events: true`). The chassis publishes with aiokafka.
  - `dapr`: `scale.sh up echo-python 1 --events dapr`, the overlay `docker-compose.scale-dapr.yaml` and `scale-dapr.yaml` (`events: dapr`, `result_events: true`). The chassis posts to daprd on `127.0.0.1:3500`; daprd publishes to the same Kafka.
- Per run (`notes/dapr/measure.py`, which reuses `load/run_matrix.py`): `scale.sh up` (a clean project every time, Kafka included), wait for `/ready` 5 times in a row, 5 idle `docker stats` samples 2 s apart, a 10 s Locust warm-up, then 60 s of the `rate` scenario (10 users at 1 call per second, so 10 RPS) while `docker stats` samples project `poc04`. Each `docker stats --no-stream` takes about 2 s, so a 60 s window has 16 samples, not 30. Then `scale.sh down`.
- Event count: the sum of end offsets of `agents.task.completed.v1` over its 3 partitions before and after the 60 s window (`kafka-get-offsets.sh`), and then every message on the topic counted with `kafka-console-consumer.sh --from-beginning --timeout-ms 15000` inside `poc04-kafka-1`, against the warm-up plus measured requests. `agents.task.failed.v1` was counted too.
- Order: kafka 1, dapr 1, then none 1, kafka 2, dapr 2, none 2, then dapr 3, kafka 3, none 3. Three runs per path, because CPU per call on this Docker Desktop VM swings up to 2x between runs.

```console
$ uv run python pocs/poc-04-stateless-scalable/notes/dapr/measure.py --events kafka --rep 1
$ uv run python pocs/poc-04-stateless-scalable/notes/dapr/measure.py --events dapr --rep 1
$ bash -c 'for spec in "none 1" "kafka 2" "dapr 2" "none 2"; do set -- $spec; uv run python pocs/poc-04-stateless-scalable/notes/dapr/measure.py --events $1 --rep $2 > .../raw/$1-rep$2.log 2>&1; echo "$1 rep$2 exit=$?"; done'
none rep1 exit=0
kafka rep2 exit=0
dapr rep2 exit=0
none rep2 exit=0
$ bash -c 'for spec in "dapr 3" "kafka 3" "none 3"; do ...; done'
dapr rep3 exit=0
kafka rep3 exit=0
none rep3 exit=0
$ uv run python pocs/poc-04-stateless-scalable/notes/dapr/summarize.py > .../raw/summary.txt
```

- Host: macOS 26.6.2 arm64, 14 CPUs; Docker Desktop VM with 14 CPUs and 7,935 MiB. 17 unrelated containers (`paligo-*`, `opensearch-*`) ran throughout and were not touched. Other agents were working on the same Mac at the same time (this was not a quiet pass). `uptime` at the end: `load averages: 2.12 3.11 4.85`.

## Results

Every run: 0 failed requests, 10.04 to 10.14 RPS. Chassis and daprd: mean vCPU / mean MiB over the samples. Raw: `notes/dapr/raw/<path>-rep<n>.json`, `-docker-stats.csv`, Locust CSVs, `summary.txt`, `table.txt`.

| Path | Run | Chassis idle vCPU / MiB | Chassis at 10 RPS vCPU / MiB | daprd idle vCPU / MiB | daprd at 10 RPS vCPU / MiB | Events: window; all on topic | p50 / p95 ms |
| ---- | --- | ----------------------- | ---------------------------- | --------------------- | -------------------------- | ---------------------------- | ------------ |
| none | 1 | 0.069 / 150 | 0.140 / 162 | - | - | - (610 sent) | 110 / 170 |
| none | 2 | 0.068 / 150 | 0.116 / 161 | - | - | - (603 sent) | 100 / 160 |
| none | 3 | 0.104 / 150 | 0.141 / 161 | - | - | - (610 sent) | 110 / 160 |
| kafka | 1 | 0.108 / 180 | 0.174 / 194 | - | - | 610 of 610; 711 of 711 | 120 / 270 |
| kafka | 2 | 0.104 / 152 | 0.120 / 165 | - | - | 610 of 610; 720 of 720 | 110 / 170 |
| kafka | 3 | 0.120 / 154 | 0.146 / 164 | - | - | 600 of 600; 703 of 703 | 110 / 290 |
| dapr | 1 | 0.110 / 150 | 0.146 / 163 | 0.0002 / 138.9 | 0.0024 / 151.1 | 600 of 600; 700 of 700 | 110 / 200 |
| dapr | 2 | 0.111 / 152 | 0.181 / 164 | 0.0002 / 26.1 | 0.0037 / 37.4 | 610 of 610; 717 of 717 | 170 / 270 |
| dapr | 3 | 0.054 / 150 | 0.132 / 163 | 0.0002 / 26.3 | 0.0025 / 34.1 | 610 of 610; 720 of 720 | 120 / 170 |

The summary per path, as ranges over the three runs (never one number):

| Path | Chassis vCPU idle | Chassis vCPU at 10 RPS | Chassis MiB at 10 RPS | Extra container per replica | Events delivered of sent | Lines in marked sections (CE / publish retries / delivery retries / DLQ) | Extra config lines |
| ---- | ----------------- | ---------------------- | --------------------- | --------------------------- | ------------------------ | ------------------------------------------------------------------------- | ------------------ |
| none | 0.068 to 0.104 | 0.116 to 0.141 | 161 to 162 | none | n/a | n/a | n/a |
| kafka | 0.104 to 0.120 | 0.120 to 0.174 | 164 to 194 | none | 2,134 of 2,134 (all 3 runs) | 22 / 27 / 15 / 14 = 78 (file 241) | 0 |
| dapr | 0.054 to 0.111 | 0.132 to 0.181 | 163 to 164 | daprd: 0.0024 to 0.0037 vCPU, 34 to 151 MiB at 10 RPS; 26 to 139 MiB idle; image 263 MB | 2,137 of 2,137 (all 3 runs) | 34 / 26 / 24 / 17 = 101 (file 271) | 46 YAML lines (34 non-comment) in `dapr/pubsub.yaml` and `dapr/resiliency.yaml`, plus the 120-line overlay |

What the numbers say, and do not say:

- **Chassis CPU: no difference resolved.** At 10 RPS, the three paths' ranges overlap (none 0.116 to 0.141, kafka 0.120 to 0.174, dapr 0.132 to 0.181). Both event paths may cost the chassis a few hundredths of a vCPU at 10 events per second, but the spread between runs of one path (up to 0.054 vCPU) is as large as the gap between paths. This rate cannot price publishing in the chassis on this host.
- **Chassis idle CPU** sits near 0.1 vCPU on every path, the baseline included (0.104 in none 3). It is not an event cost.
- **Chassis memory.** none 161 to 162 MiB; dapr 163 to 164 (the chassis only adds an httpx client); kafka 164 to 165 in runs 2 and 3, 194 in run 1. Run 1 of kafka was the first run of the session and every container in it was higher (workload 95 MiB against 80 to 81 elsewhere, Kafka 538 MiB against 393 to 434), so it is not attributed to aiokafka. The measured range is still 164 to 194, as recorded.
- **daprd, the third container.** CPU is close to zero: at most 0.0037 vCPU at 10 RPS. Memory is the cost, and it is not stable: 151 MiB in run 1, 37 and 34 MiB in runs 2 and 3 (idle 139, 26, 26). The cause of the 139 MiB run is not known; it was the first daprd start of the session. Planning should use the high value until it is measured on a cluster: up to about 150 MiB per replica, 34 MiB at the low end, against 256 MiB `mem_limit` (suggested).
- **Events: every one arrived on both paths.** 3 of 3 runs per path, 0 on `agents.task.failed.v1`. No loss and no duplicate at 10 RPS.
- **Latency** is not part of the question; p50 and p95 are in the table only to show the runs were alike. They swing between runs within one path as much as between paths.

## Closing Dapr's localhost API to the workload

daprd joins the chassis's network namespace (as the workload does), so the workload shares daprd's loopback. Raw: `notes/dapr/raw/api-probe.txt`. Probe: `notes/dapr/probe_api.py`, Python stdlib, run inside the workload container with no token or a wrong one.

```console
$ docker exec -i poc04-workload-1-1 python - < pocs/poc-04-stateless-scalable/notes/dapr/probe_api.py
daprd publish, no token                    POST 401  b'invalid api token\n'
daprd publish, wrong token                 POST 401  b'invalid api token\n'
daprd state get, no token                  GET  401  b'invalid api token\n'
daprd invoke, no token                     POST 401  b'invalid api token\n'
daprd bulk publish, no token               POST 401  b'invalid api token\n'
daprd metadata, no token                   GET  401  b'invalid api token\n'
daprd healthz, no token                    GET  204  b''
daprd healthz/outbound, no token           GET  204  b''
daprd shutdown, no token                   POST 401  b'invalid api token\n'
daprd metrics :9090, no token              GET  200  b'# HELP dapr_http_client_completed_count Count of completed requests\n# TYPE dapr_http_client_completed_count counter\ndapr'
chassis /dapr/subscribe :8090, no token    GET  401  b''
chassis /dapr/events :8090, no token       POST 401  b''
chassis /dapr/events :8090, wrong token    POST 401  b''
tcp 127.0.0.1:3500  open
tcp 127.0.0.1:50001 open
tcp 127.0.0.1:50002 closed (111)
tcp 127.0.0.1:9090  open
tcp 127.0.0.1:8090  open
tcp 127.0.0.1:9000  open
tcp 127.0.0.1:8080  closed (111)
```

The workload's environment holds no token (names only, never values):

```console
$ docker inspect poc04-workload-1-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | cut -d= -f1
CHASSIS_MODEL_URL CHASSIS_TOOL_URL DRAIN_TIMEOUT_MS GPG_KEY HOST LANG PATH PORT PYTHONUNBUFFERED PYTHON_SHA256 PYTHON_VERSION
$ docker inspect poc04-workload-1-1 ... | grep -c -E '^(DAPR_API_TOKEN|APP_API_TOKEN)='
0
$ docker exec poc04-workload-1-1 python -c 'import os; print(sorted(k for k in os.environ if "TOKEN" in k or "DAPR" in k))'
[]
$ docker inspect poc04-daprd-1-1 ... | cut -d= -f1
APP_API_TOKEN DAPR_API_TOKEN PATH SSL_CERT_FILE
```

What listens in the pair's namespace, and who owns it (`/proc/net/tcp` from the workload, then again with daprd stopped):

| Address | Owner | Reachable from another container on `poc04` (`poc04-kafka-1`, bash `/dev/tcp/chassis-1/<port>`) |
| ------- | ----- | ---------------------------------------------------------------------------------------------- |
| `127.0.0.1:3500` | daprd HTTP API | closed |
| `127.0.0.1:50001` | daprd gRPC API | closed |
| `:::9090` | daprd metrics, no auth | **open**; `GET /metrics` answers `HTTP/1.0 200 OK`, 463 `dapr_*` lines |
| `:::47901` (random port) | daprd, gone when daprd stops; presumably the internal gRPC port for sidecar-to-sidecar calls (not confirmed: the log level is `warn`, so daprd did not log it) | **open** |
| `127.0.0.1:8090` | chassis proxy (model, tools, `/dapr/*`) | closed |
| `127.0.0.1:9000` | workload | closed |
| `172.26.0.7:8080` | chassis public port | open (by design, Traefik's upstream) |

Every setting it took, with file and line:

1. Two tokens generated per `up`: `deploy/compose/scale.sh` lines 58 and 59 (`openssl rand -hex 24` into `.env.poc04`, mode 600).
2. Tokens to daprd only: `deploy/compose/docker-compose.scale-dapr.yaml` lines 45 and 46. To the chassis only: lines 51 and 52. Both `${NAME:?set by scale.sh}`. The workload's env is fixed in `docker-compose.scale.yaml` lines 146 to 153 and names neither.
3. daprd API on loopback only: `docker-compose.scale-dapr.yaml` lines 36 and 37 (`--dapr-listen-addresses 127.0.0.1`), port lines 34 and 35. No `ports:` on daprd.
4. daprd calls the chassis on the proxy port, never the public one: lines 30 and 31 (`--app-port 8090`) and 32 and 33 (`--app-channel-address 127.0.0.1`). App id: lines 28 and 29.
5. Shared namespace: line 76 (`network_mode: "service:chassis-1"`), and 89, 102, 115 for pairs 2 to 4.
6. Component scoped to the one app id: `deploy/compose/dapr/pubsub.yaml` lines 24 and 25 (`scopes: [echo]`). Consumer group: lines 19 and 20.
7. The chassis sends `dapr-api-token` on every publish: `packages/chassis/src/chassis/adapters/dapr/events.py` line 55 (`TOKEN_HEADER`) and line 71. `from_env` refuses to start without both tokens: line 148.
8. The chassis checks daprd's callbacks: `events.py` lines 222 to 224 (`hmac.compare_digest` against `APP_API_TOKEN`), 401 at lines 230 and 231 (`/dapr/subscribe`) and 250 and 251 (`/dapr/events/{topic}`). Without this, the workload could post fake events into the chassis on `127.0.0.1:8090`, the same port it uses for the model.
9. Hardening of the daprd container: `docker-compose.scale-dapr.yaml` lines 17 to 25 (digest pin, `cap_drop`, `no-new-privileges`, read-only root, tmpfs, CPU and memory caps).

So: 9 settings across 4 files, plus the adapter's token check on both directions. The tokens close the HTTP and gRPC API (every non-health endpoint answered 401). They do not close two daprd ports that bind every interface:

- **Metrics on 9090** answer anyone in the pair's namespace and any container on the `poc04` network, with no auth. Not set today: `--enable-metrics=false` or `--metrics-listen-address 127.0.0.1` (suggested; the flag name is from Dapr's docs and was not tried here).
- **A random port (47901 in this run)**, open to the network. If it is the internal gRPC port, with mTLS off (`mTLS is disabled` in daprd's log), a container on the network could make sidecar-to-sidecar calls that daprd forwards to the chassis with the app token. This was not tested (it needs a gRPC client) and is a question for `platform-security`: pin and bind it (`--dapr-internal-grpc-port`, `--dapr-internal-grpc-listen-address`, suggested and not tried), or run Sentry mTLS.

The broker-client path has no such surface: no extra listener in the pair. Both paths share the open PLAINTEXT Kafka (SECURITY.md section 7, 020 X-8), which today lets any container on the network publish to the result topics directly, bypassing both.

## Lines of code

`wc -l` and the lines between each `# --- <section>` and `# --- end <section>` marker (exclusive). Raw: `notes/dapr/raw/loc.txt`.

```console
$ wc -l packages/chassis/src/chassis/adapters/{kafka,dapr}/*.py deploy/compose/dapr/*.yaml deploy/compose/docker-compose.scale-dapr.yaml
       5 packages/chassis/src/chassis/adapters/kafka/__init__.py
     241 packages/chassis/src/chassis/adapters/kafka/events.py
       5 packages/chassis/src/chassis/adapters/dapr/__init__.py
     271 packages/chassis/src/chassis/adapters/dapr/events.py
      25 deploy/compose/dapr/pubsub.yaml
      21 deploy/compose/dapr/resiliency.yaml
     120 deploy/compose/docker-compose.scale-dapr.yaml
     688 total
```

| Section | Kafka adapter | Dapr adapter |
| ------- | ------------- | ------------ |
| CloudEvents wire form | 22 (lines 45 to 68) | 34 (60 to 95) |
| Retries: publish | 27 (136 to 164) | 26 (162 to 189) |
| Retries: delivery | 15 (208 to 224) | 24 (246 to 271) |
| Dead-letter topic | 14 (226 to 241) | 17 (226 to 244) |
| Sum of sections | 78 | 101 |
| Whole file | 241 | 271 |
| Component YAML | 0 | 46 (34 non-comment, non-blank) |

Dapr did not save code. Its adapter is 30 lines longer, and each section exists on both sides. The Dapr CloudEvents section is longer because it undoes what daprd does to the event (below). The Dapr delivery section is the route table and the HTTP callback with the token check; the retry loop itself is daprd's.

## What Dapr did not give for free

From `packages/chassis/tests/integration/test_dapr_events_contract.py` (two strict xfails and the module docstring) and the adapter:

- **Dead-letter events lack the two extensions.** daprd dead-letters the event as it was published, with no `deadletterreason` and no `deadletterattempts`, which the `EventPort` contract requires (xfail `test_after_max_attempts_the_event_goes_to_the_dead_letter_topic`).
- **Retry count comes from `resiliency.yaml`, not `max_attempts`.** daprd delivers `maxRetries + 1` times (3), whatever `subscribe(max_attempts=...)` says. The adapter only logs a mismatch, and only when `DAPR_MAX_RETRIES` is set (`events.py` line 202). It is set in no Compose file or config (`grep DAPR_MAX_RETRIES deploy/compose/*.yaml packages/chassis/configs/*.yaml` prints nothing), so in this stack the check never runs.
- **One consumer group per app id.** A second group on the same topic needs a second daprd with another app id (xfail `test_two_groups_each_get_every_event`, `raises=ValueError`).
- **Subscriptions are read once, at daprd start.** A topic subscribed after daprd started is not delivered until daprd restarts; the test binding restarts daprd for that (`_RestartOnSubscribe`). The chassis must subscribe in its lifespan, before the proxy is ready.
- **`traceparent` is overwritten.** daprd replaces the event's `traceparent` with the publish request's trace context, and writes an all-zero one when there was none. The adapter sends the event's `traceparent` as the W3C header and drops the all-zero value on delivery (`events.py` lines 64 to 86), plus filters the attributes daprd adds (`pubsubname`, `topic`, `traceid`, `tracestate`).
- **A second listener surface** to close (above), and a 263 MB image to pin and patch per release.

## What each path gives and costs

- **Broker client (aiokafka in the chassis).** Gives: the whole `EventPort` contract, dead-letter extensions and `max_attempts` included; any number of groups; subscribe at any time; no extra container, port, image, or token. Costs: a Kafka client library in the chassis image (`aiokafka==0.14.0`, pure Python); broker auth, TLS, and topic creation are the chassis's job (020 X-8); swapping brokers means a new adapter behind `EventPort`.
- **Dapr.** Gives: retries and dead-lettering run in daprd; a broker swap is a component YAML change, not code; one sidecar shape across languages. Costs: a third container per replica (34 to 151 MiB measured, near-zero CPU at 10 RPS); two tokens and 9 settings to close its API, and two network-wide ports (metrics and the random internal gRPC port) still open; two contract cases that cannot pass; retry and group settings that live in YAML outside the chassis config; a daprd restart to add a subscription; trace context that has to be repaired. On kind, the daprd sidecar would come from the Dapr operator and injector, which this note did not measure.

## Caveats

- One host (Docker Desktop on macOS), one pair, the fake model server, `echo-python` only. Not a cluster.
- Short runs: 60 s at 10 RPS, 16 `docker stats` samples per run, 3 runs per path. Other agents were working on the Mac during the runs. CPU differences under about 0.05 vCPU are inside the run-to-run spread and are not findings.
- 10 RPS is a moderate rate. Neither path was pushed to the rate where publishing would show in the chassis's CPU. At 75 RPS (the 1-pair ceiling in `2026-10-01-load-results.md`) the answer could differ.
- daprd's memory varied 4x between runs, for an unknown reason. Three runs are not enough to say which value is typical.
- `docker stats` memory on Docker Desktop is the cgroup's usage minus inactive file cache; it is not RSS.
- Port 47901's role is inferred, not confirmed.
- Images were built before this run from a working tree that other agents were editing; the chassis code measured is that build, not today's tree.

## Recommendation, as input to ADR-004

The measurements support the planning doc's suggestion: **the broker client in the chassis** behind `EventPort`, with Dapr not adopted for PoC-4 and the MVP.

- Resources do not decide it at this rate: the chassis's CPU and memory show no resolved difference between none, kafka, and dapr. daprd's own CPU is negligible. Its memory, up to about 150 MiB per replica, is the one resource cost, and it scales with every replica.
- The deciding points are contract and surface: Dapr fails two contract cases (dead-letter extensions and `max_attempts`, a second group), needs a restart for a new subscription, rewrites `traceparent`, saves no lines of adapter code (101 against 78 in the marked sections, plus 46 lines of YAML), and adds a listener surface that the tokens do not fully close (metrics and the internal gRPC port bind every interface).
- What would change the answer:
  - The platform must support more than one broker product (Kafka plus a cloud bus) per deployment, so the broker swap by YAML is worth a sidecar.
  - Non-Python workloads must publish or consume events themselves, outside the chassis: then a language-neutral sidecar beats a client per language. Today only the chassis publishes (ADR-001: only the chassis holds credentials).
  - The cluster already runs the Dapr control plane (Sentry mTLS, operator), which removes the open internal port and the token handling.
  - A cluster measurement shows daprd under about 40 MiB steadily, and Dapr adds the dead-letter metadata and per-subscription retry count upstream.
  - Load near the 1-pair ceiling shows aiokafka costing the chassis materially more CPU than an HTTP post to daprd.

Open items for ADR-004 and `platform-security`: close or bind daprd's metrics port and internal gRPC port if Dapr is ever kept; set `DAPR_MAX_RETRIES` or drop the check, since today it never runs.
