"""Application start-up: crash log, theme and main window."""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime


def _crash_log_path() -> str:
    try:
        from .ui.settings_store import settings_dir
        base = settings_dir()
    except Exception:  # noqa: BLE001
        base = os.path.expanduser("~")
    return os.path.join(base, "crash.log")


_SMOKE = False


def _install_crash_handler() -> None:
    def handler(exc_type, exc, tb):
        if _SMOKE:
            traceback.print_exception(exc_type, exc, tb)
            from PySide6.QtWidgets import QApplication
            QApplication.exit(3)
            return
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        path = _crash_log_path()
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"\n=== {datetime.now().isoformat()} ===\n{text}")
        except OSError:
            pass
        sys.__excepthook__(exc_type, exc, tb)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is not None:
                QMessageBox.critical(None, "Cine Capture Pro - unexpected error",
                                     f"Something went wrong:\n\n{exc}\n\nDetails were saved "
                                     f"to:\n{path}\n\nPlease include that file when reporting "
                                     "the problem.")
        except Exception:  # noqa: BLE001
            pass
    sys.excepthook = handler


def main(argv=None) -> int:
    """Start the app. Start-up errors are written to a log instead of a silent crash."""
    log = os.environ.get("CCP_LOG")
    if log:
        # Packaged (windowed) builds have no console: send output to a file.
        fh = open(log, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = fh
    try:
        return _main(argv)
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        text = traceback.format_exc()
        print(text, file=sys.stderr)
        try:
            with open(_crash_log_path(), "a", encoding="utf-8") as fh2:
                fh2.write(f"\n=== {datetime.now().isoformat()} (start-up) ===\n{text}")
        except OSError:
            pass
        if _SMOKE or (argv is None and "--smoke-test" in sys.argv):
            return 6
        raise


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cinecapture", description="Cine Capture Pro")
    parser.add_argument("--demo", action="store_true",
                        help="use a simulated projector and camera (no hardware needed)")
    parser.add_argument("--smoke-test", action="store_true",
                        help="open the main window and quit (used by the automated build)")
    args = parser.parse_args(argv)

    from PySide6.QtWidgets import QApplication

    from . import APP_NAME
    from .ui.main_window import MainWindow
    from .ui.settings_store import Settings
    from .ui.theme import apply_theme

    global _SMOKE
    _SMOKE = args.smoke_test
    _install_crash_handler()
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("CineCapturePro")
    apply_theme(app)
    settings = Settings()
    win = MainWindow(settings, demo=args.demo or args.smoke_test)
    win.show()
    if args.smoke_test:
        return _smoke_test(app, win)
    return app.exec()


def _smoke_test(app, win) -> int:
    """Automated check used by the Windows build: a real capture with the simulated rig."""
    import tempfile

    from PySide6.QtCore import QTimer

    from .core.project import Project

    win.non_interactive = True
    result = {"code": 0}
    tmp = tempfile.mkdtemp(prefix="ccp_smoke_")

    def step_start():
        if win.camera is None:
            print("SMOKE: camera did not open"); result["code"] = 2; app.exit(2); return
        win.camera_panel.lock_btn.setChecked(True)
        proj = Project.create(tmp, "Smoke test")
        proj.add_reel("Reel 1")
        win.capture_panel.open_project(proj.folder)
        win.trigger_panel.delay.setValue(150)
        win.start_capture()
        if win.session is None:
            print("SMOKE: capture did not start"); app.exit(2); return
        QTimer.singleShot(3000, step_stop)

    def step_stop():
        win.stop_capture()
        st = getattr(win, "last_stats", None)
        print(f"SMOKE: triggers={st.triggers if st else '?'} saved={st.saved if st else '?'} "
              f"dropped={st.dropped if st else '?'}")
        ok = st is not None and st.saved >= 5 and st.dropped == 0
        win.close()
        app.exit(0 if ok else 4)

    QTimer.singleShot(1500, step_start)
    QTimer.singleShot(30000, lambda: app.exit(5))      # safety net
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
