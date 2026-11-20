# Cine Capture Pro

Free, open-source software for digitising 8mm, Super 8 and 16mm cine film
with a modified projector, a hall-effect frame trigger and a USB camera.

**Status:** phase 1 (capture) is ready for testing on real hardware. Edit and
export come in phase 2.

## What it does

1. **Capture**
   - One frame per projector trigger, saved exactly as the camera delivers it.
   - Camera settings are set by hand and locked for the whole reel.
   - An adjustable trigger delay, with an automatic calibration test.
   - Dropped and duplicate frames are detected and logged, never hidden.
2. **Edit** (phase 2)
   - Trim and join reels, all without changing the captured frames.
   - Stabilise on the perforation and/or the film edge, horizontally and vertically.
   - Crop, colour-correct and restore.
3. **Export** (phase 2–3): ProRes, H.264/H.265, DNx, FFV1, DPX and image
   sequences, with optical sound.

## Trigger inputs

- **Gamepad button**, e.g. an Ultimarc u-HID Nano in gamepad mode
  (recommended). It works in the background and sends no key presses to
  other programs.
- **Keyboard emulator.** It is read in the background from the chosen device
  only, or only while the app is active.
- **Arduino / serial board.** The sketch is included.
- **Simulated projector**, for trying the app without hardware.

## Getting it

- **Windows installer:** download the latest *CineCapturePro-Setup* artifact
  from the [Build runs](https://github.com/Treborlee2026/cine-capture-pro/actions).
- **User guide:** [docs/USER_GUIDE.md](docs/USER_GUIDE.md)
- **Run from source and project layout:** [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)

## Platforms

Windows 10 (1809+) and 11, 64-bit. A macOS version is planned.

## Licence

GNU General Public License v3.0 or later. See `LICENSE`.
