Place the YOLO weights here as `yolo11n.pt`.

The Ultralytics package can download this model automatically when loaded, but this project checks for a local file so deployment is explicit and offline-friendly. Download `yolo11n.pt` from the official Ultralytics release/model source, or change `model_path` in `config.py` to another compatible YOLO weights file.
