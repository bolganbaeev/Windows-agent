# ExamAgent (Phase 5A)

This is a foreground Python application for the authorized lab. It reports its persistent UUID, Windows hostname, local IPv4 addresses, and version to ExamController. It polls the constrained command queue using the existing shared token. It does not run arbitrary commands, change network settings, open a browser, or run as a Windows Service.

## Windows setup

On the client PC (`192.168.0.101`), install Python 3, then run PowerShell:

```powershell
cd exam-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
$env:CONTROLLER_URL = "http://192.168.0.10:8000"
$env:INTERNAL_CONTROLLER_URL = "http://192.168.1.10:8000"
$env:AGENT_TOKEN = "dev-agent-token-change-me"
$env:HEARTBEAT_INTERVAL = "5"
python -m agent.main
```

Set `AGENT_TOKEN` to the same private development token configured on the Controller. `CONTROLLER_URL` defaults to `http://192.168.0.10:8000`; `HEARTBEAT_INTERVAL` defaults to 5 seconds. The generated UUID is stored in `exam-agent\agent_id` and reused after process or PC restarts. Back up that file if the client identity must be preserved.

The process registers at startup, then sends a heartbeat and polls for commands on its configured interval. If registration, heartbeat, polling, or acknowledgement fails, it logs the error and retries on a later loop. Use Ctrl+C to stop it.

## Mock command behavior

The Agent accepts only the structured types `MIGRATE_NETWORK` and `OPEN_TEST`. Both remain mock handlers: `MIGRATE_NETWORK` logs receipt and acknowledges `mock migration completed`; `OPEN_TEST` logs receipt and acknowledges `mock test launch completed`. They do not change Windows networking or launch a browser. Payload fields are ignored, and unknown command types are reported as failed without execution. Arbitrary shell/PowerShell commands and arbitrary URLs are unsupported.

Handled command IDs and their results are stored in `exam-agent\processed_commands.sqlite3` (override with `AGENT_COMMAND_DB`). When a timed-out delivery is resent with the same ID, the Agent reloads the saved result and acknowledges it without running the mock handler again. If the Agent restarts after delivery but before processing, it handles the command once; if it restarts after processing but before ACK, it reuses the stored result. A command interrupted while its ledger state is `PROCESSING` is failed on redelivery instead of being run again.

The Controller defaults to a 30-second delivery timeout (`COMMAND_DELIVERY_TIMEOUT`) and three delivery attempts (`COMMAND_MAX_ATTEMPTS`). Expired deliveries reuse the same command row and ID. If all three deliveries time out, the Controller marks that row `FAILED`; it does not create a replacement command.

## Migration topology configuration

`INTERNAL_CONTROLLER_URL` is stored as configuration only. The Agent continues to contact the configured `CONTROLLER_URL`; it does not automatically switch endpoints, change IP/gateway settings, or alter routes. After a move to an isolated `192.168.1.x` network, the old `192.168.0.10` endpoint may be unreachable. The Controller must be reachable from the internal LAN through a second NIC/address or verified routing. The actual lab topology must be tested before a future execution phase.

Phase 5A only validates migration targets and records Controller-side intent/reservation. No real Windows network change, adapter manipulation, browser launch, or remote command execution is implemented.

See the Controller README for the two-PC online/offline check and constrained development command endpoint. Run the Controller in a foreground terminal during this phase. Do not install a Windows Service.
