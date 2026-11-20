"""Trigger from a keyboard emulator (e.g. a u-HID left in keyboard mode).

Two modes:

``background`` (Windows)
    Uses the Windows Raw Input API through a hidden window on its own thread.
    Key presses are received whichever window has focus, and only presses from
    the chosen device count, so the computer's own keyboard cannot trigger a
    capture by accident. Windows still delivers the key to the program in
    front, so pick a key no program uses (F13-F24 are ideal).

``focused`` (any platform)
    The key only works while Cine Capture Pro is the active window. The UI
    passes key presses to :meth:`KeyboardTrigger.key_from_ui`.
"""

from __future__ import annotations

import re
import sys
import threading
from typing import Dict, List, Optional

from ..core.frames import now
from .base import TriggerDevice, TriggerSource

# Virtual-key codes for the friendly names shown in the UI.
VK_NAMES: Dict[int, str] = {0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x1B: "Esc",
                            0x20: "Space", 0x21: "Page Up", 0x22: "Page Down", 0x23: "End",
                            0x24: "Home", 0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
                            0x2D: "Insert", 0x2E: "Delete"}
VK_NAMES.update({0x30 + i: str(i) for i in range(10)})
VK_NAMES.update({0x41 + i: chr(0x41 + i) for i in range(26)})
VK_NAMES.update({0x70 + i: f"F{i + 1}" for i in range(24)})          # F1..F24
VK_NAMES.update({0x60 + i: f"Numpad {i}" for i in range(10)})

SAFE_KEYS = set(range(0x7C, 0x88))      # F13-F24: not used by normal programs


def key_name(vk: int) -> str:
    return VK_NAMES.get(vk, f"key code {vk}")


def key_is_risky(vk: int) -> bool:
    """True for keys that would do something in other programs."""
    return vk not in SAFE_KEYS


def _vid_pid(device_name: str) -> str:
    m = re.search(r"VID_([0-9A-Fa-f]{4}).*?PID_([0-9A-Fa-f]{4})", device_name)
    return f"{m.group(1).upper()}:{m.group(2).upper()}" if m else ""


class KeyboardTrigger(TriggerSource):
    kind = "keyboard"
    label = "Keyboard emulator"

    def __init__(self, config: Optional[Dict] = None) -> None:
        super().__init__(config)
        self.config.setdefault("mode", "background" if sys.platform == "win32" else "focused")
        self._listener: Optional["_RawInputListener"] = None
        self._down = False

    @property
    def works_in_background(self) -> bool:  # type: ignore[override]
        return self.config.get("mode") == "background"

    @classmethod
    def background_supported(cls) -> bool:
        return sys.platform == "win32"

    def is_configured(self) -> bool:
        if "vk" not in self.config:
            return False
        return self.config.get("mode") == "focused" or bool(self.config.get("device"))

    def describe(self) -> str:
        if "vk" not in self.config:
            return "Keyboard emulator: not set up"
        where = ("any window, chosen device only" if self.config.get("mode") == "background"
                 else "only while the app is active")
        return f"Key {key_name(int(self.config['vk']))} ({where})"

    def list_devices(self) -> List[TriggerDevice]:
        if sys.platform != "win32":
            return []
        out = []
        for handle, name in _raw_keyboards():
            vp = _vid_pid(name)
            ultimarc = vp.startswith("D209")
            out.append(TriggerDevice(name, f"Keyboard device {vp or name[-24:]}",
                                     "Ultimarc device" if ultimarc else "", ultimarc))
        return out

    # ------------------------------------------------------------- learning
    def learn(self, timeout: float = 15.0) -> Dict:
        """Background mode: wait for one key press and remember its device and key."""
        if not self.background_supported():
            raise RuntimeError("Background keyboard capture is only available on Windows. "
                               "Use 'only while the app is active' mode instead.")
        got: Dict = {}
        done = threading.Event()

        def on_key(device: str, vk: int, down: bool) -> None:
            if down and not done.is_set():
                got.update(device=device, vk=vk)
                done.set()

        listener = _RawInputListener(on_key)
        listener.start()
        try:
            if not done.wait(timeout):
                raise TimeoutError("No key press was seen. Press the trigger (or turn the "
                                   "projector by hand) once.")
        finally:
            listener.stop()
        self.config.update(got)
        self.config["device_id"] = _vid_pid(got["device"])
        return dict(self.config)

    def learn_from_ui(self, vk: int) -> None:
        """Focused mode: the UI reports the key the user pressed."""
        self.config["vk"] = int(vk)

    # -------------------------------------------------------------- running
    def start(self, callback) -> None:  # type: ignore[override]
        self._callback = callback
        self._down = False
        if self.config.get("mode") == "background":
            want_dev = self.config.get("device", "")
            want_vk = int(self.config["vk"])
            state = {"down": False}

            def on_key(device: str, vk: int, down: bool) -> None:
                if vk != want_vk or not _same_device(device, want_dev):
                    return
                if down and not state["down"]:
                    self._fire(now())
                state["down"] = down

            self._listener = _RawInputListener(on_key, on_error=self.on_error)
            self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._callback = None

    @property
    def running(self) -> bool:
        return self._callback is not None

    def key_from_ui(self, vk: int, down: bool, timestamp: Optional[float] = None) -> bool:
        """Focused mode: called by the UI for each key event. Returns True if consumed."""
        if self.config.get("mode") != "focused" or self._callback is None:
            return False
        if vk != int(self.config.get("vk", -1)):
            return False
        if down and not self._down:
            self._fire(timestamp if timestamp is not None else now())
        self._down = down
        return True


def _same_device(a: str, b: str) -> bool:
    if not b:
        return True
    if a.lower() == b.lower():
        return True
    # Device paths can change after re-plugging; fall back to vendor/product IDs.
    va, vb = _vid_pid(a), _vid_pid(b)
    return bool(va) and va == vb


# ===================================================================== Windows
def _raw_keyboards():
    """List (handle, device name) for every keyboard-type Raw Input device."""
    if sys.platform != "win32":
        return []
    w = _win()
    count = w.UINT(0)
    size = w.ctypes.sizeof(w.RAWINPUTDEVICELIST)
    w.user32.GetRawInputDeviceList(None, w.ctypes.byref(count), size)
    if count.value == 0:
        return []
    arr = (w.RAWINPUTDEVICELIST * count.value)()
    n = w.user32.GetRawInputDeviceList(arr, w.ctypes.byref(count), size)
    out = []
    for i in range(max(0, int(n) if n != 0xFFFFFFFF else 0)):
        if arr[i].dwType == w.RIM_TYPEKEYBOARD:
            out.append((arr[i].hDevice, w.device_name(arr[i].hDevice)))
    return out


_WIN = None


def _win():
    """Load the Win32 declarations once (Windows only)."""
    global _WIN
    if _WIN is not None:
        return _WIN
    import ctypes
    from ctypes import wintypes

    class NS:
        pass

    w = NS()
    w.ctypes = ctypes
    w.UINT = wintypes.UINT
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    w.user32, w.kernel32 = user32, kernel32
    LRESULT = ctypes.c_ssize_t
    w.WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                   wintypes.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                    ("lpfnWndProc", w.WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HANDLE), ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]

    class RAWINPUTDEVICE(ctypes.Structure):
        _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                    ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]

    class RAWINPUTHEADER(ctypes.Structure):
        _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                    ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]

    class RAWKEYBOARD(ctypes.Structure):
        _fields_ = [("MakeCode", wintypes.USHORT), ("Flags", wintypes.USHORT),
                    ("Reserved", wintypes.USHORT), ("VKey", wintypes.USHORT),
                    ("Message", wintypes.UINT), ("ExtraInformation", wintypes.ULONG)]

    class RAWINPUTKB(ctypes.Structure):
        _fields_ = [("header", RAWINPUTHEADER), ("keyboard", RAWKEYBOARD)]

    class RAWINPUTDEVICELIST(ctypes.Structure):
        _fields_ = [("hDevice", wintypes.HANDLE), ("dwType", wintypes.DWORD)]

    w.WNDCLASSEXW, w.RAWINPUTDEVICE, w.RAWINPUTHEADER = WNDCLASSEXW, RAWINPUTDEVICE, RAWINPUTHEADER
    w.RAWINPUTKB, w.RAWINPUTDEVICELIST = RAWINPUTKB, RAWINPUTDEVICELIST
    w.MSG = wintypes.MSG
    w.WM_INPUT, w.WM_CLOSE, w.WM_DESTROY = 0x00FF, 0x0010, 0x0002
    w.RID_INPUT, w.RIM_TYPEKEYBOARD, w.RIDEV_INPUTSINK = 0x10000003, 1, 0x00000100
    w.RIDEV_REMOVE, w.RIDI_DEVICENAME, w.RI_KEY_BREAK = 0x00000001, 0x20000007, 0x01
    w.HWND_MESSAGE = wintypes.HWND(-3)

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                      wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                       wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                       wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT,
                                               wintypes.UINT]
    user32.RegisterRawInputDevices.restype = wintypes.BOOL
    user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
                                       ctypes.POINTER(wintypes.UINT), wintypes.UINT]
    user32.GetRawInputData.restype = wintypes.UINT
    user32.GetRawInputDeviceList.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.UINT),
                                             wintypes.UINT]
    user32.GetRawInputDeviceList.restype = wintypes.UINT
    user32.GetRawInputDeviceInfoW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
                                              ctypes.POINTER(wintypes.UINT)]
    user32.GetRawInputDeviceInfoW.restype = wintypes.UINT
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                   wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                    wintypes.LPARAM]
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    def device_name(handle) -> str:
        size = wintypes.UINT(0)
        user32.GetRawInputDeviceInfoW(handle, w.RIDI_DEVICENAME, None, ctypes.byref(size))
        if size.value == 0:
            return ""
        buf = ctypes.create_unicode_buffer(size.value + 1)
        user32.GetRawInputDeviceInfoW(handle, w.RIDI_DEVICENAME, buf, ctypes.byref(size))
        return buf.value

    w.device_name = device_name
    _WIN = w
    return w


class _RawInputListener:
    """Hidden message-only window that receives keyboard Raw Input in the background."""

    _class_counter = 0

    def __init__(self, on_key, on_error=None) -> None:
        self.on_key = on_key
        self.on_error = on_error
        self._hwnd = None
        self._ready = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._names: Dict[int, str] = {}
        self._error: Optional[str] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="RawInputKeyboard", daemon=True)
        self._thread.start()
        self._ready.wait(5.0)
        if self._error:
            raise RuntimeError(self._error)

    def stop(self) -> None:
        if self._hwnd:
            w = _win()
            w.user32.PostMessageW(self._hwnd, w.WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(2.0)
            self._thread = None

    def _run(self) -> None:
        w = _win()
        ctypes = w.ctypes
        user32 = w.user32
        hinst = w.kernel32.GetModuleHandleW(None)
        _RawInputListener._class_counter += 1
        class_name = f"CineCaptureRawInput{_RawInputListener._class_counter}"
        header_size = ctypes.sizeof(w.RAWINPUTHEADER)

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == w.WM_INPUT:
                try:
                    self._handle_input(lparam, header_size)
                except Exception:  # noqa: BLE001
                    pass
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
            if msg == w.WM_CLOSE:
                rid = w.RAWINPUTDEVICE(1, 6, w.RIDEV_REMOVE, None)
                user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(rid))
                user32.DestroyWindow(hwnd)
                return 0
            if msg == w.WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = w.WNDPROC(wndproc)    # keep a reference so it is not garbage collected
        wc = w.WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(w.WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = class_name
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            self._fail("could not register the input window class")
            return
        hwnd = user32.CreateWindowExW(0, class_name, "Cine Capture Pro trigger", 0, 0, 0, 0, 0,
                                      w.HWND_MESSAGE, None, hinst, None)
        if not hwnd:
            self._fail("could not create the input window")
            return
        self._hwnd = hwnd
        rid = w.RAWINPUTDEVICE(1, 6, w.RIDEV_INPUTSINK, hwnd)   # generic desktop / keyboard
        if not user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(rid)):
            self._fail("Windows refused background keyboard input "
                       f"(error {ctypes.get_last_error()})")
            user32.DestroyWindow(hwnd)
            return
        self._ready.set()
        msg = w.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._hwnd = None
        user32.UnregisterClassW(class_name, hinst)

    def _fail(self, text: str) -> None:
        self._error = text
        if self.on_error:
            self.on_error(text)
        self._ready.set()

    def _handle_input(self, lparam, header_size: int) -> None:
        w = _win()
        ctypes = w.ctypes
        size = w.UINT(0)
        w.user32.GetRawInputData(lparam, w.RID_INPUT, None, ctypes.byref(size), header_size)
        if size.value == 0:
            return
        buf = ctypes.create_string_buffer(size.value)
        got = w.user32.GetRawInputData(lparam, w.RID_INPUT, buf, ctypes.byref(size), header_size)
        if got == 0xFFFFFFFF or got < ctypes.sizeof(w.RAWINPUTKB):
            return
        ri = w.RAWINPUTKB.from_buffer_copy(buf.raw[:ctypes.sizeof(w.RAWINPUTKB)])
        if ri.header.dwType != w.RIM_TYPEKEYBOARD:
            return
        handle = ri.header.hDevice or 0
        name = self._names.get(handle)
        if name is None:
            name = w.device_name(ri.header.hDevice) if handle else ""
            self._names[handle] = name
        down = not (ri.keyboard.Flags & w.RI_KEY_BREAK)
        self.on_key(name, int(ri.keyboard.VKey), down)
