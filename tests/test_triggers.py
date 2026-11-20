"""Trigger tests that run without hardware."""

import time

import pytest

from cinecapture.trigger import create_trigger, source_kinds
from cinecapture.trigger.hid_gamepad import learn_button
from cinecapture.trigger.keyboard import KeyboardTrigger, key_is_risky, key_name


def _reader(reports, delay_first=0.35):
    """Fake hid read(): returns queued reports, first ones after the settle period."""
    start = time.monotonic()
    queue = list(reports)

    def read(size, timeout_ms):
        if not queue:
            time.sleep(timeout_ms / 1000)
            return []
        due, rep = queue[0]
        if time.monotonic() - start < due:
            time.sleep(0.005)
            return []
        queue.pop(0)
        return rep
    return read


def test_learn_button_active_high():
    reports = [(0.0, [1, 0x80, 0x80, 0x00]),
               (0.1, [1, 0x80, 0x80, 0x00]),
               (0.5, [1, 0x80, 0x80, 0x04]),     # button 3 pressed
               (0.6, [1, 0x80, 0x80, 0x00])]
    byte, mask, active_high = learn_button(_reader(reports), timeout=3)
    assert (byte, mask, active_high) == (3, 0x04, True)


def test_learn_button_ignores_noisy_axis():
    reports = [(0.0, [1, 0x80, 0x00]),
               (0.1, [1, 0x81, 0x00]),            # axis jitter during settle
               (0.2, [1, 0x80, 0x00]),
               (0.5, [1, 0x81, 0x00]),            # jitter again after settle: ignored
               (0.6, [1, 0x80, 0x10])]            # the real button
    byte, mask, _ = learn_button(_reader(reports), timeout=3)
    assert (byte, mask) == (2, 0x10)


def test_learn_button_timeout():
    with pytest.raises(TimeoutError):
        learn_button(_reader([]), timeout=0.3)


def test_keyboard_focused_mode_fires_once_per_press():
    trig = KeyboardTrigger({"mode": "focused", "vk": 0x7C})   # F13
    got = []
    trig.start(lambda t, src: got.append((t, src)))
    assert trig.key_from_ui(0x7C, True, 1.0)
    assert trig.key_from_ui(0x7C, True, 1.01)      # auto-repeat while held: ignored
    assert trig.key_from_ui(0x7C, False, 1.05)
    assert not trig.key_from_ui(0x20, True, 1.1)   # other keys pass through
    assert trig.key_from_ui(0x7C, True, 1.2)
    trig.stop()
    assert [t for t, _ in got] == [1.0, 1.2]
    assert got[0][1] == "keyboard"


def test_key_names_and_risk():
    assert key_name(0x7C) == "F13" and key_name(0x20) == "Space"
    assert not key_is_risky(0x7C) and key_is_risky(0x20)


def test_factory_knows_all_sources():
    assert set(source_kinds()) >= {"hid", "keyboard", "serial", "simulated"}
    assert create_trigger({"kind": "keyboard", "mode": "focused", "vk": 0x7D}).describe() \
        .startswith("Key F14")
    with pytest.raises(ValueError):
        create_trigger({"kind": "telepathy"})


def test_simulated_trigger_and_camera_run_in_real_time():
    from cinecapture.camera.simulated import RigModel, SimulatedCamera, SimulatedRig
    from cinecapture.core.session import CaptureSession, SessionSettings
    from cinecapture.trigger.simulated import SimulatedTriggerSource
    rig = SimulatedRig(RigModel(projector_fps=8, width=160, height=120))
    SimulatedRig._instance = rig
    cam = SimulatedCamera(rig)
    s = CaptureSession(None, SessionSettings(delay=0.1, end_of_reel=False))
    cam.on_frame = s.on_camera_frame
    trig = SimulatedTriggerSource({})
    s.start_test()
    cam.open(cam.list_devices()[0])
    trig.start(s.on_trigger)
    time.sleep(1.2)
    trig.stop()
    cam.close()
    stats = s.stop()
    SimulatedRig._instance = None
    assert stats.triggers >= 7
    assert stats.matched >= stats.triggers - 1
    assert 20 <= stats.camera_rate <= 40
