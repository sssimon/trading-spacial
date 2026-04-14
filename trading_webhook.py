#!/usr/bin/env python3
"""
Webhook receiver for BTC Scanner.
Listens on port 9000, forwards signals to Telegram via OpenClaw.
Uses the preformatted telegram_message from the scanner payload.
"""

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import subprocess
import sys

# Configuration
PORT = 9000
_DIR     = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(_DIR, "logs", "webhook.log")
os.makedirs(os.path.join(_DIR, "logs"), exist_ok=True)

def load_config():
    """Load config.json if it exists."""
    cfg_path = os.path.join(_DIR, "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            if 'logger' in globals():
                logger.error(f"Error loading config.json: {e}")
    return {}

def _get_telegram_target():
    cfg = load_config()
    return cfg.get("telegram_chat_id", "").strip() or None

def _get_openclaw_cmd():
    cfg = load_config()
    configured = cfg.get("openclaw_path", "").strip()
    if configured and os.path.isfile(configured):
        return configured
    import shutil
    return shutil.which("openclaw") or "openclaw"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            LOG_FILE,
            maxBytes=5 * 1024 * 1024,  # 5 MB per file
            backupCount=5,              # keep 5 old files
            encoding="utf-8",
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/webhook':
            self.send_error(404, "Endpoint not found")
            return
        
        # Security: Validate secret if configured
        cfg = load_config()
        secret = cfg.get("webhook_secret", "").strip()
        if secret:
            received_secret = self.headers.get("X-Scanner-Secret", "").strip()
            import hmac
            if not hmac.compare_digest(received_secret, secret):
                logger.warning(f"Unauthorized webhook attempt from {self.address_string()}")
                self.send_error(401, "Unauthorized: Invalid secret")
                return

        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            self.send_error(400, "Empty body")
            return
        
        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body.decode('utf-8'))
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return
        
        logger.info(f"Received webhook payload with keys: {list(payload.keys())}")
        
        # Extract the preformatted telegram message
        telegram_message = payload.get('telegram_message')
        if not telegram_message:
            logger.warning("No telegram_message in payload, constructing fallback")
            telegram_message = construct_fallback_message(payload)
        
        # Send via OpenClaw
        success = send_via_openclaw(telegram_message)
        
        if success:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        else:
            self.send_error(500, "Failed to send via OpenClaw")
    
    def log_message(self, format, *args):
        logger.info("%s - %s" % (self.address_string(), format % args))

def construct_fallback_message(payload):
    """Create a basic message if telegram_message is missing."""
    lines = []
    lines.append("🚨 BTC Scanner Signal")
    lines.append(f"Scan ID: {payload.get('scan_id', 'N/A')}")
    lines.append(f"Timestamp: {payload.get('timestamp', 'N/A')}")
    lines.append(f"Estado: {payload.get('estado', 'N/A')}")
    lines.append(f"Price: {payload.get('price', 'N/A')}")
    lines.append(f"Señal activa: {payload.get('señal_activa', False)}")
    return "\n".join(lines)

def send_via_openclaw(message):
    """Send message to Telegram using OpenClaw CLI."""
    try:
        openclaw_cmd = _get_openclaw_cmd()
        telegram_target = _get_telegram_target()
        
        if not telegram_target:
            logger.error("telegram_chat_id not configured in config.json")
            return False
            
        cmd = [openclaw_cmd, "message", "send", "--channel", "telegram", "--target", str(telegram_target), "--message", message]
        logger.info(f"Executing: {' '.join(cmd)}")
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            logger.info("Message sent successfully")
            return True
        else:
            logger.error(f"OpenClaw error: {result.stderr}")
            return False
    except Exception as e:
        logger.exception(f"Failed to send via OpenClaw: {e}")
        return False

def main():
    server = HTTPServer(('localhost', PORT), WebhookHandler)
    logger.info(f"Starting webhook server on port {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down webhook server")
        server.server_close()

if __name__ == '__main__':
    main()
