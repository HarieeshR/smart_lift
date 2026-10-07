"""Configuration for the Smart Elevator person-detection module."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


ROOT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class AppConfig:
    """Runtime settings kept separate from camera and detection code."""

    model_path: Path = ROOT_DIR / "models" / "yolo11n.pt"
    camera_source: str = "0"
    confidence_threshold: float = 0.60
    roi: Tuple[int, int, int, int] = (160, 180, 1120, 700)
    window_name: str = "Smart Elevator Person Detection"
    image_width: int = 1280
    image_height: int = 720
    device: Optional[str] = None
    show_fps: bool = True


def parse_camera_source(source: str) -> int | str:
    """Use an integer for webcam indexes and a string for CCTV URLs/files."""
    return int(source) if source.isdigit() else source


CONFIG = AppConfig()
