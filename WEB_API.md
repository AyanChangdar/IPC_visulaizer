# IPC Nexus Web API

Run these commands from the repository root in PowerShell. If the virtual
environment already exists, start with the dependency installation.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe web_server.py
```

Open [http://localhost:8000](http://localhost:8000). The server binds to
`127.0.0.1:8000` and runs one Uvicorn worker. Keep a single server worker so
all browser tabs control the same simulation. The desktop application
continues to start with `python main.py`.

The root page is `web/index.html`. Put browser assets in `web/`, use relative
URLs for them, and use same-origin requests to the API. For example,
`web/styles.css` is available at `/styles.css`. REST OpenAPI documentation is
available at `/docs`.

## REST

Send JSON request bodies with `Content-Type: application/json`. Every
successful request returns HTTP 200 and a status object:

```json
{
  "running": true,
  "processes": 4,
  "method": "pipe",
  "deadlock_armed": false,
  "available_methods": ["pipe", "queue", "shared_memory"]
}
```

`processes` counts configured workers. Consult the process metrics for
liveness. While stopped, `method` is null and `processes` is zero.

| Method and path | Request body | Behavior |
| --- | --- | --- |
| `GET /api/status` | None | Read the current simulation state and IPC methods. |
| `POST /api/start` | `{"processes":4,"method":"pipe"}` | Start 1 to 20 workers. One worker can be monitored and has no peer for IPC. An armed demo requires at least four workers and `shared_memory`. |
| `POST /api/stop` | None | Stop and reap workers, clear live metrics and alerts, and disarm a running demo. An already stopped simulation is unchanged. |
| `POST /api/deadlock` | `{"armed":true}` or `{"armed":false}` | Set the deadlock demo flag while stopped. Start launches the armed demo. |

A start or deadlock change during a run returns **409**. Invalid JSON,
invalid fields and incompatible demo settings return **422**. Cross-origin
browser writes return **403**. Unexpected command failures return **500**.
Errors have a `detail` string, or FastAPI's `detail` array for request
validation errors. Each array entry includes `loc`, `msg`, and `type`.

## WebSocket

Connect to `/ws` on the page's host. Use REST for commands. Each text frame
is one JSON object with `type` and `data`:

```json
{
  "type": "message_delivered",
  "data": {
    "src": 0,
    "dst": 2,
    "message": "Hello from P0",
    "timestamp": 1795000000.123,
    "latency_ms": 8.3
  }
}
```

| Event type | Fields in `data` |
| --- | --- |
| `simulation_status` | Same fields as the REST status object. Emitted when workers start or stop, or when the demo flag changes. |
| `process_metrics` | `processes` is a complete array of records. Each has `index` (logical integer), `pid` (OS integer), `cpu` (per-process utilization percent or null), `memory` (resident memory in MiB or null), `status` (psutil status string or null), and `alive` (boolean). Emitted on start, about once per second while running, and with an empty array on Stop. An externally exited worker stays in the array with `alive: false` until Stop. |
| `message_delivered` | `src` and `dst` (logical integers), `message` (string), `timestamp` (sender's Unix epoch seconds), `latency_ms` (number). Emitted on a successful receive. |
| `performance` | `delivered` (integer count), `throughput` (average messages per second since start), and `average_latency_ms` (number). Idle values are zero. |
| `deadlock` | `armed` and `detected` (booleans), `blocked` (logical integer array), `alert` (string or null), `locks` (mapping from lock name to `{"owner":2,"waiters":[3]}`, owner may be null), and `resource` (details below, or null). |
| `log` | `kind` and `message` (strings). `process_started` includes integer `index` and `pid`. `process_exited` also includes integer `exit_code`. `worker` contains worker timeline text. |

A deadlock resource event has `resource` equal to
`{"action":"acquired","index":2,"lock":"A","held":null}` or
`{"action":"waiting","index":2,"lock":"B","held":"A"}`. `locks` contains
the live owners and waiters observed by the backend. Demo locks appear only
during the active demo. Resource events retain their observed order. Their
state fields reflect the latest state in the current batch of worker events.
Use `blocked` and `detected` for the demo indicator. The OS status can
still report a blocked worker as running.

New connections receive status, process metrics, performance and deadlock
snapshots before live events. There is no replay of historical messages or
logs. Reconnect to obtain fresh snapshots. Each connection has a bounded
queue. A client that cannot keep up may be closed with code 1013 and should
reconnect. CPU is null until there are two samples, and unavailable OS fields
are also null.

## Tests

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

The web tests use a real Uvicorn listener on a temporary loopback port and
real worker processes.
