"""Region-of-interest geometry and person-presence decision logic."""

from dataclasses import dataclass
from typing import Iterable, Tuple


BoundingBox = Tuple[int, int, int, int]


@dataclass(frozen=True)
class Detection:
    """A person detection in pixel coordinates."""

    box: BoundingBox
    confidence: float
    class_id: int = 0
    track_id: int | None = None

    @property
    def bottom_center(self) -> tuple[int, int]:
        left, top, right, bottom = self.box
        return ((left + right) // 2, bottom)

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.box
        return ((left + right) // 2, (top + bottom) // 2)


@dataclass(frozen=True)
class ROI:
    """Rectangular waiting area, represented as left, top, right, bottom."""

    left: int
    top: int
    right: int
    bottom: int

    @classmethod
    def from_tuple(cls, coordinates: tuple[int, int, int, int]) -> "ROI":
        left, top, right, bottom = coordinates
        if left >= right or top >= bottom:
            raise ValueError("ROI must have positive width and height")
        return cls(left, top, right, bottom)

    def contains(self, point: tuple[int, int]) -> bool:
        x, y = point
        return self.left <= x <= self.right and self.top <= y <= self.bottom

    def contains_detection(self, detection: Detection) -> bool:
        """Use bottom-center so a standing person's feet define their location."""
        return self.contains(detection.bottom_center)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom


def person_present(
    detections: Iterable[Detection],
    roi: ROI,
    confidence_threshold: float,
) -> bool:
    """Return true if any sufficiently confident person is standing in the ROI."""
    return any(
        detection.class_id == 0
        and detection.confidence >= confidence_threshold
        and roi.contains_detection(detection)
        for detection in detections
    )
