"""Temporal waiting-person state machine for persistent YOLO tracks.

A person counts as *waiting* once their feet have stayed inside the ROI for
``min_dwell_time`` seconds without sustained travel.  Travel is measured as
the net drift of a smoothed foot position over ``motion_window`` seconds,
divided by the person's box height, so the threshold means the same thing for
someone near the camera (large box) and someone far away (small box).
Short glitches are absorbed by hysteresis:

* motion must stay above the limit for ``walk_confirm_time`` before a track is
  marked ``WALKING THROUGH`` (one jittery box does not reset the dwell);
* feet may leave the ROI for up to ``exit_grace`` seconds without losing the
  visit (ROI-edge jitter);
* a track survives detector misses for up to ``max_track_age`` seconds.
"""

from dataclasses import dataclass
from enum import Enum
from collections import deque
from math import exp, hypot

try:
    from .roi import Detection, ROI
except ImportError:  # Supports running main.py directly from this directory.
    from roi import Detection, ROI


# Time constant of the foot/box-height smoothing filter, in seconds.
SMOOTHING_TAU = 0.15
# Speed is never computed over less than this span, to avoid dividing tiny
# jitters by tiny time steps right after a track appears.
MIN_SPEED_SPAN = 0.3


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
    people_in_zone: int = 0

    def as_dict(self) -> dict[str, bool | int | float | None]:
        return {
            "waiting_person": self.waiting_person,
            "person_id": self.person_id,
            "dwell_time": self.dwell_time,
            "people_in_zone": self.people_in_zone,
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
    speed: float = 0.0


@dataclass
class _TrackState:
    smooth_foot: tuple[float, float]
    recent_feet: deque[tuple[float, tuple[float, float]]]
    box_height: float
    entry_time: float | None = None
    state: WaitingState = WaitingState.OUTSIDE
    last_seen: float = 0.0
    last_inside: float | None = None
    moving_since: float | None = None


class WaitingPersonAnalyzer:
    """Confirm a waiting person from continuous ROI dwell and foot movement."""

    def __init__(
        self,
        roi: ROI,
        min_dwell_time: float = 2.0,
        max_waiting_speed: float = 0.35,
        max_allowed_movement: float | None = None,
        walk_confirm_time: float = 0.4,
        exit_grace: float = 0.7,
        max_track_age: float = 1.5,
        motion_window: float = 1.0,
    ) -> None:
        if (
            min_dwell_time <= 0
            or max_waiting_speed <= 0
            or (max_allowed_movement is not None and max_allowed_movement < 0)
            or walk_confirm_time < 0
            or exit_grace < 0
            or max_track_age < 0
            or motion_window <= 0
        ):
            raise ValueError("Waiting settings must be valid positive values")
        self.roi = roi
        self.min_dwell_time = min_dwell_time
        self.max_waiting_speed = max_waiting_speed
        self.max_allowed_movement = max_allowed_movement
        self.walk_confirm_time = walk_confirm_time
        self.exit_grace = exit_grace
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

    def _motion(
        self, state: _TrackState, timestamp: float
    ) -> tuple[float, float]:
        """Return (net drift in pixels, speed in body-heights/s) over the window."""
        recent = state.recent_feet
        recent.append((timestamp, state.smooth_foot))
        # Keep one sample at or beyond the window edge as the drift anchor.
        while len(recent) > 2 and timestamp - recent[1][0] >= self.motion_window:
            recent.popleft()
        anchor_time, anchor = recent[0]
        drift = hypot(
            state.smooth_foot[0] - anchor[0], state.smooth_foot[1] - anchor[1]
        )
        span = max(timestamp - anchor_time, MIN_SPEED_SPAN)
        return drift, drift / max(state.box_height, 1.0) / span

    def _is_moving(self, drift: float, speed: float) -> bool:
        if speed > self.max_waiting_speed:
            return True
        return self.max_allowed_movement is not None and drift > self.max_allowed_movement

    def _new_track(
        self, detection: Detection, timestamp: float, inside: bool
    ) -> _TrackState:
        foot = detection.foot_position
        return _TrackState(
            smooth_foot=foot,
            recent_feet=deque([(timestamp, foot)]),
            box_height=max(1.0, detection.box[3] - detection.box[1]),
            entry_time=timestamp if inside else None,
            state=WaitingState.ENTERED if inside else WaitingState.OUTSIDE,
            last_seen=timestamp,
            last_inside=timestamp if inside else None,
        )

    def _advance(
        self,
        state: _TrackState,
        detection: Detection,
        timestamp: float,
        inside: bool,
    ) -> tuple[float, float]:
        elapsed = max(timestamp - state.last_seen, 0.0)
        alpha = 1.0 - exp(-elapsed / SMOOTHING_TAU) if elapsed > 0 else 1.0
        foot = detection.foot_position
        state.smooth_foot = (
            state.smooth_foot[0] + alpha * (foot[0] - state.smooth_foot[0]),
            state.smooth_foot[1] + alpha * (foot[1] - state.smooth_foot[1]),
        )
        height = max(1.0, detection.box[3] - detection.box[1])
        state.box_height += alpha * (height - state.box_height)
        state.last_seen = timestamp
        drift, speed = self._motion(state, timestamp)

        if self._is_moving(drift, speed):
            if state.moving_since is None:
                state.moving_since = timestamp
        else:
            state.moving_since = None
        walking = (
            state.moving_since is not None
            and timestamp - state.moving_since >= self.walk_confirm_time
        )

        if inside:
            state.last_inside = timestamp
        elif state.last_inside is None or timestamp - state.last_inside > self.exit_grace:
            state.entry_time = None
            state.state = WaitingState.OUTSIDE
            return drift, speed
        else:
            # Briefly outside: keep the visit (and its dwell) untouched.
            return drift, speed

        if walking:
            state.entry_time = None
            state.state = WaitingState.WALKING_THROUGH
        elif state.entry_time is None:
            state.entry_time = timestamp
            state.state = (
                WaitingState.WAITING
                if state.state is WaitingState.WALKING_THROUGH
                else WaitingState.ENTERED
            )
        elif timestamp - state.entry_time >= self.min_dwell_time:
            state.state = WaitingState.VALID
        else:
            state.state = WaitingState.WAITING
        return drift, speed

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
            inside = self.roi.contains_detection(detection)
            state = self._tracks.get(track_id)
            if state is None or timestamp - state.last_seen > self.max_track_age:
                state = self._new_track(detection, timestamp, inside)
                self._tracks[track_id] = state
                drift, speed = 0.0, 0.0
            else:
                drift, speed = self._advance(state, detection, timestamp, inside)
            dwell = (
                0.0
                if state.entry_time is None
                else max(0.0, timestamp - state.entry_time)
            )
            statuses.append(
                TrackStatus(
                    track_id,
                    state.state,
                    dwell,
                    drift,
                    detection.foot_position,
                    inside,
                    speed,
                )
            )

        expired = [
            track_id
            for track_id, state in self._tracks.items()
            if timestamp - state.last_seen > self.max_track_age
        ]
        for track_id in expired:
            del self._tracks[track_id]

        in_zone = sum(
            1
            for state in self._tracks.values()
            if state.state is not WaitingState.OUTSIDE
        )
        valid = [
            (track_id, state)
            for track_id, state in self._tracks.items()
            if state.state is WaitingState.VALID
        ]
        if valid:
            person_id, person_state = max(
                valid,
                key=lambda item: (
                    item[1].box_height,
                    timestamp
                    - (
                        item[1].entry_time
                        if item[1].entry_time is not None
                        else timestamp
                    ),
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
                in_zone,
            )
        else:
            self._result = WaitingResult(False, None, 0.0, in_zone)
        return statuses, self._result


# Compatibility alias for callers importing the old class name.
StandingAnalyzer = WaitingPersonAnalyzer
