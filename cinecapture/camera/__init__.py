"""Camera adapters. Each platform provides a :class:`~.base.Camera` implementation."""

from .base import Camera, ControlInfo, DeviceInfo, VideoMode, CameraSettingsLock  # noqa: F401


def available_backends():
    """Camera back-ends usable on this computer, best first."""
    import sys
    out = []
    if sys.platform == "win32":
        out += ["dshow", "msmf"]
    elif sys.platform == "darwin":
        out += ["avfoundation"]
    else:
        out += ["v4l2"]
    out.append("simulated")
    return out


def create_camera(backend: str) -> Camera:
    if backend == "simulated":
        from .simulated import SimulatedCamera
        return SimulatedCamera()
    from .opencv_camera import OpenCVCamera
    return OpenCVCamera(backend)
