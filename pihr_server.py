"""
PiHR Server — Combined WebSocket Server + Attendance Sync Service

This single script runs two concurrent tasks:
  1. WebSocket Server (port 8765): Handles face enrollment/update/delete/sendlog
     commands from the C++ Watchcam application.
  2. Attendance Sync Loop: Polls the recognized_faces table every 3 seconds for
     new records (status='0'), sends them to a remote PiHR server via WebSocket,
     and updates status to '1' on success.

Usage:
    python3 pihr_server.py
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime

import websockets
from dotenv import load_dotenv
from supabase import create_client, Client
from supabase._async.client import create_client as create_async_client, AsyncClient

from pihr_client import PiHRClient

# ── Load .env ────────────────────────────────────────────────────────────────
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Remote PiHR server to forward attendance logs to
PIHR_WS_URI = os.getenv("PIHR_WS_URI", "ws://192.168.88.252:8765")
DEVICE_SN = os.getenv("DEVICE_SN", "WAC14089464")
DEVICE_CPUSN = os.getenv("SECRET_KEY", "CPU123456789")

SERVER_PORT = int(os.getenv("PIHR_SERVER_PORT", "8765"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "3"))  # seconds

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger_server = logging.getLogger("PiHR-Server")
logger_sync   = logging.getLogger("PiHR-Sync")

# Sync Supabase client (for the WebSocket server handler — blocking calls are fine here)
supabase_sync: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Async Supabase client (for the polling sync loop)
supabase_async: AsyncClient = None


# ═══════════════════════════════════════════════════════════════════════════════
# PART 1: WebSocket Server (handles C++ app ⟶ face enrollment/update/delete)
# ═══════════════════════════════════════════════════════════════════════════════

async def process_device_message(message: str, websocket):
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        logger_server.error("Invalid JSON received.")
        return

    # Don't print the huge base64 image strings in the logs
    log_data = data.copy()
    if 'image' in log_data:
        log_data['image'] = '<base64_image_data>'
    logger_server.info(f"Received: {log_data}")

    cmd = data.get("cmd")
    response = None

    if cmd == "enrollment":
        usr_id = data.get("id")
        usr_name = data.get("name")
        image = data.get("image")

        if not usr_id:
            response = {"ret": "enrollment", "result": False, "reason": "Missing id"}
        else:
            try:
                face_uuid = str(uuid.uuid4())
                supabase_sync.table('face').insert({
                    'usr_id': str(usr_id),
                    'usr_name': usr_name,
                    'img_b64': image,
                    'face_uuid': face_uuid,
                    'status': 'pending'
                }).execute()
                logger_server.info(f"DB enrollment insert successful for usr_id: {usr_id}")
                response = {"ret": "enrollment", "result": True}
            except Exception as e:
                logger_server.error(f"DB Error on enrollment: {e}")
                response = {"ret": "enrollment", "result": False, "reason": "db_error"}

    elif cmd == "update":
        usr_id = data.get("id")
        usr_name = data.get("name")
        image = data.get("image")

        if not usr_id:
            response = {"ret": "update", "result": False, "reason": "Missing id"}
        else:
            update_data = {}
            if usr_name is not None:
                update_data['usr_name'] = usr_name
            if image is not None:
                update_data['img_b64'] = image

            if update_data:
                try:
                    db_res = supabase_sync.table('face').update(update_data).eq('usr_id', str(usr_id)).execute()
                    if len(db_res.data) > 0:
                        logger_server.info(f"DB update successful for usr_id: {usr_id}")
                        response = {"ret": "update", "result": True}
                    else:
                        response = {"ret": "update", "result": False, "reason": "user_not_found"}
                except Exception as e:
                    logger_server.error(f"DB Error on update: {e}")
                    response = {"ret": "update", "result": False, "reason": "db_error"}
            else:
                response = {"ret": "update", "result": False, "reason": "No fields to update"}

    elif cmd == "delete":
        usr_id = data.get("id")
        if not usr_id:
            response = {"ret": "delete", "result": False, "reason": "Missing id"}
        else:
            try:
                # 1. Fetch img_id from face table using usr_id
                face_res = supabase_sync.table('face').select('img_id').eq('usr_id', str(usr_id)).execute()
                if face_res.data:
                    img_id = face_res.data[0]['img_id']
                    
                    # 2. Delete all records from recognized_faces where img_id_fk = img_id
                    rec_res = supabase_sync.table('recognized_faces').delete().eq('img_id_fk', img_id).execute()
                    logger_server.info(f"Deleted {len(rec_res.data)} recognized faces for img_id: {img_id}")
                    
                    # 3. Finally, delete the face from the face table
                    db_res = supabase_sync.table('face').delete().eq('usr_id', str(usr_id)).execute()
                    if len(db_res.data) > 0:
                        logger_server.info(f"DB delete successful for usr_id: {usr_id}")
                        response = {"ret": "delete", "result": True}
                    else:
                        response = {"ret": "delete", "result": False, "reason": "Failed to delete face"}
                else:
                    response = {"ret": "delete", "result": False, "reason": "user_not_found"}
            except Exception as e:
                logger_server.error(f"DB Error on delete: {e}")
                response = {"ret": "delete", "result": False, "reason": "db_error"}

    elif cmd == "sendlog":
        records = data.get("record", [])
        logger_server.info(f"Received {len(records)} log records.")
        response = {"ret": "sendlog", "result": True, "count": len(records)}

    else:
        logger_server.warning(f"Unknown command: {cmd}")
        response = {"ret": "unknown", "result": False, "reason": "invalid command"}

    if response:
        logger_server.info(f"Sending response: {response}")
        await websocket.send(json.dumps(response))


async def ws_handler(websocket):
    client_ip = websocket.remote_address[0]
    logger_server.info(f"Client connected from {client_ip}")
    authenticated = False

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"result": False, "reason": "invalid_json"}))
                continue

            cmd = data.get("cmd")

            if not authenticated:
                if cmd in ("auth", "reg"):
                    sn = data.get("sn")
                    cpusn = data.get("cpusn")
                    if sn == DEVICE_SN and cpusn == DEVICE_CPUSN:
                        authenticated = True
                        logger_server.info(f"Client {client_ip} authenticated (SN: {sn})")
                        await websocket.send(json.dumps({"ret": cmd, "result": True}))
                    else:
                        logger_server.warning(f"Auth failed from {client_ip}")
                        await websocket.send(json.dumps({"ret": cmd, "result": False, "reason": "invalid_credentials"}))
                        await websocket.close()
                else:
                    await websocket.send(json.dumps({"result": False, "reason": "not_authenticated"}))
                    await websocket.close()
                continue

            await process_device_message(message, websocket)

    except websockets.exceptions.ConnectionClosed:
        logger_server.info(f"Client {client_ip} disconnected")


# ═══════════════════════════════════════════════════════════════════════════════
# PART 2: Attendance Sync (polls DB ⟶ sends to remote PiHR via WebSocket)
# ═══════════════════════════════════════════════════════════════════════════════

def datetime_to_string(dt_str):
    if not dt_str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return dt_str


async def sync_one_record(pihr_client: PiHRClient, record: dict):
    """Send a single attendance record to remote PiHR and mark it as synced."""
    try:
        emp_id = record.get("employee_id")
        rec_id = record.get("id")
        rec_time = datetime_to_string(record.get("time"))

        log_records = [{
            "enrollid": int(emp_id) if emp_id and str(emp_id).isdigit() else emp_id,
            "name": "",
            "time": rec_time,
            "mode": 1,
            "inout": 0,
            "event": 0,
            "temp": 0.0
        }]
        
        # Look up the employee's name from the face table
        if emp_id:
            try:
                name_res = await supabase_async.table('face').select('usr_name').eq('usr_id', str(emp_id)).execute()
                if name_res.data and len(name_res.data) > 0:
                    fetched_name = name_res.data[0].get('usr_name')
                    if fetched_name:
                        log_records[0]["name"] = fetched_name
            except Exception as e:
                logger_sync.error(f"Failed to fetch employee name for {emp_id}: {e}")

        logger_sync.info(f"Syncing employee {emp_id} (ID: {rec_id}) at {rec_time}")
        response = await pihr_client.send_logs(sn=DEVICE_SN, records=log_records)

        if response and response.get("result", False):
            logger_sync.info(f"✓ Synced ID {rec_id}")
            await supabase_async.table("recognized_faces").update({"status": "1"}).eq("id", rec_id).execute()
        else:
            logger_sync.error(f"✗ Failed ID {rec_id}: {response}")
    except Exception as e:
        logger_sync.error(f"Error syncing record {record.get('id')}: {e}")


async def attendance_sync_loop(pihr_client: PiHRClient):
    """Continuously poll for new attendance records and forward them."""
    global supabase_async
    supabase_async = await create_async_client(SUPABASE_URL, SUPABASE_KEY)

    # Connect & authenticate with remote PiHR
    try:
        await pihr_client.connect()
    except Exception as e:
        logger_sync.error(f"Cannot connect to remote PiHR at {PIHR_WS_URI}: {e}")
        logger_sync.warning("Attendance sync disabled. Server-only mode.")
        return

    try:
        resp = await pihr_client.register(sn=DEVICE_SN, cpusn=DEVICE_CPUSN)
        if not (resp and resp.get("result", False)):
            logger_sync.error(f"Remote PiHR auth failed: {resp}")
            await pihr_client.disconnect()
            logger_sync.warning("Attendance sync disabled. Server-only mode.")
            return
        logger_sync.info("Authenticated with remote PiHR server.")
    except asyncio.TimeoutError:
        logger_sync.error("Remote PiHR auth timeout.")
        await pihr_client.disconnect()
        logger_sync.warning("Attendance sync disabled. Server-only mode.")
        return

    logger_sync.info(f"Polling every {POLL_INTERVAL}s for new recognized_faces...")

    while True:
        try:
            response = await supabase_async.table("recognized_faces") \
                .select("*") \
                .eq("status", "0") \
                .order("id") \
                .limit(100) \
                .execute()
            records = response.data
            if records:
                logger_sync.info(f"Found {len(records)} records to sync.")
                for record in records:
                    await sync_one_record(pihr_client, record)
        except Exception as e:
            logger_sync.error(f"Poll error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN — run both concurrently
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    logger_server.info(f"Starting PiHR Server on 0.0.0.0:{SERVER_PORT}")
    logger_sync.info(f"Attendance sync target: {PIHR_WS_URI}")

    pihr_client = PiHRClient(PIHR_WS_URI)

    # Start WebSocket server
    server = await websockets.serve(ws_handler, "0.0.0.0", SERVER_PORT)

    # Start attendance sync loop as a background task
    sync_task = asyncio.create_task(attendance_sync_loop(pihr_client))

    # Run forever
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        sync_task.cancel()
        server.close()
        await pihr_client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped.")
