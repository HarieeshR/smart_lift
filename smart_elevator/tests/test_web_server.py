from dataclasses import replace

import pytest

from smart_elevator.config import CONFIG
from smart_elevator.web_server import LiftSimulation


def simulation(camera_floor: int = 1) -> LiftSimulation:
    return LiftSimulation(
        replace(
            CONFIG,
            lift_speed_floors_per_second=1.0,
            door_open_time=1.0,
            door_transition_time=0.5,
        ),
        camera_floor=camera_floor,
    )


def latest_event(status: dict) -> str:
    return status["events"][0]["message"]


def test_camera_floor_hall_call_is_rejected_without_detection():
    lift = simulation(camera_floor=2)

    status = lift.request_hall(2, "up")

    assert status["hall_calls"] == []
    assert "rejected" in latest_event(status)


def test_camera_floor_hall_call_is_accepted_with_waiting_person():
    lift = simulation(camera_floor=2)
    lift.last_status["waiting_person"] = True

    status = lift.request_hall(2, "up")

    assert status["hall_calls"] == [{"floor": 2, "direction": "UP"}]
    assert status["planned_stops"] == [2]


def test_camera_floor_hall_call_is_cancelled_when_person_leaves():
    lift = simulation(camera_floor=4)
    lift.last_status["waiting_person"] = True
    lift.request_hall(4, "down")
    lift.controller.tick(1.0)

    lift.last_status["waiting_person"] = False
    lift._enforce_camera_gating()

    status = lift.snapshot()
    assert status["hall_calls"] == []
    assert "cancelled" in latest_event(status)


def test_other_floors_and_car_calls_are_not_gated():
    lift = simulation(camera_floor=1)

    lift.request_hall(3, "down")
    status = lift.request_car(5)

    assert status["hall_calls"] == [{"floor": 3, "direction": "DOWN"}]
    assert status["car_calls"] == [5]
    assert status["planned_stops"] == [5, 3]


def test_set_camera_floor_changes_gated_floor():
    lift = simulation(camera_floor=1)

    status = lift.set_camera_floor(3)

    assert status["camera_floor"] == 3
    assert lift.request_hall(3, "up")["hall_calls"] == []
    with pytest.raises(ValueError):
        lift.set_camera_floor(9)


def test_invalid_hall_direction_is_rejected():
    lift = simulation()
    with pytest.raises(ValueError):
        lift.request_hall(2, "sideways")
    with pytest.raises(ValueError):
        lift.request_hall(5, "up")
