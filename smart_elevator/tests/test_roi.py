from smart_elevator.roi import Detection, ROI, person_present


ROI_AREA = ROI.from_tuple((100, 100, 500, 500))
THRESHOLD = 0.60


def detection(box, confidence=0.90):
    return Detection(box=box, confidence=confidence)


def test_no_person_in_front_of_elevator():
    assert not person_present([], ROI_AREA, THRESHOLD)


def test_one_person_waiting():
    assert person_present([detection((200, 150, 300, 480))], ROI_AREA, THRESHOLD)


def test_multiple_people_waiting():
    detections = [detection((120, 120, 200, 490)), detection((300, 180, 400, 470))]
    assert person_present(detections, ROI_AREA, THRESHOLD)


def test_person_outside_roi():
    assert not person_present([detection((600, 150, 700, 480))], ROI_AREA, THRESHOLD)


def test_person_entering_roi():
    outside = detection((520, 150, 620, 480))
    inside = detection((450, 150, 550, 480))
    assert not person_present([outside], ROI_AREA, THRESHOLD)
    assert person_present([inside], ROI_AREA, THRESHOLD)


def test_person_leaving_roi():
    inside = detection((200, 150, 300, 480))
    outside = detection((520, 150, 620, 480))
    assert person_present([inside], ROI_AREA, THRESHOLD)
    assert not person_present([outside], ROI_AREA, THRESHOLD)


def test_low_confidence_detection_is_ignored():
    assert not person_present([detection((200, 150, 300, 480), 0.59)], ROI_AREA, THRESHOLD)


def test_partial_or_occluded_person_uses_bottom_center():
    assert person_present([detection((220, 220, 280, 500))], ROI_AREA, THRESHOLD)


def test_false_detection_outside_roi_is_ignored():
    assert not person_present([detection((700, 300, 760, 450), 0.99)], ROI_AREA, THRESHOLD)


def test_non_person_class_is_ignored():
    non_person = Detection(box=(200, 150, 300, 480), confidence=0.99, class_id=2)
    assert not person_present([non_person], ROI_AREA, THRESHOLD)


def test_multiple_people_with_only_one_inside_roi():
    detections = [
        detection((650, 150, 750, 480)),
        detection((250, 150, 350, 480)),
    ]
    assert person_present(detections, ROI_AREA, THRESHOLD)


def test_invalid_roi_is_rejected():
    try:
        ROI.from_tuple((300, 100, 200, 500))
    except ValueError:
        pass
    else:
        raise AssertionError("An inverted ROI must raise ValueError")
