#!/usr/bin/env python3
import os
import sys
import subprocess
import shutil

SERVICE_NAME = "pihr-receiver.service"
SERVICE_FILE_PATH = f"/etc/systemd/system/{SERVICE_NAME}"
INSTALL_DIR = "/opt/pihr_server"
SCRIPT_NAME = "standalone_pihr_server.py"

SERVICE_CONTENT = f"""[Unit]
Description=PiHR Standalone WebSocket Receiver Service
After=network.target

[Service]
User=root
WorkingDirectory={INSTALL_DIR}
ExecStart=/usr/bin/python3 {INSTALL_DIR}/{SCRIPT_NAME}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""

def run_command(cmd, shell=False):
    print(f"Running: {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True, shell=shell)
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {e}")
        sys.exit(1)

def main():
    if os.geteuid() != 0:
        print("This script must be run as root. Try running: sudo python3 install_pihr_service.py")
        sys.exit(1)
        
    print("=== Installing PiHR Receiver Service ===")
    
    # 1. Install dependencies
    print("\n[1/5] Installing dependencies (python3-websockets)...")
    run_command(["apt", "update"])
    run_command(["apt", "install", "-y", "python3-websockets"])
    
    # 2. Create directory and copy script
    print(f"\n[2/5] Creating installation directory at {INSTALL_DIR}...")
    os.makedirs(INSTALL_DIR, exist_ok=True)
    
    if not os.path.exists(SCRIPT_NAME):
        print(f"Error: {SCRIPT_NAME} not found in the current directory.")
        print("Please run this script from the directory containing standalone_pihr_server.py")
        sys.exit(1)
        
    print(f"Copying {SCRIPT_NAME} to {INSTALL_DIR}...")
    shutil.copy2(SCRIPT_NAME, os.path.join(INSTALL_DIR, SCRIPT_NAME))
    
    # 3. Create systemd service file
    print(f"\n[3/5] Creating systemd service file at {SERVICE_FILE_PATH}...")
    with open(SERVICE_FILE_PATH, "w") as f:
        f.write(SERVICE_CONTENT)
        
    # 4. Reload and enable systemd
    print("\n[4/5] Reloading systemd daemon and enabling service...")
    run_command(["systemctl", "daemon-reload"])
    run_command(["systemctl", "enable", SERVICE_NAME])
    
    # 5. Start the service
    print("\n[5/5] Starting the PiHR receiver service...")
    run_command(["systemctl", "start", SERVICE_NAME])
    
    print("\n=== Installation Complete! ===")
    print("The PiHR receiver is now running as a background service.")
    print("To check the status, run:")
    print(f"  sudo systemctl status {SERVICE_NAME}")
    print("\nTo view the live streaming JSON logs, run:")
    print(f"  sudo journalctl -u {SERVICE_NAME} -f")

if __name__ == "__main__":
    main()
