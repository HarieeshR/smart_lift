from dataclasses import replace

import cv2
import numpy as np
import pytest

from smart_elevator.cameras import CameraManager, FloorCamera
from smart_elevator.config import CONFIG
from smart_elevator.web_server import LiftSimulation


class FakeCamera:
    def __init__(self, floor, online=True, waiting=False, absent=0.0):
        self.floor = floor
        self.online = online
        self.waiting_person = waiting
        self.absent = absent

    def absent_for(self, now=None):
        return self.absent

    def status(self):
        return {"floor": self.floor, "online": self.online, "waiting_person": self.waiting_person}


class FakeCameras:
    def __init__(self):
        self.cameras = {}

    def get(self, floor):
        return self.cameras.get(floor)

    def enable(self, floor, kind, source=""):
        if floor < 1 or floor > CONFIG.num_floors:
            raise ValueError("bad floor")
        self.cameras[floor] = FakeCamera(floor)
        return self.cameras[floor]

    def disable(self, floor):
        return self.cameras.pop(floor, None) is not None

    def statuses(self):
        return [camera.status() for _, camera in sorted(self.cameras.items())]

    def stop_all(self):
        self.cameras.clear()


def simulation(*camera_floors, **camera_state):
    cameras = FakeCameras()
    for floor in camera_floors:
        cameras.cameras[floor] = FakeCamera(floor, **camera_state)
    return LiftSimulation(
        replace(
            CONFIG,
            lift_speed_floors_per_second=1.0,
            door_open_time=1.0,
            door_transition_time=0.5,
            person_lost_timeout=2.0,
        ),
        cameras=cameras,
    )


def latest_event(status: dict) -> str:
    return status["events"][0]["message"]


def test_camera_floor_hall_call_is_rejected_without_detection():
    lift = simulation(2)

    status = lift.request_hall(2, "up")

    assert status["floor_calls"] == []
    assert status["hall_calls"] == []
    assert "no waiting person" in latest_event(status)


def test_camera_floor_hall_call_is_rejected_when_camera_offline():
    lift = simulation(2, online=False, waiting=True)

    status = lift.request_hall(2, "up")

    assert status["floor_calls"] == []
    assert "camera offline" in latest_event(status)


def test_verified_call_stops_in_either_direction():
    lift = simulation(3, waiting=True)
    lift.request_car(5)

    status = lift.request_hall(3, "down")

    assert status["floor_calls"] == [3]
    assert status["hall_calls"] == []
    assert status["planned_stops"] == [3, 5]


def test_call_is_kept_through_a_short_absence_then_cancelled():
    lift = simulation(4, waiting=True)
    lift.request_hall(4, "down")
    lift.request_car(5)
    camera = lift.cameras.get(4)

    camera.absent = 1.0
    lift._enforce_camera_gating()
    assert lift.snapshot()["floor_calls"] == [4]

    camera.absent = 2.5
    lift._enforce_camera_gating()
    status = lift.snapshot()
    assert status["floor_calls"] == []
    assert status["planned_stops"] == [5]
    assert "cancelled" in latest_event(status)


def test_floors_without_cameras_and_car_calls_are_not_gated():
    lift = simulation(1)

    lift.request_hall(3, "down")
    status = lift.request_car(5)

    assert status["hall_calls"] == [{"floor": 3, "direction": "DOWN"}]
    assert status["car_calls"] == [5]
    assert status["planned_stops"] == [5, 3]


def test_enable_and_disable_camera_changes_gating():
    lift = simulation()

    status = lift.enable_camera(3, "url", "http://phone:8080/video")
    assert [c["floor"] for c in status["cameras"]] == [3]
    assert lift.request_hall(3, "up")["hall_calls"] == []

    status = lift.disable_camera(3)
    assert status["cameras"] == []
    assert lift.request_hall(3, "up")["hall_calls"] == [{"floor": 3, "direction": "UP"}]
    with pytest.raises(ValueError):
        lift.enable_camera(9, "local", "0")


def test_invalid_hall_direction_is_rejected():
    lift = simulation(2, waiting=True)
    with pytest.raises(ValueError):
        lift.request_hall(2, "sideways")
    with pytest.raises(ValueError):
        lift.request_hall(5, "up")


class FakeDetector:
    def track(self, frame):
        return []


def test_browser_frames_enable_a_camera_and_reject_other_kinds():
    manager = CameraManager(replace(CONFIG, num_floors=5), lambda config: FakeDetector())
    try:
        ok, jpeg = cv2.imencode(".jpg", np.zeros((480, 640, 3), np.uint8))
        manager.push_frame(2, jpeg.tobytes())
        camera = manager.get(2)
        assert camera.kind == "browser"
        assert camera.online
        with pytest.raises(ValueError):
            manager.push_frame(2, b"not an image")

        manager.enable(3, "url", "http://127.0.0.1:9/none")
        with pytest.raises(ValueError):
            manager.push_frame(3, jpeg.tobytes())
        with pytest.raises(ValueError):
            manager.enable(4, "bogus", "x")
    finally:
        manager.stop_all()


def test_roi_scales_to_each_source_resolution():
    camera = FloorCamera(1, "browser", "", replace(CONFIG, roi=(320, 180, 960, 720)))
    assert camera.scaled_roi(1280, 720) == (320, 180, 960, 720)
    assert camera.scaled_roi(640, 480) == (160, 120, 480, 480)
