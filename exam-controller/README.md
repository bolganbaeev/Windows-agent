# ExamController (Phase 5A)

Phase 5A adds explicit network topology configuration, strict migration preflight validation, target reservation, and persisted migration intent. It does not configure adapters or change any Windows network settings.

## Run on Windows

```powershell
cd exam-controller
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
$env:AGENT_TOKEN = "dev-agent-token-change-me"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open the dashboard at **http://192.168.0.10:8000/** from the Controller PC or another authorized lab PC. On the Controller itself, `http://127.0.0.1:8000/` also works. It polls the agents API every four seconds and does not require manual page refresh. The API health endpoint is `http://127.0.0.1:8000/api/health`; the development OpenAPI page is `http://127.0.0.1:8000/docs`. The service is intended for the authorized local lab network; do not expose it to the public Internet.

Registration, heartbeat, preflight, command polling, and acknowledgements use `X-Agent-Token`. The example token is for isolated development only; choose a private value for the lab. Firewall TCP port 8000 only for the authorized lab subnet.

## Network topology configuration

Copy `.env.example` to `.env` and configure the explicit topology before starting the Controller. Defaults represent:

- Initial network/gateway: `192.168.0.0/24`, `192.168.0.1`
- Initial Controller URL: `http://192.168.0.10:8000`
- Internal network/gateway: `192.168.1.0/24`, `192.168.1.1`
- Internal Controller URL: `http://192.168.1.10:8000`

If `INTERNAL_CONTROLLER_URL` uses a DNS name rather than an IPv4 literal, set `INTERNAL_CONTROLLER_IP` to that endpoint's internal IPv4 address so preflight can reserve it against client targets.

These URL settings describe reachable endpoints; they do not bind a second address, add a NIC, configure routing, or make the Controller reachable on both networks. After clients move from `192.168.0.x` to an isolated `192.168.1.x` network, they may no longer reach `http://192.168.0.10:8000`. The Controller must eventually be reachable from the internal LAN, for example through a second Controller NIC/address or appropriate routing. Do not assume routing exists. Verify the actual lab topology and reachability before Phase 5B; one isolated `192.168.0.x` address is insufficient after migration to an isolated `192.168.1.x` network.

## Storage

SQLite is created automatically at `data/exam-controller.sqlite3`. Set `DATABASE_PATH` to select another location. The `clients` table uses the persistent `agent_id` as its unique identity and retains records when clients go offline.

## Dashboard and statuses

The server-rendered dashboard is `GET /`. It shows hostname, persistent agent ID, current IP, network, status, last-seen time and agent version. The Change IP and Open Test controls are present as disabled placeholders. If the agent list cannot be loaded, the page shows a readable error and retries on its next poll.

- **ONLINE**: heartbeat received within the last 15 seconds.
- **OFFLINE**: last heartbeat is older than 15 seconds. The record remains in SQLite.
- **MIGRATING**: reserved for an execution-in-progress state; the Phase 5A internal transition hook can record it, but no network action starts here.
- **MIGRATED**: reserved for a future migration workflow; not set by this phase.
- **ERROR**: reserved for a future management workflow; not set by this phase.

## Phase 4 command queue

The `commands` SQLite table keeps command history and references the registered client by `agent_id`. Each row has a persistent integer ID, structured JSON payload, lifecycle status, timestamps and `attempt_count`. On startup, existing Phase 4 databases are upgraded in place with `attempt_count` defaulting to zero. History is not automatically deleted.

Lifecycle: `PENDING` → `DELIVERED` → `ACKNOWLEDGED` or `FAILED`. If a delivery is not acknowledged before its timeout, the same row returns to `PENDING` inside an atomic SQLite transaction and is redelivered with the same command ID. The next delivery increments `attempt_count`; acknowledgement does not. After the configured maximum delivery attempts have each timed out, the existing row becomes `FAILED` with `Maximum command delivery attempts exceeded`.

The Agent polls with the same `X-Agent-Token` used by registration and heartbeat. The default `COMMAND_DELIVERY_TIMEOUT` is 30 seconds and `COMMAND_MAX_ATTEMPTS` is 3. Set either environment variable to change the values. Simultaneous polls are serialized with SQLite `BEGIN IMMEDIATE`, so one command ID cannot be claimed twice by concurrent poll requests. Acknowledgements are accepted only from the owning Agent and while delivered; repeating an identical terminal acknowledgement is idempotent, which handles a lost HTTP response.

Supported types are `MIGRATE_NETWORK` and `OPEN_TEST`. The constrained development route `POST /api/agents/{agent_id}/commands/test` requires the existing development token and is disabled by default. Set `$env:ENABLE_TEST_COMMAND_API = "true"` in the Controller terminal only when exercising mock commands, and restart Uvicorn. `MIGRATE_NETWORK` requires the strict `target_ip`, `prefix_length`, and `gateway` payload and passes the migration validator; `OPEN_TEST` accepts no payload. The authenticated Agent endpoints are `GET /api/agents/{agent_id}/commands` and `POST /api/agents/{agent_id}/commands/{command_id}/ack`.

The Agent polls after its registration/heartbeat loop. It stores command IDs and outcomes in a small local SQLite ledger (`exam-agent/processed_commands.sqlite3` by default). When a command ID arrives again, the Agent reuses its saved result and resends the acknowledgement without running the handler a second time. If it restarts while a command is only marked `PROCESSING`, it reports that command failed rather than repeat the action. This covers Agent restart after handling but before ACK. `MIGRATE_NETWORK` only logs receipt and acknowledges `mock migration completed`; it does not change Windows networking. `OPEN_TEST` only logs receipt and acknowledges `mock test launch completed`; it does not open a browser. Arbitrary shell commands, PowerShell, URLs, and payload-driven execution are intentionally unsupported. Keep the Controller on the authorized lab network and replace the example shared development token.

Phase 4 still performs no real network configuration changes and opens no browser.

## Migration preflight (Phase 5A)

`POST /api/agents/{agent_id}/migration/preflight` accepts only `target_ip`, `prefix_length`, and `gateway`. It is authenticated and read-only. It returns `allowed`, validation errors/warnings, and the configured `INTERNAL_CONTROLLER_URL`.

`MIGRATE_NETWORK` command creation through the existing constrained test endpoint now requires a strict payload and the same validator. The endpoint remains disabled by default; enable `ENABLE_TEST_COMMAND_API=true` only for authorized development testing. A successful creation reserves the target IP with a database unique index and cross-check triggers, and records `migration_state=PREPARING`, target IP/gateway, and request time. Invalid command creation persists `migration_error`; failed migration ACK or exhausted delivery attempts persist `migration_state=ERROR`. The client remains `ONLINE` on command creation; command creation and mock ACK do not claim a migration occurred. An internal state hook can mark execution as `MIGRATING` for a later phase.

Targets must be valid usable IPv4 addresses in `INTERNAL_NETWORK`, use prefix 24 and exactly `INTERNAL_GATEWAY`, and cannot be the subnet network/broadcast address, gateway, configured internal Controller IP, or a target reserved by another Agent. An Agent with an active migration intent cannot receive another migration command. The Agent does not select targets.

The Agent stores `INTERNAL_CONTROLLER_URL` as configuration, but it does not switch URLs or perform any networking action. Phase 5A implements no `netsh`, PowerShell network configuration, adapter/routing/firewall changes, browser launch, or arbitrary command execution. Real network migration remains out of scope until Phase 5B and topology verification.

## API (Phase 2)

- `POST /api/agents/register`: creates or updates a client by UUID and marks it online.
- `POST /api/agents/heartbeat`: refreshes its hostname, IPv4 address and `last_seen`.
- `GET /api/agents` and `GET /api/agents/{agent_id}`: return persistent client records.

The API computes offline status when a record's last heartbeat is more than 15 seconds old. Offline rows remain stored. A heartbeat or registration immediately returns the row to `ONLINE`.

## Two-PC smoke procedure

On the Controller PC (`192.168.0.10`), create/activate the venv, install requirements, set `AGENT_TOKEN` as above, and run Uvicorn. Permit inbound TCP 8000 from the lab network in Windows Firewall.

On Client PC (`192.168.0.101`):

```powershell
cd exam-agent
python -m venv .venv
.venv\Scripts\activate
$env:CONTROLLER_URL = "http://192.168.0.10:8000"
$env:AGENT_TOKEN = "dev-agent-token-change-me"
$env:HEARTBEAT_INTERVAL = "5"
python -m agent.main
```

From either PC, open `http://192.168.0.10:8000/api/agents`. Within a few seconds it should list the client hostname, `192.168.0.101`, and `ONLINE`. Stop the foreground agent with Ctrl+C. After more than 15 seconds, reload the API and confirm the same record is `OFFLINE`. Start the agent again; it registers with the same UUID from `exam-agent/agent_id` and returns online without adding another record. Do not delete `agent_id` when restarting the process.

To run Controller syntax, command queue, dashboard/API, and Agent mock-handler tests from `exam-controller`, run `python -m compileall -q app tests`, `python -m compileall -q ../exam-agent/agent`, and `python -m pytest -q`.
