"""Per-floor cameras: local webcams, IP/RTSP streams, and browser-pushed frames.

Each enabled floor gets a :class:`FloorCamera` with its own YOLO detector
(ByteTrack state lives inside the YOLO predictor, so trackers cannot be shared
between cameras), its own waiting-person analyzer, and its own worker thread.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Any, Callable

import cv2
import numpy as np

try:
    from .config import AppConfig, parse_camera_source
    from .detector import PersonDetector
    from .main import build_analyzer, draw_overlay
except ImportError:
    from config import AppConfig, parse_camera_source
    from detector import PersonDetector
    from main import build_analyzer, draw_overlay


SOURCE_KINDS = ("local", "url", "browser")
# A source with no new frame for this long is reported offline.
OFFLINE_AFTER = 3.0
RECONNECT_DELAY = 2.0
MAX_PUSH_BYTES = 5 * 1024 * 1024


class FrameSource:
    """Delivers the newest frame; ``read`` returns None if nothing new arrived."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._consumed = 0
        self.last_frame_time = 0.0
        self.error = ""

    def _publish(self, frame: np.ndarray) -> None:
        with self._cond:
            self._frame = frame
            self._seq += 1
            self.last_frame_time = time.monotonic()
            self.error = ""
            self._cond.notify_all()

    def read(self, timeout: float = 0.5) -> np.ndarray | None:
        with self._cond:
            if self._seq == self._consumed:
                self._cond.wait(timeout)
            if self._seq == self._consumed:
                return None
            self._consumed = self._seq
            return self._frame

    @property
    def online(self) -> bool:
        return time.monotonic() - self.last_frame_time < OFFLINE_AFTER

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class CaptureSource(FrameSource):
    """OpenCV capture (webcam index, IP camera, RTSP, or video file).

    A reader thread drains the stream continuously so inference always sees
    the latest frame instead of a growing backlog from a network camera.
    """

    def __init__(self, source: str, width: int = 1280, height: int = 720) -> None:
        super().__init__()
        self.source = source
        self.width = width
        self.height = height
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"capture-{source}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _open(self) -> cv2.VideoCapture | None:
        capture = cv2.VideoCapture(parse_camera_source(self.source))
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if isinstance(parse_camera_source(self.source), int):
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if capture.isOpened():
            return capture
        capture.release()
        self.error = f"Could not open camera source {self.source!r}"
        return None

    def _run(self) -> None:
        while not self._stop.is_set():
            capture = self._open()
            if capture is None:
                self._stop.wait(RECONNECT_DELAY)
                continue
            try:
                while not self._stop.is_set():
                    success, frame = capture.read()
                    if not success:
                        self.error = f"Lost stream {self.source!r}; reconnecting"
                        break
                    self._publish(frame)
            finally:
                capture.release()
            self._stop.wait(RECONNECT_DELAY)


class PushSource(FrameSource):
    """Frames uploaded as JPEG bodies by a phone/laptop browser."""

    def __init__(self) -> None:
        super().__init__()
        self.error = "Waiting for a device to connect"

    def push(self, jpeg: bytes) -> None:
        if len(jpeg) > MAX_PUSH_BYTES:
            raise ValueError("Frame too large")
        frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Body is not a JPEG/PNG image")
        self._publish(frame)


DetectorFactory = Callable[[AppConfig], Any]


def default_detector_factory(config: AppConfig) -> PersonDetector:
    return PersonDetector(
        model_path=config.model_path,
        confidence_threshold=config.confidence_threshold,
        device=config.device,
        inference_image_size=config.inference_image_size,
    )


class FloorCamera:
    """One floor's camera: source, detector, analyzer, and worker thread."""

    def __init__(
        self,
        floor: int,
        kind: str,
        source: str,
        config: AppConfig,
        detector_factory: DetectorFactory = default_detector_factory,
    ) -> None:
        if kind not in SOURCE_KINDS:
            raise ValueError(f"Camera kind must be one of {', '.join(SOURCE_KINDS)}")
        if kind != "browser" and not source.strip():
            raise ValueError("A camera index or stream URL is required")
        self.floor = floor
        self.kind = kind
        self.source_name = source if kind != "browser" else "browser"
        self.config = config
        self.source: FrameSource = (
            PushSource()
            if kind == "browser"
            else CaptureSource(source.strip(), config.image_width, config.image_height)
        )
        self._detector_factory = detector_factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.last_jpeg = b""
        self.result: dict[str, Any] = {
            "waiting_person": False,
            "person_id": None,
            "dwell_time": 0.0,
            "people_in_zone": 0,
            "tracks": [],
        }
        self.fps = 0.0
        self.detector_error = ""
        # Monotonic time someone was last seen in the waiting zone.
        self.last_present = time.monotonic()
        self._thread = threading.Thread(
            target=self._run, name=f"floor-{floor}-camera", daemon=True
        )

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.source.start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.source.stop()

    # -------------------------------------------------------------- queries
    @property
    def online(self) -> bool:
        return self.source.online and not self.detector_error

    @property
    def waiting_person(self) -> bool:
        with self._lock:
            return self.online and bool(self.result["waiting_person"])

    def absent_for(self, now: float | None = None) -> float:
        """Seconds since anyone was in the waiting zone (or the camera went offline)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if self.online and self.result["people_in_zone"]:
                return 0.0
            return max(0.0, now - self.last_present)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "floor": self.floor,
                "kind": self.kind,
                "source": self.source_name,
                "online": self.online,
                "error": self.detector_error or ("" if self.source.online else self.source.error),
                "fps": round(self.fps, 1),
                **self.result,
                "waiting_person": self.online and bool(self.result["waiting_person"]),
            }

    def jpeg(self) -> bytes:
        with self._lock:
            return self.last_jpeg

    # --------------------------------------------------------------- worker
    def _run(self) -> None:
        try:
            detector = self._detector_factory(self.config)
        except (FileNotFoundError, RuntimeError) as error:
            self.detector_error = str(error)
            return
        analyzer = None
        frame_size: tuple[int, int] | None = None
        min_interval = 1.0 / max(self.config.camera_max_fps, 0.1)
        previous = time.perf_counter()
        while not self._stop.is_set():
            frame = self.source.read(timeout=0.5)
            if frame is None:
                continue
            height, width = frame.shape[:2]
            if (width, height) != frame_size:
                frame_size = (width, height)
                analyzer = build_analyzer(replace(self.config, roi=self.scaled_roi(width, height)))
            try:
                detections = detector.track(frame)
            except Exception as error:  # Keep the floor alive through a bad frame.
                self.detector_error = f"Inference failed: {error}"
                continue
            self.detector_error = ""
            statuses, result = analyzer.update(detections, time.perf_counter())
            now = time.perf_counter()
            self.fps = 0.8 * self.fps + 0.2 / max(now - previous, 1e-6)
            previous = now
            draw_overlay(
                frame,
                detections,
                analyzer.roi,
                {status.track_id: status for status in statuses},
                result,
                self.fps,
                self.config.confidence_threshold,
                self.config.max_waiting_speed,
                self.config.min_dwell_time,
            )
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            with self._lock:
                if ok:
                    self.last_jpeg = encoded.tobytes()
                self.result = {
                    **result.as_dict(),
                    "dwell_time": round(result.dwell_time, 2),
                    "tracks": [
                        {
                            "id": status.track_id,
                            "state": status.state.value,
                            "dwell_time": round(status.dwell_time, 2),
                            "speed": round(status.speed, 2),
                            "inside_roi": status.inside_roi,
                        }
                        for status in statuses
                    ],
                }
                if result.people_in_zone:
                    self.last_present = time.monotonic()
            spare = min_interval - (time.perf_counter() - now)
            if spare > 0:
                self._stop.wait(spare)

    def scaled_roi(self, width: int, height: int) -> tuple[int, int, int, int]:
        """Map the ROI (given for image_width x image_height) onto this source's size."""
        sx = width / self.config.image_width
        sy = height / self.config.image_height
        left, top, right, bottom = self.config.roi
        return (
            round(left * sx),
            round(top * sy),
            max(round(right * sx), round(left * sx) + 1),
            max(round(bottom * sy), round(top * sy) + 1),
        )


class CameraManager:
    """Enable/disable cameras per floor at runtime."""

    def __init__(
        self,
        config: AppConfig,
        detector_factory: DetectorFactory = default_detector_factory,
    ) -> None:
        self.config = config
        self._detector_factory = detector_factory
        self._cameras: dict[int, FloorCamera] = {}
        self._lock = threading.Lock()

    def _validate_floor(self, floor: int) -> None:
        if floor < 1 or floor > self.config.num_floors:
            raise ValueError(f"Floor must be between 1 and {self.config.num_floors}")

    def get(self, floor: int) -> FloorCamera | None:
        with self._lock:
            return self._cameras.get(floor)

    def enable(self, floor: int, kind: str, source: str = "") -> FloorCamera:
        self._validate_floor(floor)
        camera = FloorCamera(floor, kind, source, self.config, self._detector_factory)
        with self._lock:
            previous = self._cameras.pop(floor, None)
            self._cameras[floor] = camera
        if previous:
            previous.stop()
        camera.start()
        return camera

    def disable(self, floor: int) -> bool:
        self._validate_floor(floor)
        with self._lock:
            camera = self._cameras.pop(floor, None)
        if camera is None:
            return False
        camera.stop()
        return True

    def push_frame(self, floor: int, jpeg: bytes) -> None:
        """Accept a browser frame, enabling a browser camera on first use."""
        camera = self.get(floor)
        if camera is None:
            camera = self.enable(floor, "browser")
        if not isinstance(camera.source, PushSource):
            raise ValueError(f"Floor {floor} uses a {camera.kind} camera, not a browser camera")
        camera.source.push(jpeg)

    def statuses(self) -> list[dict[str, Any]]:
        with self._lock:
            cameras = sorted(self._cameras.values(), key=lambda c: c.floor)
        return [camera.status() for camera in cameras]

    def stop_all(self) -> None:
        with self._lock:
            cameras = list(self._cameras.values())
            self._cameras.clear()
        for camera in cameras:
            camera.stop()
