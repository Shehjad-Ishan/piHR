import asyncio
import json
import logging
import websockets

# Configure logger
logger = logging.getLogger("TestFaceConnection")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

async def test_face_pipeline():
    # Update this to the server's IP if running from another PC
    # e.g., uri = "ws://192.168.88.251:8765"
    uri = "ws://192.168.88.251:8765"
    
    logger.info(f"Connecting to {uri}...")
    try:
        async with websockets.connect(uri) as websocket:
            logger.info("Connected successfully.")
            
            # 0. Authenticate
            logger.info("Authenticating...")
            auth_cmd = {
                "cmd": "auth",
                "sn": "WAC14089464",
                "cpusn": "CPU123456789"
            }
            await websocket.send(json.dumps(auth_cmd))
            response = await websocket.recv()
            logger.info(f"Auth Response: {response}")
            
            # 1. Enrollment
            logger.info("Executing Enrollment...")
            enroll_cmd = {
                "cmd": "enrollment",
                "id": "1001",
                "name": "Test User",
                "image": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=" # Dummy 1x1 image
            }
            await websocket.send(json.dumps(enroll_cmd))
            response = await websocket.recv()
            logger.info(f"Enrollment Response: {response}")
            
            await asyncio.sleep(2)
            
            # 2. Update
            logger.info("Executing Update...")
            update_cmd = {
                "cmd": "update",
                "id": "1001",
                "name": "Updated Test User",
                "image": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/w8AAwAB/AL+f4R4AAAAAElFTkSuQmCC" # Different dummy image
            }
            await websocket.send(json.dumps(update_cmd))
            response = await websocket.recv()
            logger.info(f"Update Response: {response}")
            
            await asyncio.sleep(2)
            
            # 3. Delete (Commented out so the dummy user stays in the database)
            # logger.info("Executing Delete...")
            # delete_cmd = {
            #     "cmd": "delete",
            #     "id": "1001"
            # }
            # await websocket.send(json.dumps(delete_cmd))
            # response = await websocket.recv()
            # logger.info(f"Delete Response: {response}")
            
    except Exception as e:
        logger.error(f"Test failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_face_pipeline())
