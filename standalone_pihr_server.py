import asyncio
import json
import logging
import websockets

# Configure logger
logger = logging.getLogger("TestPiHRServer")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

async def handler(websocket):
    client_ip = websocket.remote_address[0]
    logger.info(f"Client connected from {client_ip}")
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
            
            # 1. Handle Authentication
            if not authenticated:
                if cmd in ["auth", "reg"]:
                    sn = data.get("sn")
                    cpusn = data.get("cpusn")
                    logger.info(f"Auth request received - SN: {sn}, CPUSN: {cpusn}")
                    
                    # For testing, we accept any SN/CPUSN, or you can restrict it here
                    authenticated = True
                    logger.info(f"Client {client_ip} authenticated successfully.")
                    await websocket.send(json.dumps({"ret": cmd, "result": True}))
                else:
                    logger.warning(f"Unauthenticated request: {cmd}")
                    await websocket.send(json.dumps({"result": False, "reason": "not_authenticated"}))
                    await websocket.close()
                continue
                
            # 2. Handle Data Sync (Authenticated)
            if cmd == "sendlog":
                records = data.get("record", [])
                logger.info("=========================================")
                logger.info(f"📥 RECEIVED ATTENDANCE LOG ({len(records)} records)")
                logger.info(f"Payload: {json.dumps(data, indent=2)}")
                logger.info("=========================================")
                
                # Acknowledge receipt back to the client so it updates status to '1'
                response = {"ret": "sendlog", "result": True, "count": len(records)}
                await websocket.send(json.dumps(response))
                
            else:
                logger.warning(f"Unknown command received: {cmd}")
                logger.info(f"Payload: {data}")
                response = {"ret": "unknown", "result": False, "reason": "invalid command"}
                await websocket.send(json.dumps(response))
            
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"Client {client_ip} disconnected")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")

async def main():
    port = 8765
    logger.info("=========================================")
    logger.info(f"🚀 Starting Standalone Test PiHR Server")
    logger.info(f"Listening on all interfaces (0.0.0.0) on port {port}")
    logger.info("Ready to receive JSON payloads...")
    logger.info("=========================================")
    
    # 0.0.0.0 allows connections from other PCs on the network
    async with websockets.serve(handler, "0.0.0.0", port):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server manually stopped.")
