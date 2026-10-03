# Aeris Control MVP

Aeris Control is a deterministic, local-first HVAC operations portfolio application. One Compose command starts a real React/nginx interface, FastAPI service, MQTT broker, equipment simulator, Redis current-state cache, and ClickHouse telemetry history. No API keys, cloud accounts, or mock routes are required.

## Prerequisites

- Docker Desktop 4.27+ with Compose v2 and at least 4 GB available memory
- Node.js 20+ and npm only when running browser tests from the host
- Python 3.12 only when running the optional host-side unit suites
- Apple Silicon and x86-64 are supported by the pinned multi-architecture images

## Start, stop, and reset

From the repository root:

```bash
# Start or rebuild the complete application
docker compose up --build -d

# Show container health
docker compose ps

# Stop containers while preserving demo data
docker compose down

# Fully reset only Aeris Control's disposable local volumes
docker compose -p aeris-control -f compose.yaml down -v --remove-orphans
```

The first start downloads and builds the pinned runtime images. The API applies ordered, versioned SQLite and ClickHouse migrations and idempotently reconciles the 12-device fleet before the simulator is allowed to publish telemetry.

## URLs

| Surface | URL |
| --- | --- |
| Operator UI | <http://localhost:3100> |
| Direct API and OpenAPI | <http://localhost:4100/docs> |
| API health | <http://localhost:4100/api/health> |
| ClickHouse HTTP | <http://localhost:8124> |

All three published ports bind to `127.0.0.1`; Redis (`6379`) and MQTT (`1883`) stay on the internal Compose network. The UI's nginx proxy serves `/api/*`, including WebSocket upgrades, while direct API access remains available on local port `4100`. This build has no authentication. Do not expose it remotely; a remote deployment requires authentication, authorization, TLS for HTTP/WebSocket/MQTT, secured service credentials, and an explicit network policy.

## Five-minute demo

1. Open <http://localhost:3100> and wait for all 12 devices to report running.
2. Find the highlighted `AHU-204` row and its live **High discharge pressure** alarm.
3. Select **Acknowledge** and confirm the unresolved alarm remains visible with its distinct **Acknowledged** lifecycle until a recovery sample closes it.
4. Open `AHU-204` equipment details and inspect its bounded historical pressure chart.
5. Choose **Setpoint**, enter `22.5`, and select **Send command**.
6. Watch the real simulator acknowledge the MQTT command and the UI status change to **Acknowledged**.

There is no authentication in this local portfolio build, so no credentials are required. See [docs/DEMO.md](docs/DEMO.md) for presenter notes and evidence.

## Tests

With the runtime healthy, install the pinned browser runner and execute the real Docker workflow:

```bash
npm ci
npx playwright install chromium
npx playwright test tests/e2e/aeris-demo.spec.ts
```

Run every backend test, including the real Compose-network Redis, ClickHouse, and MQTT integrations:

```bash
docker compose -f compose.test.yaml --profile test run --rm backend-test
docker compose -f compose.test.yaml down
```

Run simulator and frontend checks from host development environments:

```bash
python3.12 -m venv simulator/.venv
simulator/.venv/bin/python -m pip install -e 'simulator[dev]'
simulator/.venv/bin/python -m pytest -q simulator/tests

npm --prefix web ci
npm --prefix web test -- --run
npm --prefix web run typecheck
npm --prefix web run build
```

## Operations and troubleshooting

- **A service is starting:** run `docker compose ps`; `backend`, `simulator`, and `web` intentionally wait for healthy dependencies. On a cold machine, ClickHouse can take 30–60 seconds.
- **Port 3100, 4100, or 8124 is occupied:** stop the conflicting process or set a host override in a copied `.env` file. The published contract uses the defaults shown above.
- **The UI says System degraded:** inspect `docker compose logs backend`; `/api/health` reports database, Redis, ClickHouse, and MQTT independently.
- **A device says Stale:** the latest connected sample is older than `AERIS_STALE_AFTER_SECONDS` (30 seconds by default). Inspect `docker compose logs simulator backend mosquitto`; the simulator health check requires a recently written telemetry heartbeat.
- **Telemetry is rejected:** `/api/health` includes structured telemetry and command-acknowledgement reason counters. The API enforces exact topic/device identity, finite domain-bounded measurements, and configured timestamp skew.
- **A browser is cached on an old route:** nginx supports deep links; hard-refresh the page after rebuilding the web image.
- **A fully fresh demo is needed:** use the explicitly scoped reset command above, then `docker compose up --build -d`. This removes local commands, alarm acknowledgements, telemetry, and cache state.

Logs from the API and simulator are JSON. API responses include `X-Correlation-ID`, and the same identifier appears in request logs for tracing through the nginx proxy.

The tunable safety limits are documented in `.env.example`: staleness, maximum history span, history point budget, accepted telemetry age, and accepted future clock skew. Defaults cap history at seven days and 600 server-downsampled points.

## Design and implementation notes

- [Architecture and data flow](docs/ARCHITECTURE.md)
- [Demo and QA evidence](docs/DEMO.md)
- Approved visual concepts: `docs/design/` (references only; they are not shipped as product UI)
