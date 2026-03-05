"""
serve_dashboard.py
------------------
Starts a simple local HTTP server to serve the Clara Answers dashboard.
Serves from the project root so the dashboard can fetch all JSON output files.

Usage:
    python scripts/serve_dashboard.py

Then open: http://localhost:8765/dashboard/index.html

Zero dependencies — uses Python's built-in http.server module.
"""

import http.server
import socketserver
import os
import sys
import webbrowser
import threading
from pathlib import Path

# Always serve from the project root (one level above /scripts)
PROJECT_ROOT = Path(__file__).parent.parent
PORT = 8765
DASHBOARD_PATH = "/dashboard/index.html"


class CORSRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler with CORS headers so the dashboard JS can fetch local JSON."""

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def log_message(self, format, *args):
        # Guard: args[0] may be an HTTPStatus object (not a string) during error handling.
        # Convert to string safely before checking the extension.
        try:
            path = str(args[0]) if args else ""
            if any(path.endswith(ext) for ext in [".json", ".html"]):
                print(f"  [{self.address_string()}] {format % args}")
        except Exception:
            pass  # Never let logging noise crash the server


def open_browser():
    """Open the dashboard in the default browser after a short delay."""
    import time
    time.sleep(0.8)
    webbrowser.open(f"http://localhost:{PORT}{DASHBOARD_PATH}")


if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)
    print(f"\n  Clara Answers Dashboard")
    print(f"  ─────────────────────────────────────")
    print(f"  Serving from : {PROJECT_ROOT}")
    print(f"  Dashboard    : http://localhost:{PORT}{DASHBOARD_PATH}")
    print(f"  Press Ctrl+C to stop\n")

    # Open browser automatically
    t = threading.Thread(target=open_browser, daemon=True)
    t.start()

    with socketserver.TCPServer(("", PORT), CORSRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Shutting down dashboard server.")
