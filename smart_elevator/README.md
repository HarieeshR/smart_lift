# CNN-Based Smart Elevator: Person Detection

Step 1 is a standalone computer-vision module. It detects people with YOLO, checks whether a person's feet are in the configured elevator waiting area, and emits a machine-readable presence state. Elevator buttons, motors, and ESP32/Arduino communication are intentionally out of scope.

## Structure

```text
smart_elevator/
├── main.py                  # Webcam loop and display
├── detector.py              # YOLO adapter
├── roi.py                   # Geometry and presence decision
├── tracking.py              # ByteTrack IDs and waiting-person state machine
├── config.py                # Camera, model, threshold, and ROI settings
├── setup_model.py           # Downloads the default YOLO weights
├── requirements.txt
├── models/
│   ├── yolo11n.pt           # Add the weights here
│   └── README.md
└── tests/
    ├── test_roi.py
    └── test_tracking.py
```

## Installation

From the directory containing `smart_elevator`:

```powershell
cd smart_elevator
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

On Linux/macOS, activate with `source .venv/bin/activate` instead.

## YOLO model setup

Download the default weights with:

```powershell
python setup_model.py
```

This uses Ultralytics' model-name download mechanism and places `yolo11n.pt` at `models/yolo11n.pt`. A larger model can improve accuracy at the cost of speed; update `model_path` in `config.py` or pass `--model` when using another filename.

## Camera and ROI configuration

Edit the defaults in `AppConfig` in `config.py`:

```python
camera_source: str = "0"                  # Webcam index, CCTV URL, or video path
confidence_threshold: float = 0.50
inference_image_size: int = 1280         # larger input improves small-person recall
min_dwell_time: float = 3.0              # required continuous ROI dwell
max_allowed_movement: float = 25.0       # foot movement window limit in pixels
max_track_age: float = 1.5               # retain tracks through brief misses
roi: tuple[int, int, int, int] = (280, 220, 1000, 700)  # left, top, right, bottom
```

These values live in the `AppConfig` defaults. For a CCTV stream, use a string URL. The requested camera resolution is 1280x720; the actual stream resolution may differ, so calibrate the ROI against the displayed image.

The default ROI is a wider waiting zone directly in front of the elevator, while
still excluding the outer edges of the camera frame. Calibrate it against the
displayed image. Membership is
based only on the bottom-center (foot position) of the bounding box. A detection
counts only when its confidence is at least `0.60` and that foot position lies
inside the ROI.

The detector is restricted to the YOLO person class and uses a 1280-pixel inference
size by default. This improves recall for smaller or more distant people compared
with the common 640-pixel default, at the cost of lower FPS. If the camera cannot
maintain a usable frame rate, reduce it to `960` or `640`.

## Waiting-person logic

The live loop uses Ultralytics ByteTrack with `persist=True`, so every person
keeps an independent tracking ID. Each track has an explicit state:
`OUTSIDE`, `ENTERED`, `WAITING`, `WALKING THROUGH`, or `VALID WAITING`.

When a tracked foot first enters the ROI, its `entry_time` is recorded. The
track becomes `VALID WAITING` only after continuous in-ROI dwell reaches
`min_dwell_time` (3 seconds by default). Leaving the ROI resets the timer.
Movement is measured over a short window using foot positions; movement above
`max_allowed_movement` marks the track `WALKING THROUGH` and restarts its dwell
timer. This rejects people walking across the zone while tolerating small
natural movements.

The only controller output is `WAITING_PERSON`. It is true when at least one
independent track is `VALID WAITING`, and the analyzer returns:

```python
{"waiting_person": True, "person_id": 21, "dwell_time": 4.2}
```

A short detector miss is tolerated for up to `max_track_age` seconds to prevent
one bad frame from creating a false negative. The live console prints the same
state as `WAITING_PERSON = TRUE/FALSE`.

## Run

```powershell
python -m smart_elevator.main
```

The same settings can be overridden for a quick camera test:

```powershell
python -m smart_elevator.main --camera 0 --confidence 0.50 --roi 280,220,1000,700 --device cpu
```

For a small/distant person, try:

```powershell
python -m smart_elevator.main --inference-image-size 1280 --confidence 0.50
```

Use the lowest image size that reliably detects the target person while keeping
the live FPS stable; an overloaded camera pipeline can make tracking less
reliable.

Press `q` or `Esc` to stop. The window displays:

- bounding boxes, ByteTrack IDs, and confidence scores for person detections
- the waiting-area ROI
- `WALKING THROUGH`, `WAITING`, or `VALID WAITING` and dwell time for each track
- detected-person count and movement threshold
- confidence threshold
- FPS
- `WAITING PERSON DETECTED` or `NO WAITING PERSON`

The console emits the state when it changes:

```text
WAITING_PERSON = FALSE
WAITING_PERSON = TRUE {'waiting_person': True, 'person_id': 21, 'dwell_time': 3.1}
```

This line is the Step 2 integration contract. A later controller can consume the Boolean state or a serial/MQTT adapter can forward it; no elevator movement is implemented here.

## Tests

The tests use synthetic bounding boxes and track IDs, so they do not require a webcam, YOLO weights, or a GUI:

```powershell
python -m pytest -q
```

The cases cover continuous dwell, walking-through reset, leaving before dwell,
multiple independent tracks, temporary detector gaps, and foot-position ROI
membership.

For live testing, use a repeatable scene and verify:

1. Empty ROI prints `WAITING_PERSON = FALSE`.
2. One person inside becomes valid only after 3 seconds.
3. Multiple people are evaluated independently; one valid track makes the result `TRUE`.
4. A person outside the ROI remains `FALSE`.
5. A walking person crossing the ROI remains `FALSE`.
6. Leaving the ROI changes the state back to `FALSE`.
7. Repeat under low light and record missed detections or lower confidence.
8. Partially occlude a person and verify behavior at the configured threshold.
9. Check common background objects; only class `person` is accepted.
10. Put one standing person inside and one outside; the result must be `TRUE`.

For reliability before hardware integration, record representative daytime/nighttime clips, tune the threshold and ROI, and measure false positives, false negatives, and FPS on the target computer.
