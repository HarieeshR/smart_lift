from smart_elevator.roi import Detection, ROI
from smart_elevator.tracking import WaitingPersonAnalyzer, WaitingState


WAITING_ROI = ROI.from_tuple((0, 0, 500, 500))


def person(foot_x, track_id=1, bottom=400, confidence=0.9):
    return Detection(
        box=(foot_x - 10, 100, foot_x + 10, bottom),
        confidence=confidence,
        track_id=track_id,
    )


def test_entered_person_is_not_valid_before_dwell():
    analyzer = WaitingPersonAnalyzer(WAITING_ROI, min_dwell_time=3)

    statuses, result = analyzer.update([person(250)], timestamp=0.0)

    assert not result.waiting_person
    assert statuses[0].state is WaitingState.ENTERED
    assert statuses[0].dwell_time == 0.0


def test_stationary_person_becomes_valid_after_dwell():
    analyzer = WaitingPersonAnalyzer(WAITING_ROI, min_dwell_time=3)

    analyzer.update([person(250)], timestamp=0.0)
    analyzer.update([person(253)], timestamp=1.0)
    analyzer.update([person(251)], timestamp=2.0)
    statuses, result = analyzer.update([person(254)], timestamp=3.0)

    assert result.as_dict() == {
        "waiting_person": True,
        "person_id": 1,
        "dwell_time": 3.0,
    }
    assert statuses[0].state is WaitingState.VALID


def test_walking_through_resets_dwell():
    analyzer = WaitingPersonAnalyzer(
        WAITING_ROI, min_dwell_time=3, max_allowed_movement=25, max_track_age=2
    )

    analyzer.update([person(100)], timestamp=0.0)
    statuses, result = analyzer.update([person(140)], timestamp=0.5)
    assert not result.waiting_person
    assert statuses[0].state is WaitingState.WALKING_THROUGH
    assert statuses[0].dwell_time == 0.0

    statuses, result =     analyzer.update([person(140)], timestamp=1.5)
    assert not result.waiting_person
    assert statuses[0].state is WaitingState.WAITING
    assert statuses[0].dwell_time == 1.0


def test_leaving_before_dwell_cancels_visit():
    analyzer = WaitingPersonAnalyzer(WAITING_ROI, min_dwell_time=3)

    analyzer.update([person(250)], timestamp=0.0)
    statuses, result = analyzer.update([person(600)], timestamp=1.0)

    assert not result.waiting_person
    assert statuses[0].state is WaitingState.OUTSIDE
    assert statuses[0].dwell_time == 0.0


def test_valid_person_stays_valid_until_leaving_roi():
    analyzer = WaitingPersonAnalyzer(WAITING_ROI, min_dwell_time=1)

    analyzer.update([person(250)], timestamp=0.0)
    analyzer.update([person(250)], timestamp=1.0)
    statuses, result = analyzer.update([person(600)], timestamp=1.1)

    assert not result.waiting_person
    assert statuses[0].state is WaitingState.OUTSIDE


def test_multiple_people_are_tracked_independently():
    analyzer = WaitingPersonAnalyzer(
        WAITING_ROI, min_dwell_time=3, max_track_age=3
    )

    analyzer.update(
        [person(100, track_id=1), person(250, track_id=2)], timestamp=0.0
    )
    analyzer.update(
        [person(140, track_id=1), person(250, track_id=2)], timestamp=1.0
    )
    statuses, result = analyzer.update(
        [person(180, track_id=1), person(250, track_id=2)], timestamp=3.0
    )

    assert result.waiting_person
    assert result.person_id == 2
    assert statuses[0].state is WaitingState.WALKING_THROUGH
    assert statuses[1].state is WaitingState.VALID


def test_short_detection_gap_keeps_valid_result():
    analyzer = WaitingPersonAnalyzer(
        WAITING_ROI, min_dwell_time=1, max_track_age=1
    )

    analyzer.update([person(250)], timestamp=0.0)
    analyzer.update([person(250)], timestamp=1.0)
    _, result = analyzer.update([], timestamp=1.2)
    assert result.waiting_person
    assert result.person_id == 1

    _, result = analyzer.update([], timestamp=2.1)
    assert not result.waiting_person


def test_foot_position_not_box_center_controls_roi():
    analyzer = WaitingPersonAnalyzer(WAITING_ROI, min_dwell_time=1)
    detection = Detection(
        box=(250, -300, 350, 400), confidence=0.9, track_id=1
    )

    statuses, result = analyzer.update([detection], timestamp=0.0)

    assert statuses[0].inside_roi
    assert not result.waiting_person
