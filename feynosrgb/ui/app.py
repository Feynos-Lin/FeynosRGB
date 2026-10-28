"""Qt application bootstrap: theme, tray icon and single-instance IPC."""

from __future__ import annotations

import logging
import signal
import sys
from importlib import resources

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import APP_ID, APP_NAME, ipc
from ..service import Service
from .main_window import MainWindow

log = logging.getLogger(__name__)

STYLE = """
QWidget { font-size: 10.5pt; }
QFrame#header { background: palette(alternate-base); border-radius: 10px; }
QLabel#title { font-size: 15pt; font-weight: 700; color: #4fc3ff; }
QLabel#section { font-weight: 600; }
QLabel#hint { color: palette(placeholder-text); }
QLabel#error { color: #ff6b6b; }
QLabel#ok { color: #5ee08a; }
QGroupBox { border: 1px solid rgba(127,127,127,60); border-radius: 8px; margin-top: 14px; padding: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QListWidget { border-radius: 8px; padding: 4px; }
QListWidget::item { padding: 6px 4px; border-radius: 6px; }
QPushButton { padding: 6px 14px; border-radius: 6px; }
QPushButton#accent { background: #2a8cff; color: white; font-weight: 600; }
QPushButton#accent:disabled { background: rgba(127,127,127,80); }
QPushButton#power { font-weight: 700; }
QPushButton#power:checked { background: #1fae5b; color: white; }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 8px 16px; }
"""


def _dark_palette() -> QPalette:
    p = QPalette()
    base, alt, text = QColor(24, 25, 30), QColor(34, 36, 43), QColor(230, 232, 238)
    p.setColor(QPalette.Window, QColor(19, 20, 24))
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, alt)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, alt)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.ToolTipBase, alt)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Highlight, QColor(42, 140, 255))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.PlaceholderText, QColor(140, 145, 158))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(110, 112, 120))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(110, 112, 120))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(110, 112, 120))
    return p


def app_icon() -> QIcon:
    icon = QIcon.fromTheme(APP_ID)
    if icon.isNull():
        try:
            path = resources.files("feynosrgb").joinpath("resources/feynosrgb.svg")
            icon = QIcon(str(path))
        except (OSError, ModuleNotFoundError):
            pass
    return icon


class Tray:
    def __init__(self, app: QApplication, win: MainWindow, svc: Service, icon: QIcon):
        self.win, self.svc = win, svc
        self.tray = QSystemTrayIcon(icon, app)
        self.tray.setToolTip(APP_NAME)
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self._rebuild)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)
        self._rebuild()
        self.tray.show()

    def _rebuild(self) -> None:
        m = self.menu
        m.clear()
        m.addAction("Open FeynosRGB", self.win.bring_to_front)
        m.addSeparator()
        grp = QActionGroup(m)
        cur = self.svc.settings.get("active_profile")
        for name in self.svc.store.names():
            a = QAction(name, m, checkable=True)
            a.setChecked(name == cur)
            a.triggered.connect(lambda _c=False, n=name: self.svc.activate(n))
            grp.addAction(a)
            m.addAction(a)
        m.addSeparator()
        power = QAction("Lights on", m, checkable=True)
        power.setChecked(self.svc.engine.power)
        power.toggled.connect(self.svc.engine.set_power)
        m.addAction(power)
        m.addSeparator()
        m.addAction("Quit", self._quit)

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.win.isVisible() and not self.win.isMinimized():
                self.win.hide()
            else:
                self.win.bring_to_front()

    def _quit(self) -> None:
        self.win.quitting = True
        QApplication.quit()


def run_gui(minimized: bool = False, demo: bool = False) -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setDesktopFileName(APP_ID)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if app.style().name().lower() not in ("breeze", "adwaita", "adwaita-dark"):
        app.setStyle("Fusion")
        app.setPalette(_dark_palette())
    app.setStyleSheet(STYLE)

    svc = Service(demo=demo)
    server = ipc.Server(svc.handle)
    if not server.start():
        ipc.send({"cmd": "show"})
        return 0
    svc.start()

    icon = app_icon()
    win = MainWindow(svc, icon)
    tray_ok = QSystemTrayIcon.isSystemTrayAvailable()
    win.tray_available = tray_ok
    tray = Tray(app, win, svc, icon) if tray_ok else None

    start_hidden = (minimized or svc.settings.get("start_minimized")) and tray_ok
    if not start_hidden:
        win.show()
    elif minimized and not tray_ok:
        win.showMinimized()

    if not tray_ok:
        # Without a tray (stock GNOME needs the AppIndicator extension),
        # closing the window quits.
        app.setQuitOnLastWindowClosed(True)

    # Let Ctrl+C in a terminal quit cleanly.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    pulse = QTimer()
    pulse.start(250)
    pulse.timeout.connect(lambda: None)

    def _cleanup():
        win._flush_save()
        server.stop()
        svc.shutdown()

    app.aboutToQuit.connect(_cleanup)
    rc = app.exec()
    del tray
    return rc
