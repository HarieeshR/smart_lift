"""YOLO adapter that converts model output into application detections."""

from pathlib import Path
from typing import Optional

try:
    from .roi import Detection
except ImportError:  # Supports running main.py directly from this directory.
    from roi import Detection


PERSON_CLASS_ID = 0


class PersonDetector:
    """Thin YOLO wrapper; the rest of the app does not depend on Ultralytics types."""

    def __init__(
        self,
        model_path: Path,
        confidence_threshold: float,
        device: Optional[str] = None,
    ) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as error:
            raise RuntimeError(
                "Ultralytics is not installed. Run: pip install -r requirements.txt"
            ) from error

        if not model_path.exists():
            raise FileNotFoundError(
                f"YOLO model not found at {model_path}. Download it or update model_path."
            )

        self.model = YOLO(str(model_path))
        self.confidence_threshold = confidence_threshold
        self.device = device

    def detect(self, frame) -> list[Detection]:
        """Run one independent inference without tracking IDs."""
        results = self.model.predict(
            source=frame,
            conf=self.confidence_threshold,
            device=self.device,
            verbose=False,
        )
        return self._detections_from_results(results)

    def track(self, frame) -> list[Detection]:
        """Run ByteTrack-persisted inference and return person track IDs."""
        results = self.model.track(
            source=frame,
            conf=self.confidence_threshold,
            device=self.device,
            persist=True,
            tracker="bytetrack.yaml",
            verbose=False,
        )
        return self._detections_from_results(results)

    def _detections_from_results(self, results) -> list[Detection]:
        """Convert Ultralytics results without leaking its types to the app."""
        if not results:
            return []

        boxes = results[0].boxes
        if boxes is None:
            return []

        track_ids = boxes.id.cpu().tolist() if boxes.id is not None else [None] * len(boxes)
        detections: list[Detection] = []
        for box, confidence, class_id, track_id in zip(
            boxes.xyxy.cpu().tolist(),
            boxes.conf.cpu().tolist(),
            boxes.cls.cpu().tolist(),
            track_ids,
        ):
            if int(class_id) != PERSON_CLASS_ID:
                continue
            left, top, right, bottom = (round(value) for value in box)
            detections.append(
                Detection(
                    box=(left, top, right, bottom),
                    confidence=float(confidence),
                    class_id=int(class_id),
                    track_id=None if track_id is None else int(track_id),
                )
            )
        return detections
