"""Builds the real window offscreen with the virtual device and pokes at it."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


def test_main_window_smoke(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setattr("feynosrgb.devices.DETECTORS", [])
    from PySide6.QtWidgets import QApplication
    from feynosrgb.service import Service
    from feynosrgb.ui.app import STYLE, app_icon
    from feynosrgb.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    svc = Service(demo=True)
    svc.start()
    try:
        win = MainWindow(svc, app_icon())
        win.show()
        app.processEvents()
        # every effect can be selected and its editor built
        for i in range(win.effect_combo.count()):
            if win.effect_combo.itemData(i):
                win.effect_combo.setCurrentIndex(i)
                app.processEvents()
        # select a single zone and give it its own effect
        win.zone_list.setCurrentRow(2)
        win.override_cb.setChecked(True)
        app.processEvents()
        key = win.selected_zone
        assert key in win.profile.zones
        win.brightness.setValue(40)
        win.mode_hw.setChecked(True)
        app.processEvents()
        assert svc.engine.profile.mode == "onboard"
        win.mode_sw.setChecked(True)
        for tab in range(win.tabs.count()):
            win.tabs.setCurrentIndex(tab)
            app.processEvents()
        win.preview.repaint()
        win._flush_save()
        saved = svc.store.get(win.profile.name)
        assert key in saved.zones and abs(saved.brightness - 0.4) < 1e-6
        win.quitting = True
        win.close()
    finally:
        svc.shutdown()
