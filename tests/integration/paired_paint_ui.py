"""Run the held-stroke performance regression with both native views active."""
import runpy
import sys
from pathlib import Path
sys.argv.append("--paired")
runpy.run_path(str(Path(__file__).with_name("realtime_ui.py")), run_name="__main__")
