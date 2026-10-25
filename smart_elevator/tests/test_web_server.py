from dataclasses import replace

from smart_elevator.config import CONFIG
from smart_elevator.web_server import LiftSimulation


def simulation() -> LiftSimulation:
    return LiftSimulation(
        replace(CONFIG, lift_update_interval=0.05, lift_speed_floors_per_second=1.0)
    )


def test_floor_request_is_cancelled_without_detection():
    lift = simulation()

    status = lift.request_floor(4)

    assert status["camera_floor"] == 4
    assert status["requests"] == []
    assert status["target_floor"] is None
    assert "cancelled" in status["camera"]["event"]


def test_detected_person_starts_camera_floor_request():
    lift = simulation()
    lift.last_status["waiting_person"] = True
    lift.request_floor(4)

    assert lift.target_floor == 4
    assert "accepted" in lift.last_status["event"]


def test_lift_moves_continuously_without_floor_stops():
    lift = simulation()
    lift.last_status["waiting_person"] = True
    lift.request_floor(4)

    lift._advance_lift(1.0)

    assert lift.direction == "UP"
    assert lift.lift_position == 2.0
    assert lift.target_floor == 4
    assert lift.requests == {4: lift.requests[4]}

    lift._advance_lift(1.5)

    assert lift.lift_position == 3.5
    assert lift.target_floor == 4

    lift._advance_lift(0.5)

    assert lift.lift_position == 4.0
    assert lift.target_floor is None
    assert lift.requests == {}


def test_missing_person_cancels_request_during_travel():
    lift = simulation()
    lift.last_status["waiting_person"] = True
    lift.request_floor(4)
    lift._advance_lift(1.0)

    lift.last_status["waiting_person"] = False
    lift._advance_lift(0.05)

    assert lift.lift_position == 2.0
    assert lift.target_floor is None
    assert lift.requests == {}
    assert "cancelled" in lift.last_status["event"]
