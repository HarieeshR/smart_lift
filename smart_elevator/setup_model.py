"""Download the default YOLO weights through Ultralytics."""

from pathlib import Path

from ultralytics import YOLO


MODEL_NAME = "yolo11n.pt"


def main() -> None:
    model_directory = Path(__file__).resolve().parent / "models"
    model_directory.mkdir(exist_ok=True)
    model_path = model_directory / MODEL_NAME
    if model_path.exists():
        print(f"Model already exists: {model_path}")
        return

    print(f"Requesting {MODEL_NAME} from Ultralytics...")
    YOLO(MODEL_NAME)
    downloaded_model = Path(MODEL_NAME)
    if downloaded_model.exists():
        downloaded_model.replace(model_path)
    if not model_path.exists():
        raise RuntimeError(
            "Ultralytics did not place the downloaded weights in the expected location. "
            f"Place {MODEL_NAME} in {model_directory} manually."
        )
    print(f"Model ready: {model_path}")


if __name__ == "__main__":
    main()