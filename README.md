# PiHR — Face Attendance Sync System

A WebSocket-based system that bridges the **Watchcam C++ face recognition application** with a **remote PiHR attendance server**. It handles face enrollment, updates, deletions, and continuously syncs new attendance records from a Supabase database to the PiHR server.

---

## Architecture

```
┌──────────────────┐       WebSocket        ┌──────────────────────┐       WebSocket       ┌──────────────────┐
│  C++ Watchcam    │ ───────────────────────►│   pihr_server.py     │ ─────────────────────►│  Remote PiHR     │
│  Application     │  enrollment/update/     │  (This Machine)      │  sendlog (attendance) │  Server          │
│                  │  delete/sendlog         │                      │                       │  (192.168.88.252)│
└──────────────────┘                        └──────────┬───────────┘                       └──────────────────┘
                                                       │
                                                       │ read/write
                                                       ▼
                                              ┌──────────────────┐
                                              │    Supabase DB   │
                                              │  ┌────────────┐  │
                                              │  │   face      │  │
                                              │  ├────────────┤  │
                                              │  │recognized  │  │
                                              │  │  _faces    │  │
                                              │  └────────────┘  │
                                              └──────────────────┘
```

**`pihr_server.py`** runs two concurrent tasks:

1. **WebSocket Server** (port `8765`) — Receives face commands (`enrollment`, `update`, `delete`, `sendlog`) from the C++ Watchcam app and persists them to Supabase.
2. **Attendance Sync Loop** — Polls the `recognized_faces` table every 3 seconds for new records (`status='0'`), forwards them to the remote PiHR server via WebSocket, and marks them as synced (`status='1'`).

---

## File Overview

| File | Purpose |
|------|---------|
| `pihr_server.py` | **Main script** — Combined WebSocket server + attendance sync service |
| `pihr_client.py` | Reusable async WebSocket client library for communicating with PiHR servers |
| `standalone_pihr_server.py` | Lightweight test receiver — run on the remote PC to verify payloads |
| `install_pihr_service.py` | Installer script to deploy `standalone_pihr_server.py` as a `systemd` service |
| `mock_pihr_server.py` | *(Legacy)* Original mock server used during development |
| `pihr_sync_service.py` | *(Legacy)* Original standalone sync service before merge |
| `test_pihr_connection.py` | Quick connection test script |
| `.env` | Environment variables for credentials and configuration |

---

## Prerequisites

- **Python 3.8+**
- **Supabase** (self-hosted or cloud) with the `face` and `recognized_faces` tables
- Required Python packages:

```bash
pip install websockets python-dotenv supabase
```

---

## Configuration

All configuration is managed via the `.env` file in the project root:

```ini
# Supabase connection
SUPABASE_URL="http://127.0.0.1:54321"
SUPABASE_KEY="your_supabase_key_here"

# Remote PiHR server URI (where attendance logs are forwarded)
PIHR_WS_URI="ws://192.168.88.252:8765"

# Device serial number (used for WebSocket authentication)
DEVICE_SN="WAC14089464"

# Secret key (CPUSN — used for device authentication handshake)
SECRET_KEY="CPU123456789"
```

| Variable | Description | Default |
|----------|-------------|---------|
| `SUPABASE_URL` | Supabase REST API URL | *required* |
| `SUPABASE_KEY` | Supabase API key | *required* |
| `PIHR_WS_URI` | Remote PiHR WebSocket URI | `ws://192.168.88.252:8765` |
| `DEVICE_SN` | Device serial number for auth | `WAC14089464` |
| `SECRET_KEY` | Device CPUSN for auth handshake | `CPU123456789` |
| `PIHR_SERVER_PORT` | Local WebSocket server port | `8765` |
| `POLL_INTERVAL` | DB polling interval (seconds) | `3` |

---

## Usage

### Running the Main Server

```bash
cd piHR
python3 pihr_server.py
```

This starts both the WebSocket server and the attendance sync loop. You should see output like:

```
PiHR-Server - INFO - Starting PiHR Server on 0.0.0.0:8765
PiHR-Sync   - INFO - Authenticated with remote PiHR server.
PiHR-Sync   - INFO - Polling every 3s for new recognized_faces...
```

> **Note:** If the remote PiHR server is unreachable, the WebSocket server will still run normally — attendance sync is simply disabled gracefully.

---

## WebSocket Commands

The server accepts the following JSON commands from the C++ Watchcam application:

### Authentication (required first)

```json
{ "cmd": "reg", "sn": "WAC14089464", "cpusn": "CPU123456789" }
```

Response: `{ "ret": "reg", "result": true }`

### Face Enrollment

```json
{ "cmd": "enrollment", "id": "123", "name": "John Doe", "image": "<base64_data>" }
```

### Face Update

```json
{ "cmd": "update", "id": "123", "name": "John Updated", "image": "<base64_data>" }
```

### Face Delete

Automatically deletes all related records from `recognized_faces` (via `img_id_fk`) before deleting the face entry.

```json
{ "cmd": "delete", "id": "123" }
```

### Send Log

```json
{
  "cmd": "sendlog",
  "sn": "WAC14089464",
  "record": [
    { "enrollid": 123, "name": "John Doe", "time": "2026-02-24 12:00:00", "mode": 1, "inout": 0, "event": 0, "temp": 0.0 }
  ]
}
```

---

## Attendance Sync Flow

1. The C++ app recognizes a face and inserts a row into `recognized_faces` with `status='0'`.
2. The sync loop picks it up within `POLL_INTERVAL` seconds.
3. The employee's `usr_name` is fetched from the `face` table.
4. A `sendlog` payload (with `enrollid`, `name`, and `time`) is sent to the remote PiHR server.
5. On success, `status` is updated to `'1'` in the database.

---

## Database Schema

### `face` table

| Column | Type | Description |
|--------|------|-------------|
| `img_id` | integer | Primary key |
| `usr_id` | varchar | User/employee ID |
| `usr_name` | varchar | Employee name |
| `img_b64` | text | Base64-encoded face image |
| `face_uuid` | uuid | Unique face identifier |
| `status` | varchar | Enrollment status |

### `recognized_faces` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | integer | Primary key |
| `employee_id` | varchar | Recognized employee ID |
| `time` | timestamp | Recognition timestamp |
| `status` | varchar | `'0'` = pending, `'1'` = synced |
| `img_id_fk` | integer | Foreign key → `face.img_id` |
| `face_uuid_fk` | uuid | Foreign key → `face.face_uuid` |
| `camera_no` | varchar | Source camera identifier |
| `confidence` | varchar | Recognition confidence score |

---

## Deploying the Receiver on a Remote PC

To run the lightweight receiver (`standalone_pihr_server.py`) as a permanent background service on the remote PiHR machine (e.g., `192.168.88.252`):

### Step 1: Copy files to the remote PC

Copy `standalone_pihr_server.py` and `install_pihr_service.py` to the remote machine.

### Step 2: Run the installer

```bash
sudo python3 install_pihr_service.py
```

This will:
- Install `python3-websockets`
- Copy the server script to `/opt/pihr_server/`
- Create a `systemd` service (`pihr-receiver.service`)
- Enable auto-start on boot
- Start the service immediately

### Step 3: Manage the service

```bash
# Check status
sudo systemctl status pihr-receiver.service

# View live logs
sudo journalctl -u pihr-receiver.service -f

# Restart
sudo systemctl restart pihr-receiver.service

# Stop
sudo systemctl stop pihr-receiver.service
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `Address already in use` on port 8765 | Run `pkill -f "python3 pihr_server.py"` then restart |
| Attendance sync disabled | Remote PiHR server is unreachable — check `PIHR_WS_URI` and network connectivity |
| Face delete fails with FK error | This is handled automatically — `recognized_faces` rows are deleted first |
| Auth fails on connect | Verify `DEVICE_SN` and `SECRET_KEY` in `.env` match the server's expected values |
