"""Run real-time YOLO person detection against an elevator waiting ROI."""

import argparse
import time
from dataclasses import replace
from pathlib import Path

import cv2

try:
    from .config import AppConfig, CONFIG, parse_camera_source
    from .detector import PersonDetector
    from .roi import ROI
    from .tracking import TrackStatus, WaitingPersonAnalyzer, WaitingResult
except ImportError:  # Supports running main.py directly from this directory.
    from config import AppConfig, CONFIG, parse_camera_source
    from detector import PersonDetector
    from roi import ROI
    from tracking import TrackStatus, WaitingPersonAnalyzer, WaitingResult


def build_analyzer(app_config: AppConfig) -> WaitingPersonAnalyzer:
    return WaitingPersonAnalyzer(
        roi=ROI.from_tuple(app_config.roi),
        min_dwell_time=app_config.min_dwell_time,
        max_waiting_speed=app_config.max_waiting_speed,
        max_allowed_movement=app_config.max_allowed_movement,
        walk_confirm_time=app_config.walk_confirm_time,
        exit_grace=app_config.exit_grace,
        max_track_age=app_config.max_track_age,
        motion_window=app_config.motion_window,
    )


def draw_overlay(
    frame,
    detections,
    roi,
    statuses: dict[int, TrackStatus],
    result: WaitingResult,
    fps,
    threshold,
    max_waiting_speed,
    min_dwell_time,
):
    """Draw detections and operator-facing state on a frame."""
    roi_color = (0, 200, 0) if result.waiting_person else (0, 165, 255)
    cv2.rectangle(frame, (roi.left, roi.top), (roi.right, roi.bottom), roi_color, 2)
    cv2.putText(
        frame,
        "ELEVATOR ROI",
        (roi.left, max(25, roi.top - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        roi_color,
        2,
    )

    for detection in detections:
        left, top, right, bottom = detection.box
        track_status = statuses.get(detection.track_id) if detection.track_id is not None else None
        state = track_status.state.value if track_status else "OUTSIDE"
        color = {
            "VALID WAITING": (0, 255, 0),
            "WAITING": (0, 220, 255),
            "WALKING THROUGH": (0, 140, 255),
        }.get(state, (160, 160, 160))
        speed = track_status.speed if track_status else 0.0
        duration = track_status.dwell_time if track_status else 0.0
        track_label = "?" if detection.track_id is None else str(detection.track_id)
        label = f"Person #{track_label} {detection.confidence:.2f}"
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        cv2.putText(
            frame,
            label,
            (left, max(20, top - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
        )
        cv2.putText(
            frame,
            f"{state} {duration:.1f}s v={speed:.2f}",
            (left, min(frame.shape[0] - 10, bottom + 22)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            color,
            2,
        )

    status = "WAITING PERSON DETECTED" if result.waiting_person else "NO WAITING PERSON"
    status_color = (0, 255, 0) if result.waiting_person else (0, 165, 255)
    cv2.putText(frame, status, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, status_color, 2)
    cv2.putText(
        frame,
        f"People: {len(detections)}  Confidence: {threshold:.2f}",
        (20, 75),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
    )
    cv2.putText(
        frame,
        f"FPS: {fps:.1f}  Speed <= {max_waiting_speed:.2f} body/s  Dwell >= {min_dwell_time:.1f}s",
        (20, 105),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
    )
    return frame


def parse_roi(value: str) -> tuple[int, int, int, int]:
    """Parse an ROI supplied as left,top,right,bottom."""
    try:
        coordinates = tuple(int(part.strip()) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("ROI must contain four integers") from error
    if len(coordinates) != 4:
        raise argparse.ArgumentTypeError("ROI must be left,top,right,bottom")
    return coordinates  # type: ignore[return-value]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run YOLO elevator waiting-area detection")
    parser.add_argument("--camera", default=CONFIG.camera_source, help="Webcam index, URL, or video path")
    parser.add_argument("--model", default=str(CONFIG.model_path), help="YOLO weights path")
    parser.add_argument("--confidence", type=float, default=CONFIG.confidence_threshold)
    parser.add_argument(
        "--inference-image-size",
        type=int,
        default=CONFIG.inference_image_size,
        help="YOLO inference size; larger values improve small-person recall but reduce FPS",
    )
    parser.add_argument("--min-dwell-time", type=float, default=CONFIG.min_dwell_time)
    parser.add_argument(
        "--max-waiting-speed",
        type=float,
        default=CONFIG.max_waiting_speed,
        help="Foot speed (body-heights/s) above which a person counts as walking",
    )
    parser.add_argument(
        "--max-allowed-movement",
        type=float,
        default=CONFIG.max_allowed_movement,
        help="Optional pixel cap on foot drift over the motion window",
    )
    parser.add_argument("--walk-confirm-time", type=float, default=CONFIG.walk_confirm_time)
    parser.add_argument("--exit-grace", type=float, default=CONFIG.exit_grace)
    parser.add_argument("--max-track-age", type=float, default=CONFIG.max_track_age)
    parser.add_argument("--motion-window", type=float, default=CONFIG.motion_window)
    parser.add_argument(
        "--roi",
        type=parse_roi,
        default=CONFIG.roi,
        metavar="L,T,R,B",
        help="Waiting-zone rectangle based on the displayed 1280x720 image",
    )
    parser.add_argument("--device", default=CONFIG.device, help="Ultralytics device, such as cpu or 0")
    return parser


def config_from_args(args: argparse.Namespace) -> AppConfig:
    return replace(
        CONFIG,
        camera_source=args.camera,
        model_path=Path(args.model),
        confidence_threshold=args.confidence,
        inference_image_size=args.inference_image_size,
        min_dwell_time=args.min_dwell_time,
        max_waiting_speed=args.max_waiting_speed,
        max_allowed_movement=args.max_allowed_movement,
        walk_confirm_time=args.walk_confirm_time,
        exit_grace=args.exit_grace,
        max_track_age=args.max_track_age,
        motion_window=args.motion_window,
        roi=args.roi,
        device=args.device,
    )


def run(app_config: AppConfig = CONFIG) -> None:
    """Open the camera and run until the operator presses q or Escape."""
    roi = ROI.from_tuple(app_config.roi)
    detector = PersonDetector(
        model_path=app_config.model_path,
        confidence_threshold=app_config.confidence_threshold,
        device=app_config.device,
        inference_image_size=app_config.inference_image_size,
    )
    analyzer = build_analyzer(app_config)
    camera = cv2.VideoCapture(parse_camera_source(app_config.camera_source))
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, app_config.image_width)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, app_config.image_height)

    if not camera.isOpened():
        raise RuntimeError(
            f"Could not open camera source {app_config.camera_source!r}. "
            "Check camera_source in config.py."
        )

    previous_time = time.perf_counter()
    previous_presence = None
    try:
        while True:
            success, frame = camera.read()
            if not success:
                print("Camera frame could not be read; stopping.")
                break

            detections = detector.track(frame)
            statuses, waiting_result = analyzer.update(
                detections, time.perf_counter()
            )
            current_time = time.perf_counter()
            fps = 1.0 / max(current_time - previous_time, 1e-6)
            previous_time = current_time

            if waiting_result.waiting_person != previous_presence:
                print(
                    f"WAITING_PERSON = {'TRUE' if waiting_result.waiting_person else 'FALSE'} "
                    f"{waiting_result.as_dict()}",
                    flush=True,
                )
                previous_presence = waiting_result.waiting_person

            draw_overlay(
                frame,
                detections,
                roi,
                {status.track_id: status for status in statuses},
                waiting_result,
                fps,
                app_config.confidence_threshold,
                app_config.max_waiting_speed,
                app_config.min_dwell_time,
            )
            cv2.imshow(app_config.window_name, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
    finally:
        camera.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    run(config_from_args(build_parser().parse_args()))