# How to Run the Smart Elevator

There is **one process** to start. `web_server.py` is the backend *and* it serves
the frontend (`web/index.html`, `web/app.js`, `web/camera.html`). You do not need
npm, a separate frontend server, or anything else.

```
 USB webcam ─┐
 IP camera  ─┼──► web_server.py (YOLO per floor + LOOK lift) ──► http://<ip>:8000      (dashboard)
 Phone      ─┘          ▲                                     └─► http://<ip>:8000/camera (phone page)
                        └── frames pushed from phone browser
```

---

## 1. The #1 reason it "doesn't run"

Always run commands from the **repo root** (`reverse_eng/`), the folder that
*contains* `smart_elevator/`. Never run them from inside `smart_elevator/`.

```bash
# WRONG (inside smart_elevator/)
cd ~/Desktop/reverse_eng/smart_elevator
python -m smart_elevator.main
# -> ModuleNotFoundError: No module named 'smart_elevator'

# RIGHT
cd ~/Desktop/reverse_eng
python -m smart_elevator.main
```

---

## 2. One-time setup

The virtual environment lives at `reverse_eng/.venv` (not inside `smart_elevator/`).

```bash
cd ~/Desktop/reverse_eng
source .venv/bin/activate          # zsh/bash; prompt shows (.venv)

# Only if the venv is missing or broken:
#   python -m venv .venv && source .venv/bin/activate
#   pip install -r smart_elevator/requirements.txt

# Only if smart_elevator/models/yolo11n.pt is missing:
#   python smart_elevator/setup_model.py
```

Check that it works:

```bash
python -c "import ultralytics, cv2, lap; print('ok')"
python -m pytest -q smart_elevator/tests
```

---

## 3. Run backend + frontend (the full lift simulation)

```bash
cd ~/Desktop/reverse_eng
source .venv/bin/activate
python -m smart_elevator.web_server --floor-camera 1=0
```

You should see:

```
Lift simulation: http://127.0.0.1:8000
  On other devices: http://172.20.10.3:8000  (phone camera: /camera)
```

Open **http://127.0.0.1:8000** in your browser. That is the frontend. Stop with `Ctrl+C`.

The first camera can take 5–10 seconds to show **ONLINE** while YOLO loads.

### Server options

| Option | Meaning | Example |
| --- | --- | --- |
| `--floor-camera FLOOR=SOURCE` | Attach a camera to a floor. **Repeat it for more cameras.** | `--floor-camera 2=rtsp://...` |
| `--https` | Serve HTTPS (required for phone cameras) | `--https` |
| `--port` | Port (default `8000`) | `--port 9000` |
| `--host` | `0.0.0.0` (default, LAN visible) or `127.0.0.1` (this PC only) | `--host 127.0.0.1` |
| `--device` | `cpu` or a GPU index such as `0` | `--device cpu` |
| `--inference-image-size` | 640 is fast; 960/1280 catches far-away people but is slower | `--inference-image-size 960` |
| `--model` | Other YOLO weights | `--model smart_elevator/models/yolo11s.pt` |

`SOURCE` can be:

- a **number**: a USB/laptop webcam index (`0`, `1`, `2`…)
- a **URL**: `http://…`, `rtsp://…`, or a video file path
- **`browser`**: a phone/laptop that streams from the `/camera` page

Floors must be between `1` and `num_floors` (5 by default, see `config.py`).

---

## 4. Connecting multiple external cameras

Every camera gets its own YOLO tracker and thread, and is assigned to one floor.
You can attach cameras **at startup** (command line) or **at runtime**
(dashboard → the camera row for each floor → Off / Webcam index / Stream URL / Phone-browser).

### 4a. Extra USB webcams (plugged into this PC)

Find the right index first. On Linux **one camera usually creates 2 or more
`/dev/video*` nodes** (one for video, one for metadata or IR), so a second camera is
often index `2`, not `1`.

```bash
v4l2-ctl --list-devices          # sudo pacman -S v4l-utils  (if missing)
```

Example output with two cameras:

```
ASUS FHD webcam:      /dev/video0 /dev/video1
Logitech C270:        /dev/video2 /dev/video3
```

Use the **first** node of each device:

```bash
python -m smart_elevator.web_server \
    --floor-camera 1=0 \
    --floor-camera 2=2
```

Quick test of a single index (press `q` to close):

```bash
python -m smart_elevator.main --camera 2 --device cpu
```

> Several USB cameras on one USB controller can run out of bandwidth. If the
> second one stays OFFLINE, plug it into a different port (or a different side of
> the laptop).

### 4b. Phone as an IP camera (app, works over plain HTTP)

1. Install **IP Webcam** (Android) or **DroidCam** (Android/iOS) on the phone.
2. Put the phone and this PC on the **same Wi‑Fi / hotspot**.
3. Start the server in the app. It shows an address such as `http://192.168.1.20:8080`.
4. Check the stream URL in the PC browser first:
   - IP Webcam: `http://<phone-ip>:8080/video`
   - DroidCam: `http://<phone-ip>:4747/video`
5. Attach it to a floor:

```bash
python -m smart_elevator.web_server \
    --floor-camera 1=0 \
    --floor-camera 3=http://192.168.1.20:8080/video
```

### 4c. CCTV / RTSP IP cameras

Use the camera's RTSP URL (it is in the camera's manual or web settings):

```bash
python -m smart_elevator.web_server \
    --floor-camera 4="rtsp://admin:password@192.168.1.64:554/Streaming/Channels/101"
```

Common formats:

| Brand | URL |
| --- | --- |
| Hikvision | `rtsp://user:pass@IP:554/Streaming/Channels/101` |
| Dahua / CP Plus | `rtsp://user:pass@IP:554/cam/realmonitor?channel=1&subtype=0` |
| Generic ONVIF | `rtsp://user:pass@IP:554/stream1` |

Test with `ffplay "<url>"` or VLC first. If VLC cannot open it, this app cannot either.
Network streams reconnect automatically every 2 seconds.

### 4d. Any phone/laptop browser (no app needed)

Browsers only allow camera access over **HTTPS**, so start with `--https`:

```bash
python -m smart_elevator.web_server --https --floor-camera 1=0
```

On the other device (same Wi‑Fi):

1. Open `https://<pc-ip>:8000/camera` (the IP printed at startup, e.g. `https://172.20.10.3:8000/camera`).
2. Accept the "not secure / self-signed certificate" warning (*Advanced → Proceed*).
3. Choose a floor and press **Start streaming**. The floor turns ONLINE on the dashboard.

On the PC, open `https://127.0.0.1:8000` and accept the warning once too.

If your PC's IP changed since the certificate was made, delete it so it is regenerated:

```bash
rm -rf smart_elevator/certs
```

### 4e. Everything at once (example)

```bash
cd ~/Desktop/reverse_eng
source .venv/bin/activate
python -m smart_elevator.web_server --https --device cpu \
    --floor-camera 1=0 \
    --floor-camera 2=2 \
    --floor-camera 3=http://192.168.1.20:8080/video \
    --floor-camera 4="rtsp://admin:pass@192.168.1.64:554/stream1" \
    --floor-camera 5=browser
```

Then open `https://<pc-ip>:8000/camera` on the phone for floor 5.

---

## 5. Attaching cameras without restarting (API)

The dashboard buttons call these endpoints. You can call them yourself:

```bash
# Webcam index 2 on floor 2
curl -X POST http://127.0.0.1:8000/api/cameras/2 -d '{"kind":"local","source":"2"}'

# Stream URL on floor 3
curl -X POST http://127.0.0.1:8000/api/cameras/3 -d '{"kind":"url","source":"http://192.168.1.20:8080/video"}'

# Remove the camera from floor 3
curl -X DELETE http://127.0.0.1:8000/api/cameras/3

# Status of all cameras
curl http://127.0.0.1:8000/api/cameras
```

Add `-k` and use `https://` if the server was started with `--https`.

---

## 6. Detection-only mode (no web UI)

This opens an OpenCV window for **one** camera so you can tune the waiting-zone ROI:

```bash
cd ~/Desktop/reverse_eng
python -m smart_elevator.main --camera 0 --confidence 0.50 --roi 280,220,1000,700 --device cpu
```

`--camera` accepts the same sources (`0`, `2`, `http://…`, `rtsp://…`). Press `q` or `Esc` to quit.
The ROI is in 1280×720 coordinates and is scaled automatically for other resolutions.

---

## 7. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `No module named 'smart_elevator'` | You are inside `smart_elevator/`. `cd ..` to `reverse_eng/`. |
| `No module named 'ultralytics'` / `cv2` | Venv not active: `source .venv/bin/activate`. |
| `Address already in use` | Another server is running. Stop it (`Ctrl+C`), or use `--port 8001`. |
| Camera stays **OFFLINE** | Wrong index (try `2`), the camera is used by another app (close Zoom/browser tabs using it), or the URL is wrong (test in VLC). |
| `Permission denied` on `/dev/video*` | `sudo usermod -aG video $USER`, then log out and in. |
| `SSL: HTTP_REQUEST` / `dropped: HTTP_REQUEST` | You opened `http://` on an `--https` server. Use `https://127.0.0.1:8000`. |
| `CERTIFICATE_UNKNOWN` / `BrokenPipeError` with `--https` | Harmless. The browser is rejecting the self-signed cert. Click *Advanced → Proceed*. |
| Phone `/camera` page: camera blocked | You must use `--https` and open the **https://** URL. |
| Phone cannot reach the PC | Same Wi‑Fi? Firewall open? `sudo ufw allow 8000/tcp` (or your firewall's equivalent). |
| Very low FPS with many cameras | Use `--inference-image-size 640`, fewer cameras, or a GPU (`--device 0`). Each camera runs its own YOLO model. |
| Hall button rejected | Expected: a floor with a camera only accepts the call when someone is standing in its waiting zone (status **WAITING**). |
