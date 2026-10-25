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
├── cameras.py               # Per-floor cameras: webcam, stream URL, browser push
├── web_server.py            # Browser lift simulation (cameras + LOOK controller)
├── web/                     # Dashboard (index.html, app.js) and phone page (camera.html)
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
inference_image_size: int = 640          # 960/1280 improve far-person recall, cost FPS
min_dwell_time: float = 2.0              # required continuous ROI dwell
max_waiting_speed: float = 0.35          # body-heights/s; faster = walking
walk_confirm_time: float = 0.4           # motion must last this long to count as walking
exit_grace: float = 0.7                  # brief step outside the ROI edge is ignored
max_track_age: float = 1.5               # retain tracks through brief misses
person_lost_timeout: float = 2.0         # empty lobby this long cancels a floor's call
idle_return_delay: float = 8.0           # idle car returns to home_floor (1) after this
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

The detector is restricted to the YOLO person class and uses a 640-pixel inference
size by default so several floor cameras can run on one CPU. If small or distant
people are missed, raise it to `960` or `1280` (`--inference-image-size`) at the
cost of FPS.

## Waiting-person logic

The live loop uses Ultralytics ByteTrack with `persist=True`, so every person
keeps an independent tracking ID. Each track has an explicit state:
`OUTSIDE`, `ENTERED`, `WAITING`, `WALKING THROUGH`, or `VALID WAITING`.

- **Dwell**: a track becomes `VALID WAITING` after `min_dwell_time` seconds
  with its feet (bottom-centre of the box) in the ROI.
- **Walking vs. waiting**: the foot position is smoothed, and its net drift
  over `motion_window` is divided by the person's box height. The result is a
  speed in *body-heights per second*, so the same `max_waiting_speed` works for
  a person close to the camera and one far away. Normal walking is about
  0.6–0.9; someone standing, turning or shifting weight stays well below 0.35.
- **Hysteresis**: motion must exceed the limit for `walk_confirm_time` before a
  track is `WALKING THROUGH` (one jumpy detection does not reset the dwell);
  feet may leave the ROI for `exit_grace` seconds without ending the visit;
  a missed detection is tolerated for `max_track_age` seconds.
- `max_allowed_movement` is an optional extra pixel cap on drift (off by default).

The controller output is `WAITING_PERSON`: true when at least one track is
`VALID WAITING`. If several are valid, the person closest to the camera
(largest box) is reported:

```python
{"waiting_person": True, "person_id": 21, "dwell_time": 4.2, "people_in_zone": 1}
```

`people_in_zone` counts everyone currently in the waiting area (waiting or not);
the lift uses it to notice that the lobby has emptied.

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

```bash
# Laptop webcam on floor 1, an IP-camera app on floor 3, HTTPS for phones:
python -m smart_elevator.web_server --https \
    --floor-camera 1=0 \
    --floor-camera 3=http://192.168.1.20:8080/video
```

The server listens on all interfaces and prints the LAN address. Open it on
the laptop (accept the self-signed certificate warning once). The dashboard has:

1. **CCTV + floor cameras**: the annotated YOLO feed of the selected floor and
   one row per floor to switch its camera **Off / Webcam index / Stream URL /
   Phone-browser**, with live status (OFFLINE, ONLINE, PERSON, WAITING).
2. **Hoistway cross-section** with hall buttons, lanterns, and the car.
3. **Car operating panel**, the planned LOOK service order, and an event log.

### Connecting another device's camera

- **Any phone/laptop browser** (no app): start the server with `--https`, then
  on the device open `https://<laptop-ip>:8000/camera`, accept the certificate
  warning, pick the floor, and press *Start streaming*. The floor's camera is
  enabled automatically. Browsers only allow camera access over HTTPS, which is
  why `--https` generates a self-signed certificate in `certs/` (via `openssl`).
  If the laptop's IP changes, delete `certs/` so it is regenerated.
- **IP-camera apps** (IP Webcam, DroidCam, RTSP CCTV): choose *Stream URL* for
  the floor and paste the app's video URL, e.g. `http://192.168.1.20:8080/video`
  or `rtsp://…`. Streams reconnect automatically.
- **USB webcams** on the server: choose *Webcam index* (`0`, `1`, …).

Every camera has its own YOLO tracker. The ROI in `config.py` is defined for a
1280×720 image and is scaled to each source's resolution, so mount every
camera with a similar view of the waiting area. On CPU, several cameras at
`inference_image_size=640` are practical; raise it only if far people are missed.

### Lift behaviour

- **Floors with a camera**: the hall button is accepted only while that floor
  reports `WAITING_PERSON = TRUE` (and rejected while the camera is offline).
  The accepted call is served in **either direction**: the car stops on its way
  up or down. If the waiting area stays empty for `person_lost_timeout`
  seconds before the car arrives, the call is cancelled and the car continues
  to its next call.
- **Floors without a camera** and **cabin buttons** work as normal LOOK calls.
- **Idle return**: when there are no calls for `idle_return_delay` seconds and
  the car is away from floor 1, it returns to floor 1 (doors stay closed) and
  stops for any button pressed on the way; a call behind it takes over.

`lift_controller.LookController` runs the LOOK algorithm: the car keeps its
direction while calls lie ahead, stops for car calls, floor calls, and hall
calls in its direction, and turns around at the last call. Example: car at
floor 1, car call 4, hall UP at 2, hall DOWN at 3 → stops 2, 4, 3.

### HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/status` | Car, doors, calls, plan, events, and all camera statuses |
| GET | `/api/cameras` | Per-floor camera statuses |
| POST | `/api/cameras/{floor}` | Enable a camera: `{"kind": "local"\|"url"\|"browser", "source": "0"}` |
| DELETE | `/api/cameras/{floor}` | Disable a floor's camera |
| POST | `/api/cameras/{floor}/frame` | Push one JPEG frame (body) from a device |
| GET | `/video_feed/{floor}` | Annotated MJPEG stream for a floor |
| GET | `/camera` | Page that turns a phone/laptop into a floor camera |
| POST | `/api/hall/{floor}/{up\|down}` | Landing call |
| POST | `/api/car/{floor}` | Cabin call |

## Tests

The tests use synthetic bounding boxes and track IDs, so they do not require a webcam, YOLO weights, or a GUI:

```powershell
python -m pytest -q
```

The cases cover dwell with box jitter, walking and slow strolls, near vs. far
people, single jumpy detections, ROI-edge grace, multiple tracks, detector
gaps, floor calls in both directions, idle return to floor 1, and camera
gating/cancellation in the web simulation.

For live testing, use a repeatable scene and verify:

1. Empty ROI prints `WAITING_PERSON = FALSE`.
2. One person inside becomes valid only after 2 seconds.
3. Multiple people are evaluated independently; one valid track makes the result `TRUE`.
4. A person outside the ROI remains `FALSE`.
5. A walking person crossing the ROI remains `FALSE`.
6. Leaving the ROI changes the state back to `FALSE`.
7. Repeat under low light and record missed detections or lower confidence.
8. Partially occlude a person and verify behavior at the configured threshold.
9. Check common background objects; only class `person` is accepted.
10. Put one standing person inside and one outside; the result must be `TRUE`.

For reliability before hardware integration, record representative daytime/nighttime clips, tune the threshold and ROI, and measure false positives, false negatives, and FPS on the target computer.
