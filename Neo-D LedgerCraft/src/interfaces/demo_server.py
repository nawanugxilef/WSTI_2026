"""Serve the isolated WSTI demo on loopback or an explicit Tailscale address."""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from urllib.parse import urlsplit

from src.interfaces.demo_photo_ocr import read_photo_text
from src.interfaces.demo_runtime import (
    DemoError,
    DemoRuntime,
    FixtureFinance,
    GeminiRequestBudget,
    MAX_REQUEST_BYTES,
    create_gemini_finance,
    load_fixtures,
)


PROJECT = Path(__file__).resolve().parents[2]
OFFICE = PROJECT.parent / "static"
DEMO = PROJECT / "web" / "demo"
FIXTURES = PROJECT / "demo" / "sample_receipts.json"
COOKIE_NAME = "__Host-neo-wsti-demo"
TAILNET_COOKIE_NAME = "neo-wsti-tailnet-demo"
_TAILNET_IPV4 = IPv4Network("100.64.0.0/10")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
_PUBLIC_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_SLOTS = 8
_MAX_CORRECTION_BYTES = 1024
_SECURITY_POLICY = "; ".join((
    "default-src 'none'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data: blob:",
    "connect-src 'self' data: blob:",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))

_STATIC = {
    "/office-calendar.js": (OFFICE / "office-calendar.js", "text/javascript; charset=utf-8"),
    "/office-board.js": (OFFICE / "office-board.js", "text/javascript; charset=utf-8"),
    "/demo/app.js": (DEMO / "app.js", "text/javascript; charset=utf-8"),
    "/demo/womby.js": (DEMO / "womby.js", "text/javascript; charset=utf-8"),
    "/demo/chat.css": (DEMO / "chat.css", "text/css; charset=utf-8"),
    "/demo/womby.glb": (DEMO / "womby.glb", "model/gltf-binary"),
    "/demo/samples/harbour-cafe.jpg": (DEMO / "samples/harbour-cafe.jpg", "image/jpeg"),
    "/demo/samples/paper-gum.jpg": (DEMO / "samples/paper-gum.jpg", "image/jpeg"),
    "/demo/samples/studio-supply.pdf": (DEMO / "samples/studio-supply.pdf", "application/pdf"),
}


def _landing_page() -> bytes:
    html = (OFFICE / "index.html").read_text(encoding="utf-8")
    return (html.replace('<a href="/dashboard">Dashboard</a>', "", 1)
            .replace("</head>", '<link rel="stylesheet" href="/demo/chat.css">\n'
                     '<script type="module" src="/demo/app.js"></script>\n</head>', 1)
            .encode("utf-8"))


def _validate_public_origin(origin: str) -> str:
    host = urlsplit(origin).hostname or ""
    if (
        origin != f"https://{host}"
        or len(host) > 253
        or not _PUBLIC_HOST.fullmatch(host)
        or host.endswith((".localhost", ".local", ".internal", ".home.arpa"))
    ):
        raise ValueError("Use one dedicated HTTPS demo hostname without a port or path.")
    return origin


def _validate_tailnet_ip(address: str) -> str:
    try:
        ip = IPv4Address(address)
    except ValueError:
        raise ValueError("Use the host's Tailscale IPv4 address.") from None
    if ip not in _TAILNET_IPV4:
        raise ValueError("Use the host's Tailscale IPv4 address.")
    return str(ip)


class DemoServer(ThreadingHTTPServer):
    request_queue_size = 16

    def __init__(
        self, port: int, runtime: DemoRuntime, *,
        public_origin: str | None = None, tailnet_ip: str | None = None,
    ):
        if tailnet_ip is not None and (public_origin is not None or runtime.mode not in ("fixture", "local")):
            raise ValueError("The direct tailnet preview requires fixture or local OCR mode.")
        if public_origin is not None and runtime.mode == "local":
            raise ValueError("Local OCR is available only for private previews.")
        self.runtime = runtime
        self.page = _landing_page()
        self.public_origin = _validate_public_origin(public_origin) if public_origin is not None else None
        self.tailnet_ip = _validate_tailnet_ip(tailnet_ip) if tailnet_ip is not None else None
        self.cookie_name = TAILNET_COOKIE_NAME if self.tailnet_ip else COOKIE_NAME
        self._slots = threading.BoundedSemaphore(_SLOTS)
        super().__init__((self.tailnet_ip or "127.0.0.1", port), DemoHandler)

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        if self.public_origin:
            return (urlsplit(self.public_origin).netloc,)
        if self.tailnet_ip:
            return (f"{self.tailnet_ip}:{self.server_port}",)
        return (f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}")

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(15)
        return request, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nRetry-After: 3\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class DemoHandler(BaseHTTPRequestHandler):
    server: DemoServer

    def log_message(self, format, *args):
        pass

    def _send(self, status: int, mime: str, body: bytes, *, cookie: str | None = None, attachment: str | None = None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", _SECURITY_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict, *, cookie: str | None = None):
        self._send(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"), cookie=cookie)

    def _token(self) -> str | None:
        try:
            value = SimpleCookie(self.headers.get("Cookie", "")).get(self.server.cookie_name)
        except CookieError:
            return None
        return value.value if value and _TOKEN.fullmatch(value.value) else None

    def _check_host(self) -> str:
        host = self.headers.get("Host", "")
        if self.server.tailnet_ip and IPv4Address(self.client_address[0]) not in _TAILNET_IPV4:
            raise DemoError(403, "Open this private preview through Tailscale.")
        if host not in self.server.allowed_hosts:
            raise DemoError(403, "Use the configured demo address.")
        return host

    def _json_body(self, *, correction: bool = False) -> object:
        if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
            raise DemoError(415, "Send the receipt request as JSON.")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,9}", lengths[0]):
            raise DemoError(400, "The request has an invalid length.")
        length = int(lengths[0])
        if correction and length > _MAX_CORRECTION_BYTES:
            raise DemoError(413, "The receipt edit is too large.")
        if length > MAX_REQUEST_BYTES:
            raise DemoError(413, "Keep the combined uploads to 10 MiB or less.")
        try:
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("Incomplete request.")
            return json.loads(data)
        except (ValueError, RecursionError) as error:
            raise DemoError(400, "The receipt request could not be read.") from error

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self._check_host()
            route = urlsplit(self.path).path
            if route in ("/", "/demo"):
                self._send(200, "text/html; charset=utf-8", self.server.page)
            elif route in _STATIC:
                path, mime = _STATIC[route]
                if not path.is_file():
                    raise DemoError(404, "Asset not found.")
                self._send(200, mime, path.read_bytes(), attachment=path.name if "/samples/" in route else None)
            elif route == "/demo/api/session":
                self._json(200, self.server.runtime.session(self._token()))
            elif route == "/api/office/receipt-progress":
                self._json(200, self.server.runtime.progress(self._token()))
            else:
                raise DemoError(404, "Demo page not found.")
        except DemoError as error:
            self._json(error.status, {"error": str(error)})
        except ValueError:
            self._json(400, {"error": "Invalid demo route."})

    def do_POST(self):
        try:
            host = self._check_host()
            route = urlsplit(self.path).path
            if route not in ("/demo/api/session", "/demo/api/intake", "/demo/api/correct"):
                raise DemoError(404, "Demo action not found.")
            if self.headers.get("Origin") != (self.server.public_origin or f"http://{host}"):
                raise DemoError(403, "Open the demo page before sending receipts.")
            if route == "/demo/api/session":
                if self.headers.get("Transfer-Encoding") or self.headers.get_all("Content-Length", []) not in ([], ["0"]):
                    raise DemoError(400, "Starting a session takes no receipt data.")
                token, snapshot = self.server.runtime.open_session(self._token())
                ttl = snapshot["session"]["expires_in"]
                cookie = f"{self.server.cookie_name}={token}; Path=/; Max-Age={ttl}; HttpOnly; SameSite=Strict"
                if not self.server.tailnet_ip:
                    cookie += "; Secure"
                self._json(200, snapshot, cookie=cookie)
            else:
                token = self._token()
                self.server.runtime.session(token)
                if route == "/demo/api/correct":
                    self._json(200, self.server.runtime.correct(token, self._json_body(correction=True)))
                else:
                    self._json(202, self.server.runtime.submit(token, self._json_body()))
        except DemoError as error:
            self._json(error.status, {"error": str(error)})
        except ValueError:
            self._json(400, {"error": "Invalid demo request."})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the isolated WSTI demo.")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--mode", choices=("fixture", "local", "gemini"), default="fixture")
    network = parser.add_mutually_exclusive_group()
    network.add_argument("--public-origin", help="Approved HTTPS hostname forwarded by a reverse proxy. Bind stays on loopback.")
    network.add_argument("--tailnet-ip", help="Host's Tailscale IPv4 address for a private preview.")
    args = parser.parse_args()
    try:
        if args.public_origin is not None:
            _validate_public_origin(args.public_origin)
            if args.mode == "local":
                raise ValueError("Local OCR is available only for private previews.")
        if args.tailnet_ip is not None:
            _validate_tailnet_ip(args.tailnet_ip)
            if args.mode not in ("fixture", "local"):
                raise ValueError("The direct tailnet preview requires fixture or local OCR mode.")
        if args.mode == "local" and os.name != "nt":
            raise ValueError("Local photo OCR requires Windows.")
    except ValueError as error:
        parser.error(str(error))
    if args.mode == "gemini":
        key = os.environ.get("NEO_DEMO_GEMINI_API_KEY", "").strip()
        if not key:
            parser.error("Gemini demo mode needs a separate NEO_DEMO_GEMINI_API_KEY.")
        budget = GeminiRequestBudget()

        def finance_factory():
            return create_gemini_finance(key, budget)
    else:
        fixtures = load_fixtures(FIXTURES)

        def finance_factory():
            return FixtureFinance(fixtures)

    runtime = DemoRuntime(args.mode, finance_factory, photo_reader=read_photo_text if args.mode == "local" else None)
    try:
        with DemoServer(args.port, runtime, public_origin=args.public_origin, tailnet_ip=args.tailnet_ip) as server:
            origin = server.public_origin or f"http://{server.tailnet_ip or 'localhost'}:{server.server_port}"
            print(f"Neo WSTI demo ({args.mode}) at {origin}/demo", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
