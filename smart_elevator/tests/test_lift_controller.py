import pytest

from smart_elevator.lift_controller import Direction, DoorState, LookController

UP, DOWN = Direction.UP, Direction.DOWN


def controller(**overrides) -> LookController:
    settings = {"num_floors": 5, "speed": 1.0, "door_open_time": 2.0, "door_transition_time": 0.5}
    settings.update(overrides)
    return LookController(**settings)


def run_until_idle(lift: LookController, dt: float = 0.1, max_time: float = 300.0) -> list[int]:
    elapsed = 0.0
    while elapsed < max_time:
        if not lift.has_calls() and not lift.moving and lift.door_state is DoorState.CLOSED:
            break
        lift.tick(dt)
        elapsed += dt
    return list(lift.stops)


def test_look_serves_calls_in_travel_direction_before_reversing():
    lift = controller()
    lift.add_car_call(4)
    lift.add_hall_call(2, UP)
    lift.add_hall_call(3, DOWN)

    assert lift.planned_stops() == [2, 4, 3]
    assert run_until_idle(lift) == [2, 4, 3]
    assert lift.direction is Direction.IDLE


def test_opposite_hall_call_is_passed_then_served_on_the_way_down():
    lift = controller()
    lift.add_car_call(5)
    lift.add_hall_call(3, DOWN)

    assert run_until_idle(lift) == [5, 3]


def test_turnaround_floor_is_served_without_running_past_it():
    lift = controller()
    lift.add_hall_call(4, DOWN)

    lift.tick(0.1)
    assert lift.direction is Direction.UP

    stops = run_until_idle(lift)

    assert stops == [4]
    assert lift.position == 4.0
    assert lift.hall_calls == set()


def test_down_sweep_stops_for_down_calls_and_car_calls():
    lift = controller(start_floor=5)
    lift.add_hall_call(4, UP)
    lift.add_hall_call(3, DOWN)
    lift.add_car_call(1)

    assert run_until_idle(lift) == [3, 1, 4]


def test_doors_cycle_and_car_does_not_move_while_open():
    lift = controller()
    lift.add_car_call(2)
    lift.tick(0.1)
    for _ in range(10):
        lift.tick(0.1)
    assert lift.position == 2.0
    assert lift.door_state is DoorState.OPENING

    lift.add_car_call(4)
    lift.tick(0.5)
    assert lift.door_state is DoorState.OPEN
    assert lift.door_progress() == 1.0
    lift.tick(1.0)
    assert lift.position == 2.0

    lift.tick(1.0)
    assert lift.door_state is DoorState.CLOSING
    lift.tick(0.5)
    assert lift.door_state is DoorState.CLOSED
    lift.tick(0.5)
    assert lift.moving
    assert lift.position > 2.0


def test_idle_without_calls_and_call_at_current_floor_opens_doors():
    lift = controller()
    lift.tick(1.0)
    assert lift.direction is Direction.IDLE
    assert not lift.moving

    lift.add_hall_call(1, UP)
    lift.tick(0.1)

    assert lift.position == 1.0
    assert lift.door_state is DoorState.OPENING
    assert lift.hall_calls == set()


def test_cancelled_calls_park_the_car_at_the_next_floor():
    lift = controller()
    lift.add_hall_call(5, DOWN)
    lift.tick(0.1)
    lift.tick(0.5)
    assert 1.0 < lift.position < 2.0

    assert lift.cancel_hall_call(5, DOWN, "test")
    lift.tick(1.0)

    assert lift.position == 2.0
    assert not lift.moving
    assert lift.stops == type(lift.stops)()


def test_invalid_calls_are_rejected():
    lift = controller()
    with pytest.raises(ValueError):
        lift.add_car_call(6)
    with pytest.raises(ValueError):
        lift.add_hall_call(5, UP)
    with pytest.raises(ValueError):
        lift.add_hall_call(1, DOWN)


def test_snapshot_reports_calls_and_plan():
    lift = controller()
    lift.add_hall_call(3, UP)
    lift.add_car_call(2)

    snapshot = lift.snapshot()

    assert snapshot["hall_calls"] == [{"floor": 3, "direction": "UP"}]
    assert snapshot["car_calls"] == [2]
    assert snapshot["planned_stops"] == [2, 3]
    assert snapshot["door_state"] == "CLOSED"
    assert snapshot["events"][0]["message"] == "Car call registered: floor 2"
