import os
from dataclasses import dataclass

@dataclass
class BotConfig:
    # Defaults can be overridden via environment variables
    meet_link: str = os.getenv("MEET_LINK", "https://meet.google.com/wam-mbqm-axy")
    display_name: str = os.getenv("DISPLAY_NAME", "Meeting Assistant")
    backend_url: str = os.getenv("BACKEND_URL", "http://localhost:5000/captions")
    headless: bool = os.getenv("HEADLESS", "false").lower() in ("1", "true", "yes")
    # If RUN_MINUTES <= 0, run indefinitely until stopped
    run_minutes: int = int(os.getenv("RUN_MINUTES", "0"))
    dedupe_window_sec: float = float(os.getenv("DEDUPE_WINDOW_SEC", "3.0"))
    # Persistent profile to keep Google login (set to a folder path to enable)
    user_data_dir: str = os.getenv("USER_DATA_DIR", "")
