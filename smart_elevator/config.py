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
    confidence_threshold: float = 0.50
    inference_image_size: int = 1280
    min_dwell_time: float = 3.0
    max_allowed_movement: float = 25.0
    max_track_age: float = 1.5
    motion_window: float = 0.75
    lift_update_interval: float = 0.05
    lift_speed_floors_per_second: float = 0.5
    num_floors: int = 5
    door_open_time: float = 3.0
    door_transition_time: float = 1.0
    roi: Tuple[int, int, int, int] = (280, 220, 1000, 700)
    window_name: str = "Smart Elevator Person Detection"
    image_width: int = 1280
    image_height: int = 720
    device: Optional[str] = None
    show_fps: bool = True


def parse_camera_source(source: str) -> int | str:
    """Use an integer for webcam indexes and a string for CCTV URLs/files."""
    return int(source) if source.isdigit() else source


CONFIG = AppConfig()
