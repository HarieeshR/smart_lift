# CNN-Based Smart Elevator: Person Detection

Step 1 is a standalone computer-vision module. It detects people with YOLO, checks whether a person's feet are in the configured elevator waiting area, and emits a machine-readable presence state. Elevator buttons, motors, and ESP32/Arduino communication are intentionally out of scope.

## Structure

```text
smart_elevator/
├── main.py                  # Webcam loop and display
├── detector.py              # YOLO adapter
├── roi.py                   # Geometry and presence decision
├── config.py                # Camera, model, threshold, and ROI settings
├── setup_model.py           # Downloads the default YOLO weights
├── requirements.txt
├── models/
│   ├── yolo11n.pt           # Add the weights here
│   └── README.md
└── tests/
    └── test_roi.py
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
confidence_threshold: float = 0.60
roi: tuple[int, int, int, int] = (160, 180, 1120, 700)  # left, top, right, bottom
```

These values live in the `AppConfig` defaults. For a CCTV stream, use a string URL. The requested camera resolution is 1280x720; the actual stream resolution may differ, so calibrate the ROI against the displayed image.

The ROI uses the bottom-center of each person bounding box. This is more useful for a standing person than the box center because it represents where the person's feet meet the floor. A detection counts only when its confidence is at least `0.60` and that anchor lies inside the ROI.

## Run

```powershell
python -m smart_elevator.main
```

The same settings can be overridden for a quick camera test:

```powershell
python -m smart_elevator.main --camera 0 --confidence 0.60 --roi 160,180,1120,700 --device cpu
```

Press `q` or `Esc` to stop. The window displays:

- bounding boxes and confidence scores for person detections
- the waiting-area ROI
- detected-person count
- confidence threshold
- FPS
- `PERSON DETECTED` or `NO PERSON`

The console emits the state when it changes:

```text
PERSON_PRESENT = FALSE
PERSON_PRESENT = TRUE
```

This line is the Step 2 integration contract. A later controller can consume the Boolean state or a serial/MQTT adapter can forward it; no elevator movement is implemented here.

## Tests

The tests use synthetic bounding boxes, so they do not require a webcam, YOLO weights, or a GUI:

```powershell
python -m pytest -q
```

The cases cover no person, one or multiple waiting people, outside/entering/leaving the ROI, low confidence, partial/occluded detections, false detections, and multiple people with only one inside.

For live testing, use a repeatable scene and verify:

1. Empty ROI prints `PERSON_PRESENT = FALSE`.
2. One person standing inside prints `TRUE` and shows a green ROI.
3. Multiple people inside remains `TRUE` and the count increases.
4. A person outside the ROI remains `FALSE`.
5. Walking across the ROI changes the state at the boundary.
6. Leaving the ROI changes it back to `FALSE`.
7. Repeat under low light and record missed detections or lower confidence.
8. Partially occlude a person and verify behavior at the configured threshold.
9. Check common background objects; only class `person` is accepted.
10. Put one person inside and one outside; the result must be `TRUE`.

For reliability before hardware integration, record representative daytime/nighttime clips, tune the threshold and ROI, and measure false positives, false negatives, and FPS on the target computer.
