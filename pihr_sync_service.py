import asyncio
import json
import logging
import os
from datetime import datetime, timezone
import pytz
from dotenv import load_dotenv
from supabase._async.client import create_client as create_async_client, AsyncClient

from pihr_client import PiHRClient

# Configure logger
logger = logging.getLogger("PiHRSyncService")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

# Load variables from .env file
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# For testing, we connect to the mock piHR server locally.
PIHR_WS_URI = os.getenv("PIHR_WS_URI", "ws://127.0.0.1:8765")
DEVICE_SN = "WAC14089464"
DEVICE_CPUSN = "CPU123456789"

supabase: AsyncClient = None
sync_queue = asyncio.Queue()

def datetime_to_string(dt_str):
    """Convert DB ISO string to the format expected by piHR (typically YYYY-MM-DD HH:MM:SS)"""
    if not dt_str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        # e.g., "2024-03-14T15:30:00+00:00", we assume backend wants local string or UTC string
        # The prompt says Watchcam puts UTC string or Asia/Dhaka time. We just strip T and timezone for simplicity.
        dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return dt_str

async def process_attendance_record(pihr_client: PiHRClient, record: dict):
    try:
        emp_id = record.get("employee_id")
        rec_id = record.get("id")
        rec_time = datetime_to_string(record.get("time"))
        
        # Build the 'record' array for the sendlog payload
        # Standard attendance log format
        log_records = [{
            "enrollid": int(emp_id) if emp_id and emp_id.isdigit() else emp_id,
            "time": rec_time,
            "mode": 1,  # 1 typically means face recognition mode
            "inout": 0, # Depending on config
            "event": 0,
            "temp": 0.0
        }]
        
        logger.info(f"Syncing attendance for employee {emp_id} (ID: {rec_id}) at {rec_time}")
        
        # Send log via WebSocket
        response = await pihr_client.send_logs(sn=DEVICE_SN, records=log_records)
        
        if response and response.get("result", False):
            logger.info(f"Successfully synced ID {rec_id}. Updating DB status to '1'.")
            # Update the status in the database to 1
            await supabase.table("recognized_faces").update({"status": "1"}).eq("id", rec_id).execute()
        else:
            logger.error(f"Failed to sync ID {rec_id}. PiHR response: {response}")
            
    except Exception as e:
        logger.error(f"Error processing record {record.get('id')}: {e}")

async def sync_worker(pihr_client: PiHRClient):
    """Background worker that pulls records from the queue and sends them via WebSocket."""
    logger.info("Sync worker started.")
    while True:
        record = await sync_queue.get()
        await process_attendance_record(pihr_client, record)
        sync_queue.task_done()

def on_realtime_insert(payload):
    """Callback for Supabase Realtime insertions."""
    record = payload.get("record", {})
    if record.get("status") == "0":
        logger.info(f"Realtime: New recognized face (ID: {record.get('id')}). Adding to queue.")
        # We must push to the asyncio queue from this thread safely
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(sync_queue.put_nowait, record)
        except Exception as e:
            logger.error(f"Failed to enqueue realtime event: {e}")

async def authenticate_with_pihr(pihr_client: PiHRClient) -> bool:
    """Manually send auth command and handle response."""
    try:
        response = await pihr_client.register(sn=DEVICE_SN, cpusn=DEVICE_CPUSN)
        if response and response.get("result", False):
            logger.info("Successfully authenticated with PiHR WebSocket.")
            return True
        else:
            logger.error(f"Authentication failed: {response}")
            return False
    except asyncio.TimeoutError:
         logger.error("Timeout during authentication.")
         return False

async def catch_up_missed_records():
    """Fetches any existing records with status='0' that were missed while the service was down."""
    logger.info("Checking for missed records in DB...")
    # Using pagination if many records exist, but for now just grab all
    try:
        response = await supabase.table("recognized_faces").select("*").eq("status", "0").execute()
        records = response.data
        if records:
            logger.info(f"Found {len(records)} pending records to sync.")
            for record in records:
                await sync_queue.put(record)
        else:
            logger.info("No missed records found.")
    except Exception as e:
        logger.error(f"Failed to fetch missed records: {e}")

async def main():
    global supabase
    logger.info("Starting PiHR Sync Service")
    
    supabase = await create_async_client(SUPABASE_URL, SUPABASE_KEY)
    pihr_client = PiHRClient(PIHR_WS_URI)
    
    # 1. Connect to PiHR
    try:
        await pihr_client.connect()
    except Exception as e:
        logger.error("Exiting: Could not connect to PiHR WebSocket.")
        return
        
    # 2. Authenticate
    is_authenticated = await authenticate_with_pihr(pihr_client)
    if not is_authenticated:
        logger.error("Authentication failed. Ensure SN/CPUSN are correct. Exiting.")
        await pihr_client.disconnect()
        return

    # 3. Start worker tasks
    # Run the worker to process the queue
    worker_task = asyncio.create_task(sync_worker(pihr_client))
    
    # 4. Catch up any missed records
    await catch_up_missed_records()
    
    # 5. Poll for new records every few seconds
    POLL_INTERVAL = 3  # seconds
    logger.info(f"Starting polling loop (every {POLL_INTERVAL}s) for new recognized_faces...")
    
    try:
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            try:
                response = await supabase.table("recognized_faces") \
                    .select("*") \
                    .eq("status", "0") \
                    .order("id") \
                    .limit(100) \
                    .execute()
                records = response.data
                if records:
                    logger.info(f"Poll: Found {len(records)} new records to sync.")
                    for record in records:
                        await sync_queue.put(record)
            except Exception as e:
                logger.error(f"Polling error: {e}")
    except asyncio.CancelledError:
        logger.info("Shutting down...")
    finally:
        worker_task.cancel()
        await pihr_client.disconnect()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Service stopped by user.")
