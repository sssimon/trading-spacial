#!/usr/bin/env python3
"""
Webhook receiver for BTC Scanner.
Listens on port 9000, forwards signals to Telegram via OpenClaw.
Uses the preformatted telegram_message from the scanner payload.
"""

import json
import logging
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import subprocess
import sys

# Configuration
PORT = 9000
_DIR     = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(_DIR, "logs", "webhook.log")

def _load_webhook_config():
    """Load telegram_chat_id and openclaw path from config.json."""
    cfg_path = os.path.join(_DIR, "config.json")
    cfg = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
        except Exception:
            pass
    return cfg

def _get_telegram_target():
    return _load_webhook_config().get("telegram_chat_id", "")

def _get_openclaw_cmd():
    cfg = _load_webhook_config()
    configured = cfg.get("openclaw_path", "")
    if configured and os.path.isfile(configured):
        return configured
    import shutil
    found = shutil.which("openclaw")
    if found:
        return found
    return "openclaw"

os.makedirs(os.path.join(_DIR, "logs"), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/webhook':
            self.send_error(404, "Endpoint not found")
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
        cmd = [openclaw_cmd, "message", "send", "--channel", "telegram", "--target", telegram_target, "--message", message]
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