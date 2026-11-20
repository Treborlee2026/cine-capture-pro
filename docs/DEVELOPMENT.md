# Development notes

## Run from source (Windows)

```
git clone https://github.com/Treborlee2026/cine-capture-pro
cd cine-capture-pro
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt pytest
python -m cinecapture            # real hardware
python -m cinecapture --demo     # simulated projector and camera
python -m pytest -q              # tests (no hardware or Qt needed for most)
```

## Layout

| Folder | Contents | Depends on Qt? |
| --- | --- | --- |
| `cinecapture/core/` | Trigger-to-frame matching, frame writer, session, calibration, projects, reports, analysis | No |
| `cinecapture/camera/` | Camera adapters: OpenCV (DirectShow / Media Foundation), simulated rig | No |
| `cinecapture/trigger/` | Trigger inputs: HID gamepad, keyboard (Raw Input), serial, simulated | No |
| `cinecapture/ui/` | PySide6 interface | Yes |
| `packaging/` | PyInstaller launcher, Inno Setup script | - |
| `docs/` | User guide, Arduino sketch | - |

Rules:

- `core`, `camera` and `trigger` never import Qt, so they can be tested headless
  and reused by the macOS port.
- A macOS port adds `camera/avfoundation_camera.py`. The HID and serial
  triggers already work on macOS.
- To add a new trigger type, subclass `trigger.base.TriggerSource` and register
  it in `trigger/__init__.py`.

## Timing model

`core/matcher.py` keeps, for a trigger at time *T* and delay *D*, the first
camera frame that arrived at or after *T + D*. If a later trigger's window has
also opened by then, the earlier trigger is reported as dropped. Frames are
numbered by trigger, so a drop leaves a gap in the file numbers, and the gap is
recorded in `capture.log` and the session report.

`camera/simulated.py` models a projector (pull-down fraction, trigger latency)
and a rolling-shutter camera (exposure, readout, USB latency). The tests use it
to check that calibration finds the sharp window and that captures with a good
delay never pick a frame taken mid pull-down.

## Builds

`.github/workflows/build.yml` runs on every push:

1. Core tests on Linux.
2. On Windows:
   - unit tests
   - a smoke test that opens the real Qt window offscreen and captures from the simulated rig
   - a PyInstaller build
   - the same smoke test against the built `.exe`
   - an Inno Setup installer.

   The installer and the portable folder are uploaded as artifacts.
3. Pushing a tag `v1.2.3` also publishes a GitHub Release with the installer.

## Licence

GPL-3.0-or-later. Third-party licences: PySide6 (LGPL-3.0), OpenCV
(Apache-2.0), NumPy (BSD), hidapi (BSD), pyserial (BSD), pygrabber (MIT).
