"""Temporal standing classification for YOLO person tracks."""

from dataclasses import dataclass
from math import hypot

try:
    from .roi import Detection, ROI
except ImportError:  # Supports running main.py directly from this directory.
    from roi import Detection, ROI


@dataclass(frozen=True)
class TrackStatus:
    """Current movement and standing state for one tracked person."""

    track_id: int
    movement: float
    standing_time: float
    inside_roi: bool
    is_standing: bool


@dataclass
class _TrackState:
    previous_center: tuple[int, int]
    stable_since: float | None = None
    last_seen: float = 0.0


class StandingAnalyzer:
    """Confirm a person as standing only after stationary ROI dwell time."""

    def __init__(
        self,
        roi: ROI,
        movement_threshold: float = 20.0,
        standing_time: float = 2.0,
        max_track_age: float = 1.0,
    ) -> None:
        if movement_threshold < 0 or standing_time < 0 or max_track_age < 0:
            raise ValueError("Standing settings must not be negative")
        self.roi = roi
        self.movement_threshold = movement_threshold
        self.standing_time = standing_time
        self.max_track_age = max_track_age
        self._tracks: dict[int, _TrackState] = {}

    def update(
        self,
        detections: list[Detection],
        timestamp: float,
    ) -> tuple[list[TrackStatus], bool]:
        """Update tracks and return statuses plus aggregate standing state."""
        statuses: list[TrackStatus] = []
        for detection in detections:
            if detection.track_id is None:
                continue
            center = detection.center
            inside_roi = self.roi.contains_detection(detection)
            state = self._tracks.get(detection.track_id)
            if state is None:
                state = _TrackState(
                    previous_center=center,
                    stable_since=timestamp if inside_roi else None,
                    last_seen=timestamp,
                )
                self._tracks[detection.track_id] = state
                movement = 0.0
            else:
                movement = hypot(
                    center[0] - state.previous_center[0],
                    center[1] - state.previous_center[1],
                )
                if inside_roi and movement <= self.movement_threshold:
                    if state.stable_since is None:
                        state.stable_since = timestamp
                else:
                    state.stable_since = None
                state.previous_center = center
                state.last_seen = timestamp

            standing_time = (
                0.0
                if state.stable_since is None
                else max(0.0, timestamp - state.stable_since)
            )
            is_standing = (
                inside_roi
                and state.stable_since is not None
                and standing_time >= self.standing_time
            )
            statuses.append(
                TrackStatus(
                    track_id=detection.track_id,
                    movement=movement,
                    standing_time=standing_time,
                    inside_roi=inside_roi,
                    is_standing=is_standing,
                )
            )

        expired_ids = [
            track_id
            for track_id, state in self._tracks.items()
            if timestamp - state.last_seen > self.max_track_age
        ]
        for track_id in expired_ids:
            del self._tracks[track_id]

        return statuses, any(status.is_standing for status in statuses)