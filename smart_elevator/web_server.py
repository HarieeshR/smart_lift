"""Browser lift simulation: per-floor YOLO cameras + LOOK scheduling."""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import subprocess
import sys
import threading
import time
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from .cameras import CameraManager
    from .config import CONFIG, AppConfig
    from .lift_controller import LookController
except ImportError:
    from cameras import CameraManager
    from config import CONFIG, AppConfig
    from lift_controller import LookController


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
CERT_DIR = ROOT / "certs"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/camera": ("camera.html", "text/html; charset=utf-8"),
    "/camera.js": ("camera.js", "text/javascript; charset=utf-8"),
}


class LiftSimulation:
    """Coordinates per-floor cameras, LOOK scheduling, and simulated movement."""

    def __init__(self, app_config: AppConfig, cameras: CameraManager | None = None) -> None:
        self.config = app_config
        self.cameras = cameras if cameras is not None else CameraManager(app_config)
        self.controller = LookController(
            num_floors=app_config.num_floors,
            speed=app_config.lift_speed_floors_per_second,
            door_open_time=app_config.door_open_time,
            door_transition_time=app_config.door_transition_time,
            start_floor=app_config.home_floor,
            home_floor=app_config.home_floor,
            idle_return_delay=app_config.idle_return_delay,
        )
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._lift_thread = threading.Thread(
            target=self._lift_loop, name="lift-simulation", daemon=True
        )

    def start(self) -> None:
        self._lift_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.cameras.stop_all()

    def _lift_loop(self) -> None:
        previous_time = time.monotonic()
        while not self._stop.wait(self.config.lift_update_interval):
            with self._lock:
                now = time.monotonic()
                elapsed = min(now - previous_time, 0.25)
                previous_time = now
                self._enforce_camera_gating(now)
                self.controller.tick(elapsed)

    def _enforce_camera_gating(self, now: float | None = None) -> None:
        """Cancel a verified floor call once nobody has been in front of the lift."""
        for floor in sorted(self.controller.floor_calls):
            camera = self.cameras.get(floor)
            if camera is None:
                continue
            if camera.absent_for(now) >= self.config.person_lost_timeout:
                self.controller.cancel_floor_call(
                    floor, "person left the lift lobby, moving on to the next call"
                )

    def request_hall(self, floor: int, direction: str) -> dict[str, Any]:
        """Landing call. Floors with a camera need a confirmed waiting person."""
        with self._lock:
            hall_direction = self.controller.validate_hall_call(floor, direction.upper())
            camera = self.cameras.get(floor)
            if camera is None:
                self.controller.add_hall_call(floor, hall_direction)
            elif not camera.online:
                self.controller.log(f"Floor {floor} call rejected: camera offline")
            elif not camera.waiting_person:
                self.controller.log(
                    f"Floor {floor} call rejected: no waiting person detected"
                )
            else:
                self.controller.add_floor_call(floor)
            return self.snapshot()

    def request_car(self, floor: int) -> dict[str, Any]:
        """Register a cabin-panel call; these are never gated by the camera."""
        with self._lock:
            self.controller.add_car_call(floor)
            return self.snapshot()

    def enable_camera(self, floor: int, kind: str, source: str = "") -> dict[str, Any]:
        self.cameras.enable(floor, kind, source)
        with self._lock:
            label = "phone/browser" if kind == "browser" else source
            self.controller.log(f"Camera enabled on floor {floor} ({label})")
            return self.snapshot()

    def disable_camera(self, floor: int) -> dict[str, Any]:
        removed = self.cameras.disable(floor)
        with self._lock:
            if removed:
                self.controller.log(f"Camera disabled on floor {floor}")
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = self.controller.snapshot()
        state["cameras"] = self.cameras.statuses()
        return state


class Handler(BaseHTTPRequestHandler):
    simulation: LiftSimulation

    def log_message(self, format: str, *args: Any) -> None:
        # Browser cameras POST several frames per second; keep the console quiet.
        pass

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        parts = path.strip("/").split("/")
        if path in STATIC_FILES:
            self._file(*STATIC_FILES[path])
        elif path == "/api/status":
            self._json(self.simulation.snapshot())
        elif path == "/api/cameras":
            self._json(self.simulation.cameras.statuses())
        elif parts[0] == "video_feed" and len(parts) == 2 and parts[1].isdigit():
            self._mjpeg(int(parts[1]))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parts = self.path.split("?", 1)[0].strip("/").split("/")
        try:
            if parts[:2] == ["api", "hall"] and len(parts) == 4:
                self._json(self.simulation.request_hall(int(parts[2]), parts[3]))
            elif parts[:2] == ["api", "car"] and len(parts) == 3:
                self._json(self.simulation.request_car(int(parts[2])))
            elif parts[:2] == ["api", "cameras"] and len(parts) == 3:
                body = json.loads(self._body() or b"{}")
                self._json(
                    self.simulation.enable_camera(
                        int(parts[2]), str(body.get("kind", "")), str(body.get("source", ""))
                    )
                )
            elif parts[:2] == ["api", "cameras"] and len(parts) == 4 and parts[3] == "frame":
                self.simulation.cameras.push_frame(int(parts[2]), self._body())
                self._json({"ok": True})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error))

    def do_DELETE(self) -> None:
        parts = self.path.strip("/").split("/")
        try:
            if parts[:2] == ["api", "cameras"] and len(parts) == 3:
                self._json(self.simulation.disable_camera(int(parts[2])))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error))

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 6 * 1024 * 1024:
            raise ValueError("Request body too large")
        return self.rfile.read(length) if length else b""

    def _file(self, name: str, content_type: str) -> None:
        data = (WEB_DIR / name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, value: Any) -> None:
        data = json.dumps(value).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _mjpeg(self, floor: int) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last = b""
        while True:
            camera = self.simulation.cameras.get(floor)
            if camera is None:
                return
            # Send each frame as soon as it is encoded instead of polling.
            frame = camera.wait_jpeg(last)
            if frame and frame is not last:
                last = frame
                try:
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    return


def lan_addresses() -> list[str]:
    """Best-effort list of this machine's LAN IPv4 addresses."""
    addresses: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))  # No packet is sent for UDP connect.
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addresses if not a.startswith("127."))


def ensure_certificate() -> tuple[Path, Path]:
    """Create a self-signed certificate (via openssl) for HTTPS camera access."""
    cert, key = CERT_DIR / "cert.pem", CERT_DIR / "key.pem"
    if cert.exists() and key.exists():
        return cert, key
    CERT_DIR.mkdir(exist_ok=True)
    names = ["DNS:localhost", "IP:127.0.0.1", *(f"IP:{ip}" for ip in lan_addresses())]
    try:
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(key), "-out", str(cert), "-days", "825",
                "-subj", "/CN=smart-elevator",
                "-addext", f"subjectAltName={','.join(names)}",
            ],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise RuntimeError(
            "Could not create a self-signed certificate. Install openssl, or create "
            f"{cert} and {key} yourself."
        ) from error
    return cert, key


class LiftHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: tuple[str, int]) -> None:
        # Browsers drop the connection until the self-signed cert is accepted, and
        # http:// requests hit the TLS port; log those as one line, not a traceback.
        error = sys.exc_info()[1]
        if isinstance(error, (ssl.SSLError, ConnectionError)):
            reason = getattr(error, "reason", None) or type(error).__name__
            hint = " (use https://)" if reason == "HTTP_REQUEST" else ""
            print(f"  TLS/connection from {client_address[0]} dropped: {reason}{hint}", flush=True)
            return
        super().handle_error(request, client_address)


def run_server(
    config: AppConfig = CONFIG,
    port: int = 8000,
    host: str = "0.0.0.0",
    use_https: bool = False,
    floor_cameras: dict[int, str] | None = None,
) -> None:
    simulation = LiftSimulation(config)
    for floor, source in (floor_cameras or {}).items():
        kind = "browser" if source == "browser" else ("local" if source.isdigit() else "url")
        simulation.enable_camera(floor, kind, source)
    Handler.simulation = simulation
    server = LiftHTTPServer((host, port), Handler)
    scheme = "http"
    if use_https:
        cert, key = ensure_certificate()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        # Handshake in the per-request thread so one slow client cannot stall accept().
        server.socket = context.wrap_socket(
            server.socket, server_side=True, do_handshake_on_connect=False
        )
        scheme = "https"
    simulation.start()
    print(f"Lift simulation: {scheme}://127.0.0.1:{port}", flush=True)
    if host in ("0.0.0.0", ""):
        for address in lan_addresses():
            print(f"  On other devices: {scheme}://{address}:{port}  (phone camera: /camera)", flush=True)
        if not use_https:
            print("  Note: phone browsers only allow the camera over HTTPS; add --https.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        simulation.stop()
        server.server_close()


def parse_floor_camera(value: str) -> tuple[int, str]:
    floor, sep, source = value.partition("=")
    if not sep or not floor.strip().isdigit() or not source.strip():
        raise argparse.ArgumentTypeError("Use FLOOR=SOURCE, e.g. 1=0 or 3=http://phone:8080/video")
    return int(floor), source.strip()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the YOLO-backed smart elevator simulation")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="0.0.0.0", help="Use 127.0.0.1 to block other devices")
    parser.add_argument("--https", action="store_true", help="Serve HTTPS (needed for phone cameras)")
    parser.add_argument(
        "--floor-camera",
        type=parse_floor_camera,
        action="append",
        default=[],
        metavar="FLOOR=SOURCE",
        help="Camera for a floor: webcam index, stream URL, or 'browser'. Repeatable.",
    )
    parser.add_argument("--model", default=str(CONFIG.model_path))
    parser.add_argument("--device", default=CONFIG.device)
    parser.add_argument("--inference-image-size", type=int, default=CONFIG.inference_image_size)
    args = parser.parse_args()
    run_server(
        replace(
            CONFIG,
            model_path=Path(args.model),
            device=args.device,
            inference_image_size=args.inference_image_size,
        ),
        args.port,
        args.host,
        args.https,
        dict(args.floor_camera),
    )
