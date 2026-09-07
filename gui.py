"""图形界面入口：python gui.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.app import run_gui

if __name__ == "__main__":
    cfg = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "config.yaml")
    run_gui(cfg)
