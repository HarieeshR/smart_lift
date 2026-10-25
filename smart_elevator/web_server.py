"""Browser lift simulation backed by the existing YOLO waiting-person detector."""

from __future__ import annotations

import argparse
import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import cv2

try:
    from .config import CONFIG, AppConfig, parse_camera_source
    from .detector import PersonDetector
    from .roi import ROI
    from .tracking import WaitingPersonAnalyzer
except ImportError:
    from config import CONFIG, AppConfig, parse_camera_source
    from detector import PersonDetector
    from roi import ROI
    from tracking import WaitingPersonAnalyzer


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"


class LiftSimulation:
    """Coordinates camera inference, floor requests, and simulated movement."""

    def __init__(self, app_config: AppConfig, camera_floor: int = 1) -> None:
        self.config = app_config
        self.camera_floor = camera_floor
        self.current_floor = 1
        self.lift_position = 1.0
        self.direction = "IDLE"
        self.target_floor: int | None = None
        self.requests: dict[int, float] = {}
        self.last_frame = b""
        self.last_status: dict[str, Any] = {
            "waiting_person": False,
            "person_id": None,
            "dwell_time": 0.0,
            "event": "Camera starting",
        }
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._camera_thread = threading.Thread(
            target=self._camera_loop, name="yolo-camera", daemon=True
        )
        self._lift_thread = threading.Thread(
            target=self._lift_loop, name="lift-simulation", daemon=True
        )

    def start(self) -> None:
        self._camera_thread.start()
        self._lift_thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _camera_loop(self) -> None:
        try:
            detector = PersonDetector(
                model_path=self.config.model_path,
                confidence_threshold=self.config.confidence_threshold,
                device=self.config.device,
                inference_image_size=self.config.inference_image_size,
            )
        except (FileNotFoundError, RuntimeError) as error:
            with self._lock:
                self.last_status["camera_error"] = str(error)
                self.last_status["event"] = "Camera detector unavailable"
            return
        analyzer = WaitingPersonAnalyzer(
            roi=ROI.from_tuple(self.config.roi),
            min_dwell_time=self.config.min_dwell_time,
            max_allowed_movement=self.config.max_allowed_movement,
            max_track_age=self.config.max_track_age,
            motion_window=self.config.motion_window,
        )
        camera = cv2.VideoCapture(parse_camera_source(self.config.camera_source))
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.image_width)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.image_height)
        if not camera.isOpened():
            with self._lock:
                self.last_status["camera_error"] = (
                    f"Could not open camera source {self.config.camera_source!r}"
                )
            return

        try:
            while not self._stop.is_set():
                success, frame = camera.read()
                if not success:
                    time.sleep(0.1)
                    continue
                detections = detector.track(frame)
                statuses, result = analyzer.update(detections, time.perf_counter())
                self._draw_camera(frame, detections, statuses, result)
                ok, encoded = cv2.imencode(".jpg", frame)
                if ok:
                    with self._lock:
                        event = self.last_status.get("event", "Monitoring camera")
                        self.last_frame = encoded.tobytes()
                        self.last_status = result.as_dict()
                        self.last_status["event"] = event
                        self.last_status.update(
                            {
                                "camera_floor": self.camera_floor,
                                "tracks": [
                                    {
                                        "id": status.track_id,
                                        "state": status.state.value,
                                        "dwell_time": round(status.dwell_time, 2),
                                        "movement": round(status.movement, 1),
                                        "inside_roi": status.inside_roi,
                                    }
                                    for status in statuses
                                ],
                            }
                        )
                    self._start_pending_request()
        finally:
            camera.release()

    def _draw_camera(self, frame, detections, statuses, result) -> None:
        roi = ROI.from_tuple(self.config.roi)
        cv2.rectangle(frame, (roi.left, roi.top), (roi.right, roi.bottom), (0, 220, 255), 2)
        by_id = {status.track_id: status for status in statuses}
        colors = {
            "VALID WAITING": (0, 255, 0),
            "WAITING": (0, 220, 255),
            "WALKING THROUGH": (0, 140, 255),
        }
        for detection in detections:
            left, top, right, bottom = detection.box
            status = by_id.get(detection.track_id)
            label = "OUTSIDE"
            dwell = 0.0
            if status:
                label = status.state.value
                dwell = status.dwell_time
            color = colors.get(label, (180, 180, 180))
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            cv2.putText(
                frame,
                f"#{detection.track_id} {detection.confidence:.2f} {label} {dwell:.1f}s",
                (left, max(20, top - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                2,
            )
        status_text = "WAITING PERSON DETECTED" if result.waiting_person else "NO WAITING PERSON"
        cv2.putText(frame, status_text, (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0) if result.waiting_person else (0, 165, 255), 2)

    def _lift_loop(self) -> None:
        previous_time = time.monotonic()
        while not self._stop.wait(self.config.lift_update_interval):
            with self._lock:
                now = time.monotonic()
                elapsed = min(now - previous_time, 0.25)
                previous_time = now
                self._advance_lift(elapsed)

    def _advance_lift(self, elapsed: float) -> None:
        if self.target_floor is None:
            self.direction = "IDLE"
            return
        if not self.last_status.get("waiting_person", False):
            self._cancel_request(
                self.target_floor,
                "waiting person is no longer detected",
            )
            return

        target = float(self.target_floor)
        distance = target - self.lift_position
        if abs(distance) <= self.config.lift_speed_floors_per_second * elapsed:
            self.lift_position = target
            self.current_floor = self.target_floor
            self._arrive_at_target()
            return

        self.direction = "UP" if distance > 0 else "DOWN"
        self.lift_position += (
            self.config.lift_speed_floors_per_second
            * elapsed
            * (1 if distance > 0 else -1)
        )
        self.current_floor = round(self.lift_position)

    def _arrive_at_target(self) -> None:
        floor = self.current_floor
        request_time = self.requests.get(floor)
        if request_time is not None:
            person_present = (
                self.last_status.get("waiting_person", False)
            )
            if person_present:
                self.requests.pop(floor, None)
                self.last_status["event"] = (
                    f"Floor {floor} request accepted: waiting person confirmed"
                )
            else:
                self._cancel_request(floor, "no waiting person at arrival")
        remaining = sorted(self.requests)
        self.target_floor = remaining[0] if remaining else None
        self.direction = "IDLE" if self.target_floor is None else (
            "UP" if self.target_floor > self.current_floor else "DOWN"
        )

    def _start_pending_request(self) -> None:
        with self._lock:
            if (
                not self.last_status.get("waiting_person", False)
                or self.target_floor is not None
                or not self.requests
            ):
                return
            self.target_floor = (
                self.camera_floor
                if self.camera_floor in self.requests
                else sorted(self.requests)[0]
            )
            self.last_status["event"] = (
                f"Floor {self.target_floor} request accepted: waiting person detected"
            )

    def _cancel_request(self, floor: int, reason: str) -> None:
        self.requests.pop(floor, None)
        if self.target_floor == floor:
            self.target_floor = None
        self.direction = "IDLE"
        self.last_status["event"] = f"Floor {floor} request cancelled: {reason}"

    def request_floor(self, floor: int) -> dict[str, Any]:
        if floor < 1 or floor > 5:
            raise ValueError("Floor must be between 1 and 5")
        with self._lock:
            self.camera_floor = floor
            if not self.last_status.get("waiting_person", False):
                self._cancel_request(floor, "no waiting person detected")
                return self.snapshot()
            self.requests.setdefault(floor, time.time())
            if self.target_floor is None and self.last_status.get("waiting_person", False):
                self.target_floor = floor
                if self.lift_position == floor:
                    self._arrive_at_target()
                else:
                    self.last_status["event"] = (
                        f"Floor {floor} request accepted: waiting person detected"
                    )
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "current_floor": self.current_floor,
                "lift_position": round(self.lift_position, 3),
                "direction": self.direction,
                "target_floor": self.target_floor,
                "requests": sorted(self.requests),
                "camera_floor": self.camera_floor,
                "camera": self.last_status,
            }


class Handler(BaseHTTPRequestHandler):
    simulation: LiftSimulation

    def do_GET(self) -> None:
        if self.path == "/":
            self._file("index.html", "text/html; charset=utf-8")
        elif self.path == "/app.js":
            self._file("app.js", "text/javascript; charset=utf-8")
        elif self.path == "/styles.css":
            self._file("styles.css", "text/css; charset=utf-8")
        elif self.path == "/api/status":
            self._json(self.simulation.snapshot())
        elif self.path == "/video_feed":
            self._mjpeg()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path.startswith("/api/request/"):
            try:
                floor = int(self.path.rsplit("/", 1)[1])
                self._json(self.simulation.request_floor(floor))
            except (ValueError, IndexError) as error:
                self.send_error(HTTPStatus.BAD_REQUEST, str(error))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

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
        self.end_headers()
        self.wfile.write(data)

    def _mjpeg(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        while True:
            with self.simulation._lock:
                frame = self.simulation.last_frame
            if frame:
                try:
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
            time.sleep(0.08)


def run_server(
    config: AppConfig = CONFIG, port: int = 8000, camera_floor: int = 1
) -> None:
    if camera_floor < 1 or camera_floor > 5:
        raise ValueError("camera_floor must be between 1 and 5")
    simulation = LiftSimulation(config, camera_floor=camera_floor)
    Handler.simulation = simulation
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    simulation.start()
    print(f"Lift simulation: http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        simulation.stop()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the YOLO-backed smart elevator simulation")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--camera", default=CONFIG.camera_source)
    parser.add_argument("--model", default=str(CONFIG.model_path))
    parser.add_argument("--camera-floor", type=int, default=1)
    args = parser.parse_args()
    run_server(
        AppConfig(
            **{
                **CONFIG.__dict__,
                "camera_source": args.camera,
                "model_path": Path(args.model),
            }
        ),
        args.port,
        args.camera_floor,
    )
