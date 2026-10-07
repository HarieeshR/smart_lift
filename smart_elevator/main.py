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
    from .tracking import StandingAnalyzer, TrackStatus
except ImportError:  # Supports running main.py directly from this directory.
    from config import AppConfig, CONFIG, parse_camera_source
    from detector import PersonDetector
    from roi import ROI
    from tracking import StandingAnalyzer, TrackStatus


def draw_overlay(
    frame,
    detections,
    roi,
    statuses: dict[int, TrackStatus],
    is_standing_person,
    fps,
    threshold,
    movement_threshold,
    standing_time,
):
    """Draw detections and operator-facing state on a frame."""
    roi_color = (0, 200, 0) if is_standing_person else (0, 165, 255)
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
        is_standing = track_status.is_standing if track_status else False
        color = (0, 255, 0) if is_standing else (255, 180, 0)
        movement = track_status.movement if track_status else 0.0
        duration = track_status.standing_time if track_status else 0.0
        movement_label = "STANDING" if is_standing else "WALKING"
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
            f"{movement_label} {duration:.1f}s d={movement:.1f}",
            (left, min(frame.shape[0] - 10, bottom + 22)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            color,
            2,
        )

    status = "STANDING PERSON DETECTED" if is_standing_person else "NO STANDING PERSON"
    status_color = (0, 255, 0) if is_standing_person else (0, 165, 255)
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
        f"FPS: {fps:.1f}  Move <= {movement_threshold:.1f}px  Dwell >= {standing_time:.1f}s",
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
    parser.add_argument("--movement-threshold", type=float, default=CONFIG.movement_threshold)
    parser.add_argument("--standing-time", type=float, default=CONFIG.standing_time)
    parser.add_argument("--max-track-age", type=float, default=CONFIG.max_track_age)
    parser.add_argument("--roi", type=parse_roi, default=CONFIG.roi, metavar="L,T,R,B")
    parser.add_argument("--device", default=CONFIG.device, help="Ultralytics device, such as cpu or 0")
    return parser


def config_from_args(args: argparse.Namespace) -> AppConfig:
    return replace(
        CONFIG,
        camera_source=args.camera,
        model_path=Path(args.model),
        confidence_threshold=args.confidence,
        movement_threshold=args.movement_threshold,
        standing_time=args.standing_time,
        max_track_age=args.max_track_age,
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
    )
    analyzer = StandingAnalyzer(
        roi=roi,
        movement_threshold=app_config.movement_threshold,
        standing_time=app_config.standing_time,
        max_track_age=app_config.max_track_age,
    )
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
            statuses, is_standing_person = analyzer.update(
                detections, time.perf_counter()
            )
            current_time = time.perf_counter()
            fps = 1.0 / max(current_time - previous_time, 1e-6)
            previous_time = current_time

            if is_standing_person != previous_presence:
                print(
                    f"STANDING_PERSON = {'TRUE' if is_standing_person else 'FALSE'}",
                    flush=True,
                )
                previous_presence = is_standing_person

            draw_overlay(
                frame,
                detections,
                roi,
                {status.track_id: status for status in statuses},
                is_standing_person,
                fps,
                app_config.confidence_threshold,
                app_config.movement_threshold,
                app_config.standing_time,
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