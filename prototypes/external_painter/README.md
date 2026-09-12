# Open Painter external-window rendering prototype

This is a separate PySide6 application window. The Blender extension supplies scene data, material edits, theme colors, and EEVEE frames through an authenticated loopback connection. The UI does not create Blender windows, replace editors, or draw an overlay over Blender.

## Run

1. Create a Python environment and install `requirements.txt` (development environment already exists at `.venv`).
2. Install `dist/open_painter_bridge-0.0.1.zip` using Blender Preferences → Get Extensions → Install from Disk.
3. In this extension's preferences, set **UI Python executable** to the environment's Python executable, e.g. `/Volumes/SSD/projects/open_painter/prototypes/external_painter/.venv/bin/python` on this development machine.
4. Select a mesh and keep a 3D Viewport available in Blender. Choose **Window → Open Painter — External Window**.

Blender must stay running. Right/middle drag orbits; wheel zooms. T toggles Assets and N toggles the right dock in the external window. Material controls modify the selected object's actual Blender material. Linked shader inputs are rejected rather than silently editing ineffective defaults. Closing the external window or loading a Blender file ends the session.

## Scope and limitations

This is the rendering bridge milestone, not the completed painting application. Layer editing, brush strokes, masks, generators, texture creation/export and the 2D view have not been migrated. Asset thumbnails are reference thumbnails; selecting an asset currently applies only constant PBR values, not its pictured procedural texture.

The UI reads Blender theme colors and follows the reference's broad assets/view/properties arrangement. It does not yet match all reference controls or Blender's native widgets. No main-plugin files or installed user add-on are replaced by this prototype.

EEVEE rendering uses `GPUOffScreen.draw_view3d` on Blender's main thread, then transfers RGBA pixels to Qt. Frame requests are scheduled every 16 ms when the bridge is idle, at up to 800 pixels per dimension. Camera input is accumulated into the next frame request, avoiding a backlog of individual mouse events. Actual frame rate is limited by rendering and transfer. Shader compilation can still stall Blender; GPU readback and transfer add latency. This proves API integration, not Substance Painter performance. A persistent/incremental rendering and painting path needs further work before increasing feature scope.

Tested on macOS Apple Silicon with Blender 5.2.1 and Python 3.14/PySide6 6.11.2. Other platforms and portable runtime distribution are unverified.

## Verification

Run an isolated factory-startup Blender (GPU access required):

```sh
/Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python prototypes/external_painter/test_bridge.py -- -ApplePersistenceIgnoreState YES
```

`bridge_test.json` records results and frame timings; `client_test.png` shows the actual Qt window. The test edits only an unsaved factory cube scene and exits its own Blender process.
