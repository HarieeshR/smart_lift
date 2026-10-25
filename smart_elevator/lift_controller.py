"""LOOK elevator scheduler with hall calls, car calls, and door timing.

The controller is pure Python (no camera or web dependencies) so it can be
unit-tested with synthetic ticks.  The lift keeps travelling in its current
direction while any call lies ahead of it, stopping at floors with a car call
or a hall call in the travel direction.  When nothing remains ahead it turns
around (LOOK), rather than running to the end of the shaft (SCAN).
"""

from __future__ import annotations

import copy
import math
import time
from collections import deque
from enum import Enum
from typing import Any


class Direction(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    IDLE = "IDLE"

    @property
    def opposite(self) -> "Direction":
        if self is Direction.UP:
            return Direction.DOWN
        if self is Direction.DOWN:
            return Direction.UP
        return Direction.IDLE


class DoorState(str, Enum):
    CLOSED = "CLOSED"
    OPENING = "OPENING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"


class LookController:
    """Single-car elevator controller using the LOOK scheduling algorithm."""

    def __init__(
        self,
        num_floors: int = 5,
        speed: float = 0.5,
        door_open_time: float = 3.0,
        door_transition_time: float = 1.0,
        start_floor: int = 1,
    ) -> None:
        if num_floors < 2:
            raise ValueError("num_floors must be at least 2")
        self.num_floors = num_floors
        self.speed = speed
        self.door_open_time = door_open_time
        self.door_transition_time = door_transition_time
        self.position = float(start_floor)
        self.direction = Direction.IDLE
        self.moving = False
        self.door_state = DoorState.CLOSED
        self.door_timer = 0.0
        self.hall_calls: set[tuple[int, Direction]] = set()
        self.car_calls: set[int] = set()
        self.stops: deque[int] = deque(maxlen=50)
        self.events: deque[dict[str, Any]] = deque(maxlen=12)

    # ------------------------------------------------------------------ calls
    @property
    def current_floor(self) -> int:
        return int(round(self.position))

    def _validate_floor(self, floor: int) -> None:
        if floor < 1 or floor > self.num_floors:
            raise ValueError(f"Floor must be between 1 and {self.num_floors}")

    def log(self, message: str) -> None:
        self.events.appendleft({"time": time.time(), "message": message})

    def add_car_call(self, floor: int) -> None:
        self._validate_floor(floor)
        if not self.moving and self.current_floor == floor:
            self._reopen_doors()
            return
        if floor not in self.car_calls:
            self.car_calls.add(floor)
            self.log(f"Car call registered: floor {floor}")

    def add_hall_call(self, floor: int, direction: Direction) -> None:
        self._validate_floor(floor)
        direction = Direction(direction)
        if direction is Direction.IDLE:
            raise ValueError("Hall call direction must be UP or DOWN")
        if direction is Direction.UP and floor == self.num_floors:
            raise ValueError("Top floor has no UP hall call")
        if direction is Direction.DOWN and floor == 1:
            raise ValueError("Bottom floor has no DOWN hall call")
        if (
            not self.moving
            and self.current_floor == floor
            and self.door_state is not DoorState.CLOSED
            and self.direction in (direction, Direction.IDLE)
        ):
            self.direction = direction
            self._reopen_doors()
            return
        if (floor, direction) not in self.hall_calls:
            self.hall_calls.add((floor, direction))
            self.log(f"Hall call registered: floor {floor} {direction.value}")

    def cancel_hall_call(self, floor: int, direction: Direction, reason: str) -> bool:
        key = (floor, Direction(direction))
        if key not in self.hall_calls:
            return False
        self.hall_calls.discard(key)
        self.log(f"Floor {floor} {key[1].value} call cancelled: {reason}")
        return True

    def has_calls(self) -> bool:
        return bool(self.hall_calls or self.car_calls)

    def _call_floors(self) -> set[int]:
        return self.car_calls | {floor for floor, _ in self.hall_calls}

    def _calls_beyond(self, floor: int, direction: Direction) -> bool:
        if direction is Direction.UP:
            return any(f > floor for f in self._call_floors())
        if direction is Direction.DOWN:
            return any(f < floor for f in self._call_floors())
        return False

    # ------------------------------------------------------------------- tick
    def tick(self, elapsed: float) -> None:
        """Advance doors and car position by ``elapsed`` seconds."""
        if elapsed <= 0:
            return
        if self.door_state is not DoorState.CLOSED:
            self._advance_doors(elapsed)
            return
        if not self.moving:
            self._decide_at_floor()
            if not self.moving:
                return
        self._move(elapsed)

    def _advance_doors(self, elapsed: float) -> None:
        self.door_timer += elapsed
        if self.door_state is DoorState.OPENING and self.door_timer >= self.door_transition_time:
            self.door_state, self.door_timer = DoorState.OPEN, 0.0
        elif self.door_state is DoorState.OPEN and self.door_timer >= self.door_open_time:
            self.door_state, self.door_timer = DoorState.CLOSING, 0.0
        elif self.door_state is DoorState.CLOSING and self.door_timer >= self.door_transition_time:
            self.door_state, self.door_timer = DoorState.CLOSED, 0.0
            if not self.has_calls():
                self.direction = Direction.IDLE

    def _reopen_doors(self) -> None:
        if self.door_state is DoorState.OPEN:
            self.door_timer = 0.0
        elif self.door_state is DoorState.CLOSING:
            self.door_state = DoorState.OPENING
            self.door_timer = max(self.door_transition_time - self.door_timer, 0.0)
        elif self.door_state is DoorState.CLOSED:
            self.door_state, self.door_timer = DoorState.OPENING, 0.0
        self.log(f"Doors opened at floor {self.current_floor}")

    def _should_stop(self, floor: int, direction: Direction) -> bool:
        if floor in self.car_calls:
            return True
        if direction is Direction.IDLE:
            return any(f == floor for f, _ in self.hall_calls)
        if (floor, direction) in self.hall_calls:
            return True
        return (floor, direction.opposite) in self.hall_calls and not self._calls_beyond(
            floor, direction
        )

    def _decide_at_floor(self) -> None:
        floor = self.current_floor
        if self._should_stop(floor, self.direction):
            self._stop_at(floor)
            return
        self.direction = self._choose_direction(floor)
        self.moving = self.direction is not Direction.IDLE

    def _choose_direction(self, floor: int) -> Direction:
        current = self.direction
        if current is not Direction.IDLE:
            if self._calls_beyond(floor, current):
                return current
            if self._calls_beyond(floor, current.opposite):
                return current.opposite
            return Direction.IDLE
        floors = self._call_floors()
        if not floors:
            return Direction.IDLE
        nearest = min(floors, key=lambda f: (abs(f - floor), -f))
        return Direction.UP if nearest > floor else Direction.DOWN

    def _move(self, elapsed: float) -> None:
        sign = 1 if self.direction is Direction.UP else -1
        start = self.position
        end = start + sign * self.speed * elapsed
        if sign > 0:
            crossed = range(math.floor(start) + 1, math.floor(end) + 1)
        else:
            crossed = range(math.ceil(start) - 1, math.ceil(end) - 1, -1)
        for floor in crossed:
            if self._should_stop(floor, self.direction):
                self.position = float(floor)
                self._stop_at(floor)
                return
            if not self._calls_beyond(floor, self.direction):
                # Calls were cancelled mid-travel: park at the next floor.
                self.position = float(floor)
                self.moving = False
                return
        self.position = min(max(end, 1.0), float(self.num_floors))

    def _stop_at(self, floor: int) -> None:
        """Serve the calls at ``floor``, pick the departing direction, open doors."""
        direction = self.direction
        self.moving = False
        self.car_calls.discard(floor)
        if direction is Direction.IDLE:
            if (floor, Direction.UP) in self.hall_calls:
                direction = Direction.UP
            elif (floor, Direction.DOWN) in self.hall_calls:
                direction = Direction.DOWN
        if (floor, direction) in self.hall_calls:
            self.hall_calls.discard((floor, direction))
        elif direction is not Direction.IDLE and not self._calls_beyond(floor, direction):
            if (floor, direction.opposite) in self.hall_calls:
                direction = direction.opposite
                self.hall_calls.discard((floor, direction))
            elif not self.has_calls():
                direction = Direction.IDLE
        self.direction = direction
        self.door_state, self.door_timer = DoorState.OPENING, 0.0
        self.stops.append(floor)
        suffix = "" if direction is Direction.IDLE else f", going {direction.value}"
        self.log(f"Stopped at floor {floor}{suffix}")

    # --------------------------------------------------------------- reports
    def planned_stops(self, limit: int = 50) -> list[int]:
        """Return the floors the LOOK sweep will stop at, in service order."""
        clone = copy.copy(self)
        clone.hall_calls = set(self.hall_calls)
        clone.car_calls = set(self.car_calls)
        clone.stops = deque()
        clone.events = deque(maxlen=1)
        clone.speed = 1.0
        clone.door_open_time = 0.0
        clone.door_transition_time = 0.0
        for _ in range(limit * 6):
            if not clone.has_calls() and not clone.moving:
                break
            clone.tick(1.0)
        return list(clone.stops)[:limit]

    def door_progress(self) -> float:
        transition = max(self.door_transition_time, 1e-6)
        if self.door_state is DoorState.OPENING:
            return min(self.door_timer / transition, 1.0)
        if self.door_state is DoorState.OPEN:
            return 1.0
        if self.door_state is DoorState.CLOSING:
            return max(1.0 - self.door_timer / transition, 0.0)
        return 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "num_floors": self.num_floors,
            "position": round(self.position, 3),
            "current_floor": self.current_floor,
            "direction": self.direction.value,
            "moving": self.moving,
            "door_state": self.door_state.value,
            "door_progress": round(self.door_progress(), 3),
            "hall_calls": [
                {"floor": floor, "direction": direction.value}
                for floor, direction in sorted(self.hall_calls)
            ],
            "car_calls": sorted(self.car_calls),
            "planned_stops": self.planned_stops(),
            "events": list(self.events),
        }
