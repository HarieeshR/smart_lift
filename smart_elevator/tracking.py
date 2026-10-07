"""Temporal waiting-person state machine for persistent YOLO tracks."""

from dataclasses import dataclass
from enum import Enum
from collections import deque
from math import hypot

try:
    from .roi import Detection, ROI
except ImportError:  # Supports running main.py directly from this directory.
    from roi import Detection, ROI


class WaitingState(str, Enum):
    OUTSIDE = "OUTSIDE"
    ENTERED = "ENTERED"
    WAITING = "WAITING"
    WALKING_THROUGH = "WALKING THROUGH"
    VALID = "VALID WAITING"


@dataclass(frozen=True)
class WaitingResult:
    """Machine-readable aggregate result for the elevator controller."""

    waiting_person: bool
    person_id: int | None
    dwell_time: float

    def as_dict(self) -> dict[str, bool | int | float | None]:
        return {
            "waiting_person": self.waiting_person,
            "person_id": self.person_id,
            "dwell_time": self.dwell_time,
        }


@dataclass(frozen=True)
class TrackStatus:
    """Current state of one tracked person's waiting-zone visit."""

    track_id: int
    state: WaitingState
    dwell_time: float
    movement: float
    foot_position: tuple[float, float]
    inside_roi: bool


@dataclass
class _TrackState:
    previous_foot: tuple[float, float]
    recent_feet: deque[tuple[float, tuple[float, float]]]
    entry_time: float | None = None
    state: WaitingState = WaitingState.OUTSIDE
    last_seen: float = 0.0


class WaitingPersonAnalyzer:
    """Confirm a waiting person from continuous ROI dwell and foot movement."""

    def __init__(
        self,
        roi: ROI,
        min_dwell_time: float = 3.0,
        max_allowed_movement: float = 25.0,
        max_track_age: float = 1.0,
        motion_window: float = 0.75,
    ) -> None:
        if (
            min_dwell_time <= 0
            or max_allowed_movement < 0
            or max_track_age < 0
            or motion_window <= 0
        ):
            raise ValueError("Waiting settings must be valid positive values")
        self.roi = roi
        self.min_dwell_time = min_dwell_time
        self.max_allowed_movement = max_allowed_movement
        self.max_track_age = max_track_age
        self.motion_window = motion_window
        self._tracks: dict[int, _TrackState] = {}
        self._result = WaitingResult(False, None, 0.0)

    @property
    def waiting_person(self) -> bool:
        return self._result.waiting_person

    @property
    def result(self) -> WaitingResult:
        return self._result

    def _movement_in_window(
        self,
        state: _TrackState,
        timestamp: float,
        foot: tuple[float, float],
    ) -> float:
        state.recent_feet.append((timestamp, foot))
        while (
            len(state.recent_feet) > 1
            and timestamp - state.recent_feet[0][0] > self.motion_window
        ):
            state.recent_feet.popleft()
        x_values = [point[0] for _, point in state.recent_feet]
        y_values = [point[1] for _, point in state.recent_feet]
        return hypot(max(x_values) - min(x_values), max(y_values) - min(y_values))

    def _status(
        self,
        track_id: int,
        state: _TrackState,
        foot: tuple[float, float],
        movement: float,
        timestamp: float,
    ) -> TrackStatus:
        inside = self.roi.contains(foot)
        dwell = (
            0.0
            if state.entry_time is None
            else max(0.0, timestamp - state.entry_time)
        )
        return TrackStatus(track_id, state.state, dwell, movement, foot, inside)

    def update(
        self,
        detections: list[Detection],
        timestamp: float,
    ) -> tuple[list[TrackStatus], WaitingResult]:
        """Update all tracks and return per-track statuses plus WAITING_PERSON."""
        statuses: list[TrackStatus] = []
        for detection in detections:
            if detection.track_id is None:
                continue
            track_id = detection.track_id
            foot = detection.foot_position
            inside = self.roi.contains(foot)
            state = self._tracks.get(track_id)
            if state is None or timestamp - state.last_seen > self.max_track_age:
                state = _TrackState(
                    previous_foot=foot,
                    recent_feet=deque([(timestamp, foot)]),
                    entry_time=timestamp if inside else None,
                    state=WaitingState.ENTERED if inside else WaitingState.OUTSIDE,
                    last_seen=timestamp,
                )
                self._tracks[track_id] = state
                movement = 0.0
            else:
                movement = max(
                    hypot(
                        foot[0] - state.previous_foot[0],
                        foot[1] - state.previous_foot[1],
                    ),
                    self._movement_in_window(state, timestamp, foot),
                )
                if not inside:
                    state.entry_time = None
                    state.state = WaitingState.OUTSIDE
                elif movement > self.max_allowed_movement:
                    state.entry_time = timestamp
                    state.state = WaitingState.WALKING_THROUGH
                elif state.entry_time is None:
                    state.entry_time = timestamp
                    state.state = WaitingState.ENTERED
                elif timestamp - state.entry_time >= self.min_dwell_time:
                    state.state = WaitingState.VALID
                else:
                    state.state = WaitingState.WAITING
                state.previous_foot = foot
                state.last_seen = timestamp
            statuses.append(self._status(track_id, state, foot, movement, timestamp))

        expired = [
            track_id
            for track_id, state in self._tracks.items()
            if timestamp - state.last_seen > self.max_track_age
        ]
        for track_id in expired:
            del self._tracks[track_id]

        valid = [
            (track_id, state)
            for track_id, state in self._tracks.items()
            if state.state is WaitingState.VALID
            and timestamp - state.last_seen <= self.max_track_age
        ]
        if valid:
            person_id, person_state = max(
                valid,
                key=lambda item: timestamp
                - (
                    item[1].entry_time
                    if item[1].entry_time is not None
                    else timestamp
                ),
            )
            entry_time = (
                person_state.entry_time
                if person_state.entry_time is not None
                else timestamp
            )
            self._result = WaitingResult(
                True,
                person_id,
                max(0.0, timestamp - entry_time),
            )
        else:
            self._result = WaitingResult(False, None, 0.0)
        return statuses, self._result


# Compatibility alias for callers importing the old class name.
StandingAnalyzer = WaitingPersonAnalyzer
