"""Browser lift simulation: YOLO waiting-person detection + LOOK scheduling."""

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
    from .lift_controller import Direction, LookController
except ImportError:
    from config import CONFIG, AppConfig, parse_camera_source
    from detector import PersonDetector
    from roi import ROI
    from tracking import WaitingPersonAnalyzer
    from lift_controller import Direction, LookController


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"


class LiftSimulation:
    """Coordinates camera inference, LOOK scheduling, and simulated movement."""

    def __init__(self, app_config: AppConfig, camera_floor: int = 1) -> None:
        self.config = app_config
        self.camera_floor = camera_floor
        self.controller = LookController(
            num_floors=app_config.num_floors,
            speed=app_config.lift_speed_floors_per_second,
            door_open_time=app_config.door_open_time,
            door_transition_time=app_config.door_transition_time,
        )
        self.last_frame = b""
        self.last_status: dict[str, Any] = {
            "waiting_person": False,
            "person_id": None,
            "dwell_time": 0.0,
        }
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._camera_thread = threading.Thread(
            target=self._camera_loop, name="yolo-camera", daemon=True
        )
        self._lift_thread = threading.Thread(
            target=self._lift_loop, name="lift-simulation", daemon=True
        )
        self.controller.log(f"Camera assigned to floor {camera_floor}")

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
                        self.last_frame = encoded.tobytes()
                        self.last_status = result.as_dict()
                        self.last_status["tracks"] = [
                            {
                                "id": status.track_id,
                                "state": status.state.value,
                                "dwell_time": round(status.dwell_time, 2),
                                "movement": round(status.movement, 1),
                                "inside_roi": status.inside_roi,
                            }
                            for status in statuses
                        ]
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
                self._enforce_camera_gating()
                self.controller.tick(elapsed)

    def _waiting_person(self) -> bool:
        return bool(self.last_status.get("waiting_person", False))

    def _enforce_camera_gating(self) -> None:
        """Drop camera-floor hall calls once the waiting person is gone."""
        if self._waiting_person():
            return
        for direction in (Direction.UP, Direction.DOWN):
            self.controller.cancel_hall_call(
                self.camera_floor, direction, "waiting person left the camera zone"
            )

    def request_hall(self, floor: int, direction: str) -> dict[str, Any]:
        """Register a landing UP/DOWN call; camera-floor calls need a waiting person."""
        hall_direction = Direction(direction.upper())
        with self._lock:
            if floor == self.camera_floor and not self._waiting_person():
                self.controller.log(
                    f"Floor {floor} {hall_direction.value} call rejected: "
                    "no waiting person detected"
                )
                return self.snapshot()
            self.controller.add_hall_call(floor, hall_direction)
            return self.snapshot()

    def request_car(self, floor: int) -> dict[str, Any]:
        """Register a cabin-panel call; these are never gated by the camera."""
        with self._lock:
            self.controller.add_car_call(floor)
            return self.snapshot()

    def set_camera_floor(self, floor: int) -> dict[str, Any]:
        if floor < 1 or floor > self.config.num_floors:
            raise ValueError(f"Floor must be between 1 and {self.config.num_floors}")
        with self._lock:
            if floor != self.camera_floor:
                self.camera_floor = floor
                self.controller.log(f"Camera assigned to floor {floor}")
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = self.controller.snapshot()
            state["camera_floor"] = self.camera_floor
            state["camera"] = dict(self.last_status)
            return state


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
        parts = self.path.strip("/").split("/")
        try:
            if parts[:2] == ["api", "hall"] and len(parts) == 4:
                self._json(self.simulation.request_hall(int(parts[2]), parts[3]))
            elif parts[:2] == ["api", "car"] and len(parts) == 3:
                self._json(self.simulation.request_car(int(parts[2])))
            elif parts[:2] == ["api", "camera-floor"] and len(parts) == 3:
                self._json(self.simulation.set_camera_floor(int(parts[2])))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error))

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
    if camera_floor < 1 or camera_floor > config.num_floors:
        raise ValueError(f"camera_floor must be between 1 and {config.num_floors}")
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
