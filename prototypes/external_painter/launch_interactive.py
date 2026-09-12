"""Launch the external painter for manual testing, without changing saved preferences."""
import sys
from pathlib import Path
import bpy

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import open_painter_bridge as bridge

bridge.register()
entry = bpy.context.preferences.addons.get('open_painter_bridge')
if entry is None:
    entry = bpy.context.preferences.addons.new()
    entry.module = 'open_painter_bridge'
entry.preferences.python_executable = str(ROOT / '.venv/bin/python')
bpy.context.preferences.view.show_splash = False

def launch():
    result = bpy.ops.open_painter_bridge.open()
    print('OPEN_PAINTER_INTERACTIVE', result, flush=True)
    return None

bpy.app.timers.register(launch, first_interval=2)
