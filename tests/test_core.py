"""Tests for the platform-independent capture logic (no Qt, no hardware)."""

import json
import os

import cv2
import numpy as np
import pytest

from cinecapture.camera.base import CameraSettingsLock
from cinecapture.camera.simulated import RigModel, SimulatedCamera, SimulatedRig, simulate
from cinecapture.core import analysis
from cinecapture.core.calibration import check_rate, suggest_delay
from cinecapture.core.frames import Frame
from cinecapture.core.gauges import GAUGES, footage, format_duration, running_time
from cinecapture.core.matcher import FrameMatcher, MatcherSettings
from cinecapture.core.project import Project
from cinecapture.core.session import CaptureSession, SessionSettings
from cinecapture.core.writer import FrameWriter


def _img(value=128, w=64, h=48):
    rng = np.random.default_rng(value)
    return rng.integers(0, 255, size=(h, w, 3), dtype=np.uint8)


def _frame(seq, t, value=None):
    return Frame(seq=seq, timestamp=t, image=_img(seq if value is None else value), width=64,
                 height=48)


# ------------------------------------------------------------------ matcher
class TestMatcher:
    def test_first_frame_after_delay_is_chosen(self):
        m = FrameMatcher(MatcherSettings(delay=0.05))
        m.add_trigger(1.00)
        assert m.add_frame(_frame(1, 1.02)) is None          # too early
        hit = m.add_frame(_frame(2, 1.06))
        assert hit is not None and hit.frame.seq == 2 and hit.trigger.index == 1
        assert m.add_frame(_frame(3, 1.09)) is None          # nothing pending

    def test_frame_older_than_trigger_never_used(self):
        m = FrameMatcher(MatcherSettings(delay=0.0))
        m.add_frame(_frame(1, 0.99))
        m.add_trigger(1.0)
        assert m.pending == 1

    def test_late_frame_goes_to_latest_trigger_and_earlier_is_dropped(self):
        drops = []
        m = FrameMatcher(MatcherSettings(delay=0.05, debounce=0.0),
                         on_drop=lambda t, r: drops.append(t.index))
        m.add_trigger(1.0)
        m.add_trigger(1.2)
        hit = m.add_frame(_frame(9, 1.30))   # after both deadlines: shows film frame 2
        assert hit.trigger.index == 2
        assert drops == [1]
        assert m.stats.dropped == 1 and m.stats.matched == 1

    def test_bounce_ignored(self):
        m = FrameMatcher(MatcherSettings(debounce=0.02))
        assert m.add_trigger(1.000) is not None
        assert m.add_trigger(1.005) is None
        assert m.stats.bounces == 1 and m.stats.triggers == 1

    def test_timeout_drops_trigger(self):
        drops = []
        m = FrameMatcher(MatcherSettings(delay=0.05, max_wait=0.5),
                         on_drop=lambda t, r: drops.append(r))
        m.add_trigger(1.0)
        m.check_timeouts(1.2)
        assert drops == []
        m.check_timeouts(1.6)
        assert len(drops) == 1 and "timeout" in drops[0]

    def test_duplicate_detected(self):
        dups = []
        m = FrameMatcher(MatcherSettings(delay=0.0, debounce=0.0),
                         on_duplicate=lambda mf: dups.append(mf.trigger.index))
        m.add_trigger(1.0)
        m.add_frame(_frame(1, 1.01, value=7))
        m.add_trigger(1.1)
        m.add_frame(_frame(2, 1.11, value=7))  # identical pixels
        assert dups == [2]


# ------------------------------------------------------------------- writer
@pytest.mark.parametrize("fmt", ["jpeg", "tiff8", "tiff16", "png8", "png16"])
def test_writer_formats(tmp_path, fmt):
    warnings = []
    w = FrameWriter(str(tmp_path), fmt, on_warning=warnings.append)
    w.start()
    img = _img()
    jpeg = cv2.imencode(".jpg", img)[1].tobytes()
    assert w.submit(5, Frame(seq=1, timestamp=0, image=img, jpeg=jpeg if fmt == "jpeg" else None))
    w.stop()
    files = os.listdir(tmp_path)
    assert len(files) == 1 and files[0].startswith("frame_000005")
    data = open(tmp_path / files[0], "rb").read()
    if fmt == "jpeg":
        assert data == jpeg                       # passed through untouched
    back = cv2.imread(str(tmp_path / files[0]), cv2.IMREAD_UNCHANGED)
    assert back.dtype == np.uint8               # 8-bit camera never becomes fake 16-bit
    if fmt != "jpeg":
        assert np.array_equal(back, img)        # lossless
    if fmt.endswith("16"):
        assert warnings, "16-bit request with 8-bit data should warn"


def test_writer_16bit_data_kept(tmp_path):
    w = FrameWriter(str(tmp_path), "png16")
    w.start()
    img = (np.arange(48 * 64 * 3, dtype=np.uint16).reshape(48, 64, 3) * 7)
    w.submit(1, Frame(seq=1, timestamp=0, image=img))
    w.stop()
    back = cv2.imread(str(tmp_path / "frame_000001.png"), cv2.IMREAD_UNCHANGED)
    assert back.dtype == np.uint16 and np.array_equal(back, img)


def test_writer_buffer_limit(tmp_path):
    w = FrameWriter(str(tmp_path), "png8", max_buffer_bytes=10_000)
    # Thread not started, so nothing drains: the second frame must be refused.
    big = Frame(seq=1, timestamp=0, image=_img())   # 9216 bytes
    assert w.submit(1, big)
    assert not w.submit(2, big)


def test_writer_reports_errors(tmp_path):
    errors = []
    w = FrameWriter(str(tmp_path / "x"), "jpeg", on_error=lambda n, m: errors.append(n))
    w.start()
    w.submit(1, Frame(seq=1, timestamp=0))   # no data at all
    w.stop()
    assert errors == [1] and w.stats.errors == 1


# ----------------------------------------------------------------- analysis
def test_sharpness_and_blank():
    rng = np.random.default_rng(0)
    sharp = rng.integers(0, 255, (300, 400, 3), dtype=np.uint8)
    blurred = cv2.GaussianBlur(sharp, (31, 31), 10)
    assert analysis.sharpness(sharp) > 5 * analysis.sharpness(blurred)
    clear = np.full((300, 400, 3), 240, np.uint8)
    assert analysis.is_blank(clear) == (True, "clear")
    assert analysis.is_blank(sharp)[0] is False
    assert analysis.clipped_fraction(clear, 230) == 1.0


def test_gauges():
    assert footage(720, GAUGES["super8"]) == 10
    assert footage(400, GAUGES["16mm"]) == 10
    assert running_time(1080, 18) == 60
    assert format_duration(3725) == "1:02:05"


def test_rate_check():
    assert check_rate(30, 5).ok
    bad = check_rate(30, 12)
    assert not bad.ok and "10.0" in bad.message


# ---------------------------------------------------------------- project
def test_project_roundtrip(tmp_path):
    p = Project.create(str(tmp_path), "Family Films 1965")
    r = p.add_reel("Holiday 1965", gauge="8mm", negative=False)
    r.trigger_delay = 0.123
    r.camera = {"locked": True, "values": {"exposure": -6}}
    r.save()
    p.save()
    q = Project.open(p.folder)
    assert q.name == "Family Films 1965"
    assert len(q.reels) == 1
    r2 = q.reels[0]
    assert r2.id == "R01" and r2.gauge == "8mm" and r2.trigger_delay == 0.123
    assert r2.metadata.title == "Holiday 1965"
    assert os.path.isdir(r2.frames_dir)
    assert len(os.listdir(os.path.join(p.folder, "history"))) >= 1
    data = json.load(open(os.path.join(p.folder, "project.ccp")))
    assert data["reels"] == ["reels/R01_Holiday_1965"]


def test_settings_lock_reapplies():
    cam = SimulatedCamera(SimulatedRig())
    cam.set_control("gain", 20)
    lock = CameraSettingsLock()
    lock.lock(cam)
    cam.set_control("gain", 80)       # camera "resets" itself
    cam.set_auto("exposure", True)
    drifts = lock.check(cam)
    assert {d.key for d in drifts} >= {"gain", "exposure (auto mode)"}
    assert lock.apply(cam) == []
    assert cam.get_control("gain") == 20 and cam.get_auto("exposure") is False
    assert lock.check(cam) == []
    again = CameraSettingsLock.from_dict(lock.to_dict())
    assert again.values == lock.values and again.locked


# ------------------------------------------------- end-to-end, simulated rig
def run_rig(session, model, duration):
    for kind, t, frame in simulate(model, duration):
        if kind == "trigger":
            session.on_trigger(t, "simulated")
        else:
            session.on_camera_frame(frame)
        session._matcher.check_timeouts(t)


def test_calibration_finds_sharp_delay():
    model = RigModel(projector_fps=4.0, width=320, height=240)
    s = CaptureSession(None, SessionSettings(delay=0.0, end_of_reel=False))
    s.start_test()
    run_rig(s, model, 6.0)
    result = suggest_delay(s.recording)
    assert result.delay is not None, result.message
    # Physical safe window for this model is roughly 0.09-0.23 s.
    assert 0.10 <= result.delay <= 0.22, result.message


def test_capture_with_good_delay_saves_every_frame_sharp(tmp_path):
    model = RigModel(projector_fps=4.0, width=320, height=240)
    settings = SessionSettings(delay=0.15, end_of_reel=False)
    s = CaptureSession(str(tmp_path / "frames"), settings, first_number=1,
                       log_path=str(tmp_path / "capture.log"))
    blurred = []
    s.subscribe(lambda ev, d: None)
    s.start_capture()
    # Wrap to check which simulated frames were chosen.
    orig = s._matcher.on_match

    def spy(m):
        blurred.append(m.frame.sim_blurred)
        orig(m)
    s._matcher.on_match = spy
    run_rig(s, model, 5.0)
    stats = s.stop()
    n_trig = len(model.trigger_times(5.0))
    assert stats.triggers == n_trig
    assert stats.dropped == 0
    assert stats.saved == stats.matched
    assert not any(blurred), "a good delay must never pick a frame mid pull-down"
    files = sorted(os.listdir(tmp_path / "frames"))
    assert len(files) == stats.saved
    assert files[0] == "frame_000001.jpg"
    log = open(tmp_path / "capture.log").read()
    assert log.count(",frame,") == stats.saved


def test_bad_delay_picks_blurred_frames():
    model = RigModel(projector_fps=4.0, width=320, height=240)
    s = CaptureSession(None, SessionSettings(delay=0.0, end_of_reel=False))
    s.start_test()
    chosen = []
    orig = s._matcher.on_match
    s._matcher.on_match = lambda m: (chosen.append(m.frame.sim_blurred), orig(m))
    run_rig(s, model, 4.0)
    assert any(chosen)


def test_too_fast_projector_reports_drops(tmp_path):
    model = RigModel(projector_fps=20.0, camera_fps=15.0, width=160, height=120)
    s = CaptureSession(str(tmp_path), SessionSettings(delay=0.02, debounce=0.0,
                                                      end_of_reel=False))
    s.start_capture()
    run_rig(s, model, 3.0)
    stats = s.stop()
    assert stats.dropped > 0
    assert stats.matched + stats.dropped == stats.triggers


def test_end_of_reel_detected(tmp_path):
    model = RigModel(projector_fps=4.0, width=320, height=240, film_frames=60)
    events = []
    s = CaptureSession(str(tmp_path), SessionSettings(delay=0.15, end_of_reel_frames=8,
                                                      min_frames_before_end=20))
    s.subscribe(lambda ev, d: events.append((ev, d)))
    s.start_capture()
    run_rig(s, model, 20.0)
    s.stop()
    ends = [d for ev, d in events if ev == "end_of_reel"]
    assert ends and ends[0]["kind"] == "clear"
    assert 60 + 8 <= ends[0]["number"] <= 60 + 10


def test_buffer_full_pauses_and_alarms(tmp_path):
    model = RigModel(projector_fps=4.0, width=320, height=240)
    alarms = []
    s = CaptureSession(str(tmp_path), SessionSettings(delay=0.15, end_of_reel=False,
                                                      buffer_bytes=1))
    s.subscribe(lambda ev, d: alarms.append(d) if ev == "alarm" else None)
    s.start_capture()
    s._writer.stop()          # simulate a disk that never drains
    s._writer._thread = None
    run_rig(s, model, 3.0)
    assert s.state == "paused"
    assert alarms and "buffer" in alarms[0]
    stats = s.stats()
    assert stats.dropped > 0 and stats.matched == stats.triggers - 0


def test_numbering_continues_from_reel(tmp_path):
    model = RigModel(projector_fps=4.0, width=160, height=120)
    s = CaptureSession(str(tmp_path), SessionSettings(delay=0.15, end_of_reel=False),
                       first_number=101)
    s.start_capture()
    run_rig(s, model, 2.0)
    s.stop()
    assert sorted(os.listdir(tmp_path))[0] == "frame_000101.jpg"


def test_report_and_preview_render(tmp_path):
    from cinecapture.core.preview_render import ViewOptions, render
    from cinecapture.core.report import _ranges, write_report
    from cinecapture.core.session import SessionStats
    assert _ranges([3, 4, 5, 9, 11, 12]) == "3-5, 9, 11-12"
    assert _ranges([]) == "none"
    p = Project.create(str(tmp_path), "P")
    r = p.add_reel("Test reel")
    st = SessionStats(triggers=10, saved=9, dropped=1, dropped_numbers=[4], last_number=10)
    txt, csv_path = write_report(r, st, SessionSettings(), {"name": "ELP", "values": {"gain": 0}})
    text = open(txt).read()
    assert "Dropped frames" in text and "1 (4)" in text and "CHECK" in text
    assert os.path.exists(csv_path)
    img = np.full((600, 800, 3), 255, np.uint8)
    img[:, :400] = 40
    res = render(img, ViewOptions(flip_h=True, rotate=90, zebra=True, peaking=True,
                                  max_width=400), (3840, 2160))
    assert res.image.shape[:2] == (400, 300)          # resized then rotated
    assert 0.4 < res.clipped < 0.6
    assert res.source_width == 3840 and res.histogram.shape == (3, 256)
