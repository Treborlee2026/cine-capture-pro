# Cine Capture Pro user guide (phase 1: capture)

Cine Capture Pro captures cine film one frame at a time from a modified
projector. A sensor on the projector tells the computer each time a new film
frame is sitting still in the gate, and the app saves exactly one camera frame
for each of those pulses.

This guide covers the Capture screen. Editing and export come in later phases.

---

## 1. Install

1. Download the latest **CineCapturePro-…-Setup.exe**. Until the Microsoft
   Store version is published, get it from the project's
   [GitHub Actions page](https://github.com/Treborlee2026/cine-capture-pro/actions):
   open the newest green "Build" run and download the *CineCapturePro-Setup*
   artifact.
2. Run it. Windows may show **"Windows protected your PC"** because the
   download is not signed yet. Click **More info**, then **Run anyway**.
3. Start **Cine Capture Pro** from the Start menu.

To try the program without any hardware, start **Cine Capture Pro (demo, no
hardware)** from the Start menu. It uses a simulated projector and camera.

Requirements: Windows 10 (version 1809 or later) or Windows 11, 64-bit. An SSD
for the capture drive and a USB 3 port are recommended.

---

## 2. The hardware

| Part | Notes |
| --- | --- |
| Projector | Modified 8mm, Super 8 or 16mm projector with speed control, lens removed. |
| Camera | Any USB camera that Windows recognises, looking straight at the gate through a macro or relay lens. Tested with the ELP-USB4KCAM01H-CFV. |
| Sensor | Hall sensor on the main shaft, positioned so it triggers just after the claw has pulled the frame down and left the perforation. |
| Interface | Ultimarc u-HID Nano (recommended), any keyboard emulator, or an Arduino-type board. |

### Wiring the hall sensor to a u-HID Nano

Connect the sensor's output to one u-HID input and its ground to the u-HID
ground. u-HID inputs are triggered by being connected to ground, which is what
an open-collector hall sensor (for example A3144 or US5881) does. Power the
sensor from 5 V.

---

## 3. Set the u-HID Nano to gamepad mode (recommended)

In gamepad mode the u-HID sends a game-controller button instead of a key
press. Cine Capture Pro reads that button directly, so capture keeps going when
you click on another program, and no other program ever receives a stray key
press.

1. Download Ultimarc's configuration program (**U-Config**) from ultimarc.com
   and run it with the u-HID plugged in.
2. Select the input the hall sensor is wired to.
3. Assign it to a **gamepad button** (for example *Button 1*) instead of a
   keyboard key.
4. Write the settings to the board.
5. Unplug the u-HID and plug it back in.

Ultimarc's screens change between versions, so the wording may differ
slightly. If the program asks for a *mode*, choose the gamepad (or game
controller) mode.

**Keyboard mode instead?** That works too (see section 6). Ideally make the
u-HID send **F13–F24**, which no normal program uses.

---

## 4. Step 1: Camera tab

1. **Connection type:** leave on *DirectShow*. If the camera misbehaves, try
   *Media Foundation*.
2. **Camera:** pick your camera and press **Connect**.
3. **Resolution and format:** choose the largest **MJPG** mode. With MJPG the
   app saves the camera's own JPEG files with no re-encoding, which is fastest
   and loses nothing compared with what the camera sends.
4. **Set from leader:** put clear leader in the gate with the lamp on and press
   *Set from leader*. Exposure is set just below clipping and white balance is
   made neutral.
5. **Fine-tune:**
   - **Exposure:** a shorter exposure means less motion blur. *-7* is 1/128 s.
   - **Gain:** keep it as low as you can, because gain adds noise.
   - **Sharpness:** set it to 0 or low, because camera sharpening creates halos on film grain.
   - The **Zebra** stripes and the red bar on the **Histogram** show overexposed areas.
6. **Focus:** turn the lens until the **Sharpness** bar in the Focus box is
   full and green. Turn on **Focus peaking** in the toolbar if it helps.
   Double-click the picture to zoom in.
7. **Lock settings:** press **Lock**. The settings are then:
   - saved with the reel
   - put back automatically if the camera disconnects and reconnects
   - checked every 5 seconds during capture.

   Use **Save preset** to reuse them (for example "Super 8 Kodachrome").

**Driver settings...** opens the camera manufacturer's own window, for controls
the app does not show.

---

## 5. Step 2: Trigger tab

1. **Trigger input:** choose *Gamepad button (HID)*.
2. **Device:** pick the u-HID. Ultimarc devices are listed first.
3. Press **Learn trigger...**, then turn the projector by hand until the sensor
   fires once. The app remembers which button it is.
4. Run the projector. The green light flashes on every pulse, and **Projector
   speed** shows the frame rate.

### Set the delay

The camera always lags slightly behind the sensor, so the app waits a set
**delay** after each pulse before taking the next camera frame.

1. Thread blank leader or a test strip, and run the projector at your capture
   speed.
2. Press **Start test**. The last 8 frames appear side by side. Each one has a
   green border if it is sharp, amber if it is slightly blurred, and red if it
   is blurred (caught while the film was moving).
3. Press **Suggest delay** after about 10 frames. The app tries every delay
   on the recorded frames and suggests the middle of the sharp range.
4. Or adjust **Delay after trigger** by hand until every frame is green.

If the app reports **Projector too fast**, slow the projector down. A camera
needs about three of its own frames per film frame to keep up, which is about
10 fps for a 30 fps camera. In practice 1–8 fps is typical.

---

## 6. Other trigger inputs

| Input | When to use it |
| --- | --- |
| **Gamepad button (HID)** | Recommended. Works in the background and sends no key presses. |
| **Keyboard emulator, any window** | A u-HID or similar in keyboard mode. Only key presses from the chosen device count, so your own keyboard cannot trigger a capture. Windows still passes the key to the program in front, so use F13–F24. The app warns you if the key could do something elsewhere. |
| **Keyboard emulator, only while this app is active** | Simple testing. Capture stops counting if you click on another window. |
| **Arduino / serial board** | Sketch in `docs/arduino/CineCaptureTrigger`. Sends one character per frame. |
| **Simulated projector** | Demo and practice without hardware. |

---

## 7. Step 3: Capture tab

1. **New project...:** choose your capture drive and give the project a name.
   One project can hold many reels.
2. **New reel...:** title, gauge (8mm, Super 8, 16mm), positive or negative,
   film stock and notes.
3. **Format:** *Camera JPEG* (default), TIFF or PNG. 16-bit is only offered
   when the camera really delivers more than 8 bits.
4. Press **Start capture**, then start the projector.
5. Watch the counters:
   - **Frames saved** should equal **Triggers**.
   - **Dropped** frames are shown in red and listed in the report.
   - **Write buffer** should stay low. If the drive can't keep up, capture
     pauses with an alarm instead of losing frames.
6. **End of reel:** when clear or black leader shows for 24 frames in a row,
   capture stops and an alarm sounds. Untick *Stop automatically* if you
   prefer to stop by hand.
7. Press **Stop**. A summary appears, and a **session report** (`.txt` and
   `.csv`) is saved in the reel folder with the numbers of any dropped
   frames.

Capturing the same reel again continues the frame numbers where the last
capture stopped.

### Files on disk

```
Family Films 1965/
  project.ccp
  reels/R01_Holiday_1965/
    reel.json         reel details, camera settings, trigger delay
    capture.log       every trigger, frame and alarm, with times
    session_….txt     summary of each capture
    frames/frame_000001.jpg …
```

---

## 8. Troubleshooting

| Problem | What to try |
| --- | --- |
| Camera not listed | Close other camera apps (Teams, Zoom, the Windows Camera app) and press Refresh. Try another USB port. |
| Picture freezes or stutters | Use a USB 3 port directly on the computer, not a hub. Choose a smaller mode. |
| Frames blurred | Run the delay test again. Shorten the exposure. Slow the projector. |
| Dropped frames | Projector too fast for the camera, or the camera delivering fewer fps than expected. Slow down. |
| Trigger not seen | Check the u-HID mode and wiring, then use **Learn trigger** again. Watch the green light while turning the projector by hand. |
| Settings keep changing | Make sure **Lock** is on. Some cameras reset when they reconnect; the app restores them and logs it. |

If the app closes unexpectedly, a `crash.log` file is saved in
`%APPDATA%\CineCapturePro\Cine Capture Pro\`. Please attach it when
[reporting a problem](https://github.com/Treborlee2026/cine-capture-pro/issues).
