import asyncio
import json
import logging
import websockets
from supabase import create_client, Client
import uuid
import os
from dotenv import load_dotenv

# Load variables from .env file
load_dotenv()

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY") # Service role key

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Configure logger
logger = logging.getLogger("MockFaceServer")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

async def process_device_message(message: str, websocket):
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        logger.error("Invalid JSON received.")
        return

    # Don't print the huge base64 image strings in the logs
    log_data = data.copy()
    if 'image' in log_data:
        log_data['image'] = '<base64_image_data>'
    logger.info(f"Received: {log_data}")
    
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
                # Generate a new UUID for this face
                face_uuid = str(uuid.uuid4())
                
                db_res = supabase.table('face').insert({
                    'usr_id': str(usr_id),
                    'usr_name': usr_name,
                    'img_b64': image,
                    'face_uuid': face_uuid,
                    'status': 'pending'
                }).execute()
                logger.info(f"DB enrollment insert successful for usr_id: {usr_id}")
                response = {"ret": "enrollment", "result": True}
            except Exception as e:
                logger.error(f"DB Error on enrollment: {e}")
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
                    db_res = supabase.table('face').update(update_data).eq('usr_id', str(usr_id)).execute()
                    if len(db_res.data) > 0:
                        logger.info(f"DB update successful for usr_id: {usr_id}")
                        response = {"ret": "update", "result": True}
                    else:
                        logger.warning(f"No record found to update for usr_id: {usr_id}")
                        response = {"ret": "update", "result": False, "reason": "user_not_found"}
                except Exception as e:
                    logger.error(f"DB Error on update: {e}")
                    response = {"ret": "update", "result": False, "reason": "db_error"}
            else:
                response = {"ret": "update", "result": False, "reason": "No fields to update"}

    elif cmd == "delete":
        usr_id = data.get("id")
        
        if not usr_id:
            response = {"ret": "delete", "result": False, "reason": "Missing id"}
        else:
            try:
                db_res = supabase.table('face').delete().eq('usr_id', str(usr_id)).execute()
                if len(db_res.data) > 0:
                    logger.info(f"DB delete successful for usr_id: {usr_id}")
                    response = {"ret": "delete", "result": True}
                else:
                    logger.warning(f"No record found to delete for usr_id: {usr_id}")
                    response = {"ret": "delete", "result": False, "reason": "user_not_found"}
            except Exception as e:
                logger.error(f"DB Error on delete: {e}")
                response = {"ret": "delete", "result": False, "reason": "db_error"}

    elif cmd == "sendlog":
        records = data.get("record", [])
        logger.info(f"Received {len(records)} log records to store.")
        response = {"ret": "sendlog", "result": True, "count": len(records)}

    else:
        logger.warning(f"Unknown command: {cmd}")
        response = {"ret": "unknown", "result": False, "reason": "invalid command"}

    if response:
        logger.info(f"Sending response: {response}")
        await websocket.send(json.dumps(response))

async def handler(websocket):
    logger.info("Client connected")
    authenticated = False
    
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                logger.error("Invalid JSON received.")
                await websocket.send(json.dumps({"result": False, "reason": "invalid_json"}))
                continue

            cmd = data.get("cmd")
            
            if not authenticated:
                if cmd == "auth" or cmd == "reg":
                    sn = data.get("sn")
                    cpusn = data.get("cpusn")
                    if sn == "WAC14089464" and cpusn == "CPU123456789":
                        authenticated = True
                        logger.info(f"Client authenticated successfully with SN: {sn}")
                        await websocket.send(json.dumps({"ret": cmd, "result": True}))
                    else:
                        logger.warning(f"Authentication failed. sn: {sn}, cpusn: {cpusn}")
                        await websocket.send(json.dumps({"ret": cmd, "result": False, "reason": "invalid_credentials"}))
                        await websocket.close()
                else:
                    logger.warning(f"Unauthenticated request: {cmd}")
                    await websocket.send(json.dumps({"result": False, "reason": "not_authenticated"}))
                    await websocket.close()
                continue
                
            # If authenticated, process face operations
            await process_device_message(message, websocket)
            
    except websockets.exceptions.ConnectionClosed:
        logger.info("Client disconnected")

async def main():
    logger.info("Starting Mock Face Server on 0.0.0.0:8765")
    async with websockets.serve(handler, "0.0.0.0", 8765):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
