
import asyncio
import json
import logging
import websockets
from typing import Optional, Dict, Any, List

# Configure logger
logger = logging.getLogger("PiHRClient")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)


import asyncio
import json
import logging
import websockets
from typing import Optional, Dict, Any, List, Callable
from collections import defaultdict, deque

# Configure logger
logger = logging.getLogger("PiHRClient")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

class PiHRClient:
    def __init__(self, uri: str):
        self.uri = uri
        self.websocket = None
        self.running = False
        self._pending_responses = defaultdict(deque)  # Map "ret_value" -> deque of futures
        self._listen_task = None
        self.command_handlers: Dict[str, Callable] = {}

    async def connect(self):
        """Establish WebSocket connection and start listener."""
        try:
            logger.info(f"Connecting to {self.uri}...")
            self.websocket = await websockets.connect(self.uri)
            self.running = True
            self._listen_task = asyncio.create_task(self._listen_loop())
            logger.info("Connected successfully.")
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            raise

    async def disconnect(self):
        self.running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self.websocket:
            await self.websocket.close()
            logger.info("Disconnected.")

    async def _listen_loop(self):
        """Background loop to receive messages."""
        logger.info("Listener loop started.")
        while self.running:
            try:
                message = await self.websocket.recv()
                data = json.loads(message)
                logger.debug(f"Received: {data}")
                
                if "ret" in data:
                    # It's a response to our command
                    ret_cmd = data["ret"]
                    if self._pending_responses[ret_cmd]:
                         future = self._pending_responses[ret_cmd].popleft()
                         if not future.done():
                             future.set_result(data)
                    else:
                        logger.warning(f"Received unexpected response for {ret_cmd}: {data}")
                
                elif "cmd" in data:
                    # It's a command from server
                    await self._handle_incoming_command(data)
                
                else:
                    logger.warning(f"Unknown message structure: {data}")

            except websockets.exceptions.ConnectionClosed:
                logger.warning("Connection closed by server.")
                self.running = False
                break
            except Exception as e:
                logger.error(f"Error in listener: {e}")
                # Brief pause to avoid tight error loops
                await asyncio.sleep(0.1)

    async def _send_request(self, cmd: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send a request and wait for the matching response."""
        if not self.websocket:
            raise RuntimeError("WebSocket is not connected.")

        future = asyncio.Future()
        self._pending_responses[cmd].append(future)
        
        try:
            msg = json.dumps(payload)
            logger.debug(f"Sending: {msg}")
            await self.websocket.send(msg)
            # Wait for response with a timeout
            return await asyncio.wait_for(future, timeout=10.0)
        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for response to {cmd}")
            # Remove the future if it's still there
            if future in self._pending_responses[cmd]:
                self._pending_responses[cmd].remove(future)
            raise
        except Exception as e:
            logger.error(f"Error sending request {cmd}: {e}")
            raise

    async def _handle_incoming_command(self, data: Dict[str, Any]):
        cmd = data.get("cmd")
        logger.info(f"Handling incoming command: {cmd}")
        
        reply = None
        
        if cmd == "setuserinfo":
            # Logic to save user info would go here
            reply = {
                "ret": "setuserinfo",
                "sn": "DEVICE_SN", # In real app, this comes from config
                "enrollid": data.get("enrollid"),
                "backupnum": data.get("backupnum"),
                "result": True
            }
        
        elif cmd == "deleteuser":
            # Logic to delete user
            reply = {
                "ret": "deleteuser",
                "sn": "DEVICE_SN",
                "enrollid": data.get("enrollid"),
                "backupnum": data.get("backupnum"),
                "result": True
            }

        # Allow custom handlers injection
        if cmd in self.command_handlers:
             await self.command_handlers[cmd](data)

        if reply:
            await self.websocket.send(json.dumps(reply))

    async def register(self, sn: str, cpusn: str, devinfo: Optional[Dict] = None, nosenduser: bool = True) -> Dict[str, Any]:
        payload = {
            "cmd": "reg",
            "sn": sn,
            "cpusn": cpusn,
            "devinfo": devinfo or {},
            "nosenduser": nosenduser
        }
        return await self._send_request("reg", payload)

    async def send_logs(self, sn: str, records: List[Dict[str, Any]], count: Optional[int] = None, logindex: Optional[int] = None) -> Dict[str, Any]:
        payload = {
            "cmd": "sendlog",
            "sn": sn,
            "record": records
        }
        if count is not None:
            payload["count"] = count
        if logindex is not None:
            payload["logindex"] = logindex
            
        return await self._send_request("sendlog", payload)

    async def send_user(self, sn: str, enrollid: int, name: str, backupnum: int, record: Any, admin: int = 0, imagepath: Optional[str] = None) -> Dict[str, Any]:
        payload = {
            "cmd": "senduser",
            "enrollid": enrollid,
            "name": name,
            "backupnum": backupnum,
            "admin": admin,
            "record": record,
            "imagepath": imagepath
        }
        return await self._send_request("senduser", payload)


