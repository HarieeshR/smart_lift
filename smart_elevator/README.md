# CNN-Based Smart Elevator: Person Detection

Step 1 is a standalone computer-vision module. It detects people with YOLO, checks whether a person's feet are in the configured elevator waiting area, and emits a machine-readable presence state. Elevator buttons, motors, and ESP32/Arduino communication are intentionally out of scope.

## Structure

```text
smart_elevator/
├── main.py                  # Webcam loop and display
├── detector.py              # YOLO adapter
├── roi.py                   # Geometry and presence decision
├── tracking.py              # ByteTrack IDs and waiting-person state machine
├── lift_controller.py       # LOOK scheduler, hall/car calls, door timing
├── web_server.py            # Browser lift simulation (camera + LOOK controller)
├── web/                     # Simulation UI (index.html, styles.css, app.js)
├── config.py                # Camera, model, threshold, and ROI settings
├── setup_model.py           # Downloads the default YOLO weights
├── requirements.txt
├── models/
│   ├── yolo11n.pt           # Add the weights here
│   └── README.md
└── tests/
    ├── test_roi.py
    ├── test_tracking.py
    ├── test_lift_controller.py
    └── test_web_server.py
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
based on the bottom-center (foot position) of the bounding box. If a person is
very close to the camera and their lower body is clipped by the frame, the
detection also counts when its box crosses the ROI's lower edge and its
horizontal center is inside the ROI.

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
independent track is `VALID WAITING`. If multiple tracks are valid, the
analyzer selects the person closest to the camera, using the detected
bounding-box height as the camera-proximity signal, and returns:

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

## Browser lift simulation

Run the YOLO-backed five-floor browser simulation:

```powershell
python -m smart_elevator.web_server --camera 0 --camera-floor 1 --port 8000
```

Open <http://127.0.0.1:8000>. The page has three panels:

1. **CCTV**: the annotated YOLO feed, `WAITING_PERSON`, track ID, dwell time, and
   a selector for the floor the camera is mounted on.
2. **Hoistway cross-section**: every landing has a floor plate, a hall position
   indicator, UP/DOWN hall lanterns, and UP/DOWN call buttons. The shaft shows the
   cabin with sliding doors, cable, counterweight, and machine room.
3. **Car operating panel**: floor/direction display, illuminated cabin buttons,
   the planned LOOK service order, and an event log.

### LOOK scheduling

`lift_controller.LookController` runs one car with the LOOK algorithm:

- **Hall calls** are UP/DOWN buttons on a landing; **car calls** are cabin buttons.
- The car keeps its direction while any call lies ahead of it. On the way it
  stops for car calls and for hall calls going the same way.
- When nothing is left ahead, it turns around at the last call instead of
  running to the end of the shaft. A hall call in the opposite direction at that
  floor is served there.
- At each stop the doors open (`door_transition_time`), stay open
  (`door_open_time`), and close before the car moves again. Pressing a call
  for the current floor reopens the doors.
- Example: car at floor 1, car call 4, hall UP at 2, hall DOWN at 3 → stops 2, 4, 3.

Speed, door times, and floor count live in `AppConfig`
(`lift_speed_floors_per_second`, `door_open_time`, `door_transition_time`,
`num_floors`).

### Camera gating

Only **hall calls on the camera floor** depend on YOLO. Such a call is rejected
unless `WAITING_PERSON = TRUE`. If the person leaves before the car arrives,
the call is cancelled. Hall calls on other floors and all car calls are never
gated.

### HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/status` | Car position, direction, doors, calls, planned stops, events, camera status |
| GET | `/video_feed` | Annotated MJPEG stream |
| POST | `/api/hall/{floor}/{up\|down}` | Landing call |
| POST | `/api/car/{floor}` | Cabin call |
| POST | `/api/camera-floor/{floor}` | Move the camera to another floor |

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
