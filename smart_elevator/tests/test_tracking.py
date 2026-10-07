from smart_elevator.roi import Detection, ROI
from smart_elevator.tracking import StandingAnalyzer


WAITING_ROI = ROI.from_tuple((0, 0, 500, 500))


def person(center_x, track_id=1, bottom=400, confidence=0.9):
    return Detection(
        box=(center_x - 10, 100, center_x + 10, bottom),
        confidence=confidence,
        track_id=track_id,
    )


def test_does_not_confirm_on_first_frame():
    analyzer = StandingAnalyzer(WAITING_ROI, movement_threshold=20, standing_time=2)

    statuses, standing = analyzer.update([person(250)], timestamp=0.0)

    assert not standing
    assert not statuses[0].is_standing
    assert statuses[0].standing_time == 0.0


def test_small_natural_movement_confirms_after_dwell_time():
    analyzer = StandingAnalyzer(WAITING_ROI, movement_threshold=20, standing_time=2)

    analyzer.update([person(250)], timestamp=0.0)
    analyzer.update([person(253)], timestamp=0.7)
    analyzer.update([person(251)], timestamp=1.4)
    statuses, standing = analyzer.update([person(254)], timestamp=2.0)

    assert standing
    assert statuses[0].is_standing
    assert statuses[0].standing_time == 2.0


def test_walking_person_never_confirms():
    analyzer = StandingAnalyzer(WAITING_ROI, movement_threshold=20, standing_time=2)

    analyzer.update([person(100)], timestamp=0.0)
    analyzer.update([person(125)], timestamp=0.5)
    statuses, standing = analyzer.update([person(155)], timestamp=2.5)

    assert not standing
    assert not statuses[0].is_standing
    assert statuses[0].movement > 20


def test_person_entering_roi_must_dwell_before_confirming():
    analyzer = StandingAnalyzer(WAITING_ROI, movement_threshold=20, standing_time=2)

    analyzer.update([person(600)], timestamp=0.0)
    statuses, standing = analyzer.update([person(450)], timestamp=0.5)
    assert not standing
    assert statuses[0].inside_roi
    assert statuses[0].standing_time == 0.0

    statuses, standing = analyzer.update([person(450)], timestamp=2.4)
    assert not standing
    assert statuses[0].inside_roi
    assert statuses[0].standing_time == 0.0

    statuses, standing = analyzer.update([person(450)], timestamp=4.4)
    assert standing
    assert statuses[0].is_standing


def test_leaving_roi_clears_standing_state():
    analyzer = StandingAnalyzer(WAITING_ROI, movement_threshold=20, standing_time=2)

    analyzer.update([person(250)], timestamp=0.0)
    analyzer.update([person(250)], timestamp=2.0)
    statuses, standing = analyzer.update([person(600)], timestamp=2.1)

    assert not standing
    assert not statuses[0].inside_roi
    assert not statuses[0].is_standing


def test_one_standing_person_keeps_aggregate_true_among_walkers():
    analyzer = StandingAnalyzer(WAITING_ROI, movement_threshold=20, standing_time=2)

    analyzer.update([person(250, track_id=1), person(100, track_id=2)], timestamp=0.0)
    analyzer.update([person(253, track_id=1), person(130, track_id=2)], timestamp=1.0)
    statuses, standing = analyzer.update(
        [person(251, track_id=1), person(160, track_id=2)], timestamp=2.0
    )

    assert standing
    assert statuses[0].is_standing
    assert not statuses[1].is_standing


def test_all_walking_people_keep_aggregate_false():
    analyzer = StandingAnalyzer(WAITING_ROI, movement_threshold=20, standing_time=2)

    analyzer.update([person(100, track_id=1), person(300, track_id=2)], timestamp=0.0)
    statuses, standing = analyzer.update(
        [person(130, track_id=1), person(330, track_id=2)], timestamp=2.0
    )

    assert not standing
    assert all(not status.is_standing for status in statuses)
