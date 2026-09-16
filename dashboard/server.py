"""Web server for the dashboard: the page, a live state stream (Server-Sent
Events) and the control endpoints.
"""
import json
import mimetypes
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import bot_runner
import pause_control
import telemetry

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_PORT = 8765


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """Don't print a traceback every time a tab is closed or reloaded."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "BlockBlastBot"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet

    # helpers

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_index(self):
        """index.html with the current state embedded, so the first paint is
        already up to date instead of flashing an empty board."""
        html = (STATIC_DIR / "index.html").read_text()
        payload = json.dumps(telemetry.snapshot()).replace("</", "<\\/")
        html = html.replace(
            '<script id="initial-state" type="application/json">null</script>',
            f'<script id="initial-state" type="application/json">{payload}</script>')
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, rel_path):
        path = (STATIC_DIR / rel_path).resolve()
        if not path.is_file() or STATIC_DIR.resolve() not in path.parents:
            self.send_error(404)
            return
        body = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # routes

    def do_GET(self):
        route = self.path.split("?")[0]
        if route == "/":
            self._send_index()
        elif route.startswith("/static/"):
            self._send_static(route[len("/static/"):])
        elif route == "/api/state":
            self._send_json(telemetry.snapshot())
        elif route == "/api/diagnose":
            import diagnose
            self._send_json(diagnose.collect())
        elif route == "/api/stream":
            self._stream()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.split("?")[0] != "/api/control":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "malformed JSON"}, status=400)
            return
        ok, message = self._control(payload)
        self._send_json({"ok": ok, "message": message, "state": telemetry.snapshot()})

    def _control(self, payload):
        action = payload.get("action")
        if action == "start":
            mode = "live" if payload.get("mode") == "live" else "demo"
            return bot_runner.start(mode)
        if action == "stop":
            return bot_runner.stop()
        if action == "pause":
            pause_control.set_paused(True)
            return True, "paused"
        if action == "resume":
            pause_control.set_paused(False)
            return True, "resumed"
        if action == "speed":
            telemetry.set_speed(payload.get("value", 0.45))
            return True, "speed updated"
        if action == "diagnose":
            # Run it here, since this process has the Screen Recording permission.
            import diagnose
            report = diagnose.collect()
            found = report.get("pieces_detected")
            if report.get("error"):
                telemetry.log(f"Vision check failed at the {report['stage']} stage: "
                              f"{report['error']}", "error")
            else:
                telemetry.log(f"Vision check: board {report['board_filled']}/64 filled, "
                              f"{found}/3 tray slots detected",
                              "good" if found else "warn")
            for note in report.get("notes", []):
                telemetry.log(note, "warn")
            return True, "diagnosed"
        if action == "reset_combo":
            import solver
            solver.save_combo_counter(0)
            telemetry.log("Combo counter reset to 0", "warn")
            return True, "combo counter reset"
        return False, f"unknown action {action!r}"

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        last = -1
        try:
            while True:
                state = telemetry.wait_for_change(last, timeout=15.0)
                last = state["version"]
                self.wfile.write(b"data: " + json.dumps(state).encode() + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # tab closed


def _bind(port, tries=20):
    """Bind to `port`, or the next free port if it's taken."""
    last_error = None
    for candidate in range(port, port + tries):
        try:
            httpd = DashboardServer(("127.0.0.1", candidate), Handler)
        except OSError as exc:
            last_error = exc
            continue
        if candidate != port:
            print(f"Port {port} is in use; using {candidate} instead.")
        return httpd, candidate
    raise SystemExit(f"No free port in {port}-{port + tries - 1}: {last_error}")


def serve(port=DEFAULT_PORT, open_browser=True):
    """Serve the dashboard until Ctrl+C."""
    httpd, port = _bind(port)
    url = f"http://127.0.0.1:{port}"
    print(f"Dashboard: {url}   (Ctrl+C to quit)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        pause_control.request_stop()
        httpd.server_close()
