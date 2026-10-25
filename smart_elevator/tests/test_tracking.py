from smart_elevator.roi import Detection, ROI
from smart_elevator.tracking import WaitingPersonAnalyzer, WaitingState


WAITING_ROI = ROI.from_tuple((0, 0, 500, 500))
FPS = 10
DT = 1.0 / FPS


def person(foot_x, track_id=1, top=100, bottom=400, confidence=0.9, foot_y=None):
    if foot_y is not None:
        top, bottom = foot_y - (bottom - top), foot_y
    return Detection(
        box=(round(foot_x) - 10, top, round(foot_x) + 10, bottom),
        confidence=confidence,
        track_id=track_id,
    )


def analyzer(**overrides):
    settings = {"min_dwell_time": 2.0, "max_track_age": 1.5}
    settings.update(overrides)
    return WaitingPersonAnalyzer(WAITING_ROI, **settings)


def play(tracker, frames, start=0.0):
    """Feed one detection list per frame at FPS; return the last update."""
    result = None
    for index, detections in enumerate(frames):
        result = tracker.update(detections, timestamp=start + index * DT)
    return result


def jitter(index, amplitude=4):
    return amplitude if index % 2 else -amplitude


def test_entered_person_is_not_valid_before_dwell():
    statuses, result = analyzer().update([person(250)], timestamp=0.0)

    assert not result.waiting_person
    assert statuses[0].state is WaitingState.ENTERED
    assert statuses[0].dwell_time == 0.0
    assert result.people_in_zone == 1


def test_standing_person_with_box_jitter_becomes_valid_after_dwell():
    tracker = analyzer()
    statuses, result = play(
        tracker, [[person(250 + jitter(i), foot_y=400 + jitter(i, 3))] for i in range(21)]
    )

    assert result.waiting_person
    assert result.person_id == 1
    assert abs(result.dwell_time - 2.0) < 1e-6
    assert statuses[0].state is WaitingState.VALID


def test_walking_across_the_roi_never_becomes_valid():
    tracker = analyzer()
    # 0.8 body-heights/s across the whole ROI: an ordinary walking pace.
    for i in range(int(500 / 24) + 1):
        statuses, result = tracker.update([person(10 + 24 * i)], timestamp=i * DT)
        assert not result.waiting_person
    assert statuses[0].state is WaitingState.WALKING_THROUGH


def test_slow_stroll_through_roi_is_still_walking():
    tracker = analyzer()
    # 0.45 body-heights/s for 3.5 s: slower than normal, but still travelling.
    for i in range(35):
        statuses, result = tracker.update([person(10 + 13.5 * i)], timestamp=i * DT)
        assert not result.waiting_person
    assert statuses[0].state is WaitingState.WALKING_THROUGH


def test_single_jumpy_box_does_not_reset_dwell():
    tracker = analyzer()
    frames = [[person(250)] for _ in range(21)]
    frames[10] = [person(330)]  # one bad box
    statuses, result = play(tracker, frames)

    assert result.waiting_person
    assert statuses[0].state is WaitingState.VALID


def test_person_stops_after_walking_and_then_dwell_starts():
    tracker = analyzer()
    play(tracker, [[person(40 + 24 * i)] for i in range(8)])
    statuses, _ = tracker.update([person(40 + 24 * 8)], timestamp=0.8)
    assert statuses[0].state is WaitingState.WALKING_THROUGH

    stop_x = 40 + 24 * 8
    statuses, result = play(tracker, [[person(stop_x)] for _ in range(20)], start=0.9)
    assert not result.waiting_person
    assert statuses[0].state is WaitingState.WAITING

    statuses, result = play(tracker, [[person(stop_x)] for _ in range(25)], start=2.9)
    assert result.waiting_person
    assert statuses[0].state is WaitingState.VALID


def test_far_and_near_people_are_judged_by_the_same_relative_speed():
    # Same physical walk: the far person's box is 1/4 the size and moves 1/4 the pixels.
    near, far = analyzer(), analyzer()
    for i in range(15):
        near_status, _ = near.update([person(40 + 20 * i, top=100, bottom=400)], i * DT)
        far_status, _ = far.update([person(40 + 5 * i, top=325, bottom=400)], i * DT)
    assert near_status[0].state is WaitingState.WALKING_THROUGH
    assert far_status[0].state is WaitingState.WALKING_THROUGH

    near, far = analyzer(), analyzer()
    for i in range(25):
        near_status, _ = near.update([person(250 + jitter(i, 6), top=100, bottom=400)], i * DT)
        far_status, _ = far.update([person(250 + jitter(i, 2), top=325, bottom=400)], i * DT)
    assert near_status[0].state is WaitingState.VALID
    assert far_status[0].state is WaitingState.VALID


def test_optional_pixel_cap_marks_drift_as_walking():
    tracker = analyzer(max_allowed_movement=15.0, max_waiting_speed=10.0)
    statuses, _ = play(tracker, [[person(100 + 4 * i)] for i in range(15)])
    assert statuses[0].state is WaitingState.WALKING_THROUGH


def test_leaving_before_dwell_cancels_visit():
    tracker = analyzer()
    play(tracker, [[person(250)] for _ in range(10)])
    statuses, result = play(tracker, [[person(600)] for _ in range(10)], start=1.0)

    assert not result.waiting_person
    assert statuses[0].state is WaitingState.OUTSIDE
    assert statuses[0].dwell_time == 0.0
    assert result.people_in_zone == 0


def test_brief_step_outside_the_roi_edge_keeps_the_visit():
    tracker = analyzer()
    play(tracker, [[person(495)] for _ in range(15)])
    statuses, _ = tracker.update([person(505)], timestamp=1.5)
    assert statuses[0].state is not WaitingState.OUTSIDE

    statuses, result = play(tracker, [[person(495)] for _ in range(6)], start=1.6)
    assert result.waiting_person


def test_valid_person_is_dropped_after_leaving_roi():
    tracker = analyzer(min_dwell_time=1.0)
    _, result = play(tracker, [[person(250)] for _ in range(11)])
    assert result.waiting_person

    statuses, result = play(tracker, [[person(600)] for _ in range(9)], start=1.1)
    assert not result.waiting_person
    assert statuses[0].state is WaitingState.OUTSIDE


def test_multiple_people_are_tracked_independently():
    tracker = analyzer()
    statuses, result = play(
        tracker,
        [[person(20 + 15 * i, track_id=1), person(250, track_id=2)] for i in range(25)],
    )

    assert result.waiting_person
    assert result.person_id == 2
    assert result.people_in_zone == 2
    assert statuses[0].state is WaitingState.WALKING_THROUGH
    assert statuses[1].state is WaitingState.VALID


def test_closer_valid_person_is_selected_over_farther_person():
    tracker = analyzer(min_dwell_time=1.0)
    _, result = play(
        tracker,
        [
            [
                person(100, track_id=1, top=100, bottom=400),
                person(300, track_id=2, top=250, bottom=400),
            ]
            for _ in range(12)
        ],
    )

    assert result.waiting_person
    assert result.person_id == 1


def test_short_detection_gap_keeps_valid_result():
    tracker = analyzer(min_dwell_time=1.0, max_track_age=0.5)
    _, result = play(tracker, [[person(250)] for _ in range(11)])
    assert result.waiting_person

    _, result = tracker.update([], timestamp=1.3)
    assert result.waiting_person
    assert result.person_id == 1

    _, result = tracker.update([], timestamp=1.7)
    assert not result.waiting_person
    assert result.people_in_zone == 0


def test_foot_position_not_box_center_controls_roi():
    detection = Detection(box=(250, -300, 350, 400), confidence=0.9, track_id=1)

    statuses, result = analyzer().update([detection], timestamp=0.0)

    assert statuses[0].inside_roi
    assert not result.waiting_person


def test_bottom_truncated_person_is_inside_when_box_crosses_roi_edge():
    detection = Detection(box=(200, 100, 300, 600), confidence=0.9, track_id=1)

    statuses, result = analyzer().update([detection], timestamp=0.0)

    assert statuses[0].inside_roi
    assert not result.waiting_person
