"""Main window."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox,
                               QFormLayout, QFrame, QGroupBox, QHBoxLayout, QInputDialog,
                               QLineEdit,
                               QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPushButton, QRadioButton, QScrollArea, QSlider, QSpinBox,
                               QSplitter, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, autostart
from ..config import EffectSpec, Profile
from ..effects import CATEGORIES, EFFECTS, get_effect
from ..service import Service
from .widgets import ColorButton, LedPreview, ParamForm

ALL_ZONES = "*"


class _Bridge(QObject):
    """Moves engine-thread callbacks onto the GUI thread."""
    status_changed = Signal()
    profile_changed = Signal(str)
    show_requested = Signal()


class MainWindow(QMainWindow):
    def __init__(self, svc: Service, icon: QIcon):
        super().__init__()
        self.svc = svc
        self.engine = svc.engine
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icon)
        self.resize(1040, 720)
        self.quitting = False

        self.bridge = _Bridge()
        self.engine.on_status = self.bridge.status_changed.emit
        svc.on_profile_changed = self.bridge.profile_changed.emit
        svc.on_show = self.bridge.show_requested.emit
        self.bridge.status_changed.connect(self._refresh_status)
        self.bridge.profile_changed.connect(self._on_external_profile)
        self.bridge.show_requested.connect(self.bring_to_front)

        self.profile: Profile = self.engine.profile
        self.selected_zone = ALL_ZONES
        self._loading = False
        self._save_timer = QTimer(self, singleShot=True, interval=700)
        self._save_timer.timeout.connect(self._save_profile)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 8)
        root.setSpacing(10)
        root.addWidget(self._build_header())
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_lighting_tab(), "Lighting")
        self.tabs.addTab(self._build_onboard_tab(), "Onboard effect")
        self.tabs.addTab(self._build_devices_tab(), "Devices")
        self.tabs.addTab(self._build_settings_tab(), "Settings")
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self.status = self.statusBar()
        self._fps_timer = QTimer(self, interval=1000)
        self._fps_timer.timeout.connect(self._refresh_statusbar)
        self._fps_timer.start()

        self._reload_profiles()
        self._load_profile_into_ui()
        self._refresh_status()

    # ================================================================ header
    def _build_header(self) -> QWidget:
        w = QFrame(objectName="header")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(12, 8, 12, 8)
        title = QLabel(APP_NAME, objectName="title")
        lay.addWidget(title)
        lay.addSpacing(18)
        lay.addWidget(QLabel("Profile"))
        self.profile_combo = QComboBox(minimumWidth=200)
        self.profile_combo.currentTextChanged.connect(self._on_profile_selected)
        lay.addWidget(self.profile_combo)
        for text, tip, slot in [("New", "Create a new profile", self._new_profile),
                                ("Copy", "Duplicate this profile", self._dup_profile),
                                ("Rename", "Rename this profile", self._rename_profile),
                                ("Delete", "Delete this profile", self._delete_profile)]:
            b = QToolButton(text=text, toolTip=tip)
            b.clicked.connect(slot)
            lay.addWidget(b)
        lay.addStretch(1)
        lay.addWidget(QLabel("Brightness"))
        self.brightness = QSlider(Qt.Horizontal, minimumWidth=160)
        self.brightness.setRange(0, 100)
        self.brightness.valueChanged.connect(self._on_brightness)
        lay.addWidget(self.brightness)
        self.power_btn = QPushButton("On", checkable=True, objectName="power", minimumWidth=70)
        self.power_btn.setChecked(self.engine.power)
        self.power_btn.toggled.connect(self._on_power)
        lay.addWidget(self.power_btn)
        return w

    # ============================================================== lighting
    def _build_lighting_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 10, 0, 0)

        mode_row = QHBoxLayout()
        self.mode_sw = QRadioButton("Live effects (rendered by FeynosRGB)")
        self.mode_hw = QRadioButton("Onboard effect (runs on the motherboard)")
        grp = QButtonGroup(self)
        grp.addButton(self.mode_sw)
        grp.addButton(self.mode_hw)
        self.mode_sw.toggled.connect(self._on_mode)
        mode_row.addWidget(self.mode_sw)
        mode_row.addWidget(self.mode_hw)
        mode_row.addStretch(1)
        outer.addLayout(mode_row)

        split = QSplitter(Qt.Horizontal)
        # zones
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("Zones", objectName="section"))
        self.zone_list = QListWidget()
        self.zone_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.zone_list.currentItemChanged.connect(self._on_zone_selected)
        self.zone_list.itemChanged.connect(self._on_zone_checked)
        ll.addWidget(self.zone_list, 1)
        hint = QLabel("Untick a zone to turn it off in this profile. Pick a zone to give it its own effect.",
                      wordWrap=True, objectName="hint")
        ll.addWidget(hint)
        split.addWidget(left)

        # effect editor
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        self.override_cb = QCheckBox("Give this zone its own effect")
        self.override_cb.toggled.connect(self._on_override)
        rl.addWidget(self.override_cb)
        row = QHBoxLayout()
        row.addWidget(QLabel("Effect", objectName="section"))
        self.effect_combo = QComboBox(minimumWidth=240)
        for cat in CATEGORIES:
            self.effect_combo.addItem(f"— {cat} —")
            idx = self.effect_combo.count() - 1
            self.effect_combo.model().item(idx).setEnabled(False)
            for e in EFFECTS:
                if e.category == cat:
                    self.effect_combo.addItem(e.name, e.id)
        self.effect_combo.currentIndexChanged.connect(self._on_effect_selected)
        row.addWidget(self.effect_combo)
        row.addStretch(1)
        rl.addLayout(row)
        self.effect_desc = QLabel(wordWrap=True, objectName="hint")
        rl.addWidget(self.effect_desc)
        self.param_form = ParamForm(sensor_source=self.engine.sensors.available)
        self.param_form.changed.connect(self._on_params)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setWidget(self.param_form)
        rl.addWidget(scroll, 1)
        split.addWidget(right)
        split.setSizes([300, 700])
        outer.addWidget(split, 1)

        prev_box = QGroupBox("Live preview")
        pl = QVBoxLayout(prev_box)
        self.preview = LedPreview(lambda: self.engine.last_frame, self.engine.zone_keys)
        pl.addWidget(self.preview)
        outer.addWidget(prev_box)
        return tab

    # =============================================================== onboard
    def _build_onboard_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 14, 4, 4)
        info = QLabel(
            "Onboard effects run on the motherboard's lighting controller itself, so they keep "
            "going without FeynosRGB running and use no CPU. Use <b>Save to motherboard</b> to make "
            "the effect stick through reboots, in the BIOS, and while the PC is shut down "
            "(onboard LEDs only).", wordWrap=True)
        lay.addWidget(info)
        form = QFormLayout()
        self.hw_mode_combo = QComboBox(minimumWidth=240)
        self.hw_mode_combo.currentIndexChanged.connect(self._on_hw_changed)
        self.hw_color = ColorButton("#ff0040")
        self.hw_color.changed.connect(lambda _c: self._on_hw_changed())
        form.addRow("Effect", self.hw_mode_combo)
        form.addRow("Color", self.hw_color)
        lay.addLayout(form)
        row = QHBoxLayout()
        self.hw_use_btn = QPushButton("Use onboard effect now")
        self.hw_use_btn.clicked.connect(lambda: self.mode_hw.setChecked(True))
        self.hw_save_btn = QPushButton("Save to motherboard", objectName="accent")
        self.hw_save_btn.clicked.connect(self._save_onboard)
        row.addWidget(self.hw_use_btn)
        row.addWidget(self.hw_save_btn)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        return tab

    # =============================================================== devices
    def _build_devices_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 14, 4, 4)
        row = QHBoxLayout()
        self.dev_summary = QLabel(wordWrap=True)
        row.addWidget(self.dev_summary, 1)
        rescan = QPushButton("Rescan devices")
        rescan.clicked.connect(self._rescan)
        row.addWidget(rescan)
        lay.addLayout(row)
        self.dev_scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        lay.addWidget(self.dev_scroll, 1)
        return tab

    def _populate_devices(self) -> None:
        st = self.engine.status()
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        for ds in st.devices:
            d = ds.device
            box = QGroupBox(d.name)
            form = QFormLayout(box)
            state = QLabel("Connected" if ds.ok else f"Not working: {ds.error}", wordWrap=True)
            state.setObjectName("ok" if ds.ok else "error")
            form.addRow("Status", state)
            if d.info.firmware:
                form.addRow("Firmware", QLabel(d.info.firmware))
            form.addRow("Location", QLabel(d.info.location))
            for z in d.zones:
                if z.resizable:
                    spin = QSpinBox()
                    spin.setRange(z.min_leds, z.max_leds)
                    spin.setValue(z.led_count)
                    spin.setSuffix(" LEDs")
                    spin.setKeyboardTracking(False)
                    spin.valueChanged.connect(
                        lambda v, uid=d.uid, zid=z.id: self._resize_zone(uid, zid, v))
                    form.addRow(z.name, spin)
                else:
                    form.addRow(z.name, QLabel(f"{z.led_count} LEDs"))
            if any(z.resizable for z in d.zones):
                form.addRow("", QLabel("Set each ARGB header to the number of LEDs on the fans or "
                                       "strips connected to it (0 if nothing is plugged in). Daisy-"
                                       "chained devices add up.", wordWrap=True, objectName="hint"))
            lay.addWidget(box)
        if not st.devices:
            lay.addWidget(QLabel(
                "No supported devices were found.<br><br>Supported now: ASUS Aura USB motherboards "
                "(e.g. ROG Strix B550-F Gaming). Run <tt>feynosrgb status</tt> in a terminal for details, "
                "or tick <b>Show a virtual demo device</b> in Settings to try the app without hardware.",
                wordWrap=True))
        for e in st.detect_errors:
            lay.addWidget(QLabel(f"Detection error: {e}", wordWrap=True, objectName="error"))
        lay.addStretch(1)
        self.dev_scroll.setWidget(holder)
        ok = sum(1 for s in st.devices if s.ok)
        self.dev_summary.setText(f"{ok} of {len(st.devices)} device(s) working.")

    # ============================================================== settings
    def _build_settings_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(4, 14, 4, 4)
        s = self.svc.settings
        form = QFormLayout()
        self.cb_autostart = QCheckBox("Start FeynosRGB when I log in")
        self.cb_autostart.setChecked(autostart.is_enabled())
        self.cb_autostart.toggled.connect(self._on_autostart)
        self.cb_min = QCheckBox("Start minimized to the tray")
        self.cb_min.setChecked(bool(s.get("start_minimized")))
        self.cb_min.toggled.connect(lambda v: self._set_setting("start_minimized", v))
        self.cb_tray = QCheckBox("Closing the window keeps lighting running in the tray")
        self.cb_tray.setChecked(bool(s.get("close_to_tray", True)))
        self.cb_tray.toggled.connect(lambda v: self._set_setting("close_to_tray", v))
        self.fps = QSpinBox(suffix=" fps")
        self.fps.setRange(5, 60)
        self.fps.setValue(int(s.get("fps", 30)))
        self.fps.valueChanged.connect(lambda v: self._set_setting("fps", int(v)))
        self.exit_combo = QComboBox()
        for val, label in [("keep", "Leave the last colors on"), ("off", "Turn lights off"),
                           ("onboard", "Switch to the onboard effect")]:
            self.exit_combo.addItem(label, val)
        self.exit_combo.setCurrentIndex(max(0, self.exit_combo.findData(s.get("restore_on_exit", "keep"))))
        self.exit_combo.currentIndexChanged.connect(
            lambda _i: self._set_setting("restore_on_exit", self.exit_combo.currentData()))
        self.cb_demo = QCheckBox("Show a virtual demo device")
        self.cb_demo.setChecked(bool(s.get("demo_device")))
        self.cb_demo.toggled.connect(self._on_demo)
        form.addRow("Startup", self.cb_autostart)
        form.addRow("", self.cb_min)
        form.addRow("Window", self.cb_tray)
        form.addRow("Animation smoothness", self.fps)
        form.addRow("When FeynosRGB quits", self.exit_combo)
        form.addRow("Testing", self.cb_demo)
        lay.addLayout(form)
        tips = QLabel(
            "<b>Keyboard shortcuts:</b> bind these commands in your desktop's keyboard settings: "
            "<tt>feynosrgb next</tt>, <tt>feynosrgb toggle</tt>, "
            "<tt>feynosrgb apply \"Music\"</tt>.", wordWrap=True, objectName="hint")
        lay.addSpacing(10)
        lay.addWidget(tips)
        lay.addStretch(1)
        lay.addWidget(QLabel(f"{APP_NAME} {__version__} · GPL-3.0-or-later", objectName="hint"))
        return tab

    # ============================================================== profiles
    def _reload_profiles(self, select: str | None = None) -> None:
        self._loading = True
        self.profile_combo.clear()
        self.profile_combo.addItems(self.svc.store.names())
        name = select or self.svc.settings.get("active_profile") or ""
        idx = self.profile_combo.findText(name)
        self.profile_combo.setCurrentIndex(max(0, idx))
        self._loading = False

    def _on_profile_selected(self, name: str) -> None:
        if self._loading or not name:
            return
        self._flush_save()
        if self.svc.activate(name):
            self.profile = self.engine.profile
            self._load_profile_into_ui()

    def _on_external_profile(self, name: str) -> None:
        self.profile = self.engine.profile
        self._reload_profiles(name)
        self._load_profile_into_ui()

    def _ask_name(self, title: str, default: str) -> str | None:
        name, ok = QInputDialog.getText(self, title, "Profile name:", QLineEdit.Normal, default)
        name = (name or "").strip()
        if not ok or not name:
            return None
        if name in self.svc.store.names():
            QMessageBox.warning(self, title, f"A profile named “{name}” already exists.")
            return None
        return name

    def _new_profile(self) -> None:
        name = self._ask_name("New profile", "My profile")
        if name:
            self._flush_save()
            self.svc.store.save(Profile(name=name))
            self._reload_profiles(name)
            self._on_profile_selected(name)

    def _dup_profile(self) -> None:
        name = self._ask_name("Copy profile", f"{self.profile.name} copy")
        if name:
            self._flush_save()
            p = Profile.from_dict(self.profile.to_dict())
            p.name = name
            self.svc.store.save(p)
            self._reload_profiles(name)
            self._on_profile_selected(name)

    def _rename_profile(self) -> None:
        old = self.profile.name
        name = self._ask_name("Rename profile", old)
        if name:
            self._flush_save()
            self.svc.store.rename(old, name)
            self.svc.settings["active_profile"] = name
            self.svc.settings.save()
            self.profile.name = name
            self.engine.set_profile(self.profile)
            self._reload_profiles(name)

    def _delete_profile(self) -> None:
        names = self.svc.store.names()
        if len(names) <= 1:
            QMessageBox.information(self, "Delete profile", "You need at least one profile.")
            return
        name = self.profile.name
        if QMessageBox.question(self, "Delete profile", f"Delete “{name}”?") != QMessageBox.Yes:
            return
        self._save_timer.stop()
        self.svc.store.delete(name)
        nxt = self.svc.store.names()[0]
        self._reload_profiles(nxt)
        self._on_profile_selected(nxt)

    def _changed(self, restart: bool = False) -> None:
        """Push edits to the engine immediately; save to disk shortly after."""
        if self._loading:
            return
        self.engine.set_profile(self.profile, restart_time=restart)
        self._save_timer.start()

    def _save_profile(self) -> None:
        self.svc.store.save(self.profile)

    def _flush_save(self) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_profile()

    # ============================================================ ui <- data
    def _load_profile_into_ui(self) -> None:
        self._loading = True
        p = self.profile
        self.brightness.setValue(int(round(p.brightness * 100)))
        (self.mode_hw if p.mode == "onboard" else self.mode_sw).setChecked(True)
        self._populate_zone_list()
        self._load_zone_editor()
        self._populate_hw_modes()
        self._loading = False

    def _populate_zone_list(self) -> None:
        self.zone_list.blockSignals(True)
        self.zone_list.clear()
        all_item = QListWidgetItem("All zones")
        all_item.setData(Qt.UserRole, ALL_ZONES)
        self.zone_list.addItem(all_item)
        sel_row = 0
        for i, (key, name, count) in enumerate(self.engine.zone_keys(), start=1):
            label = name.split(" · ", 1)[-1]
            if key in self.profile.zones:
                label += "   ★ own effect"
            it = QListWidgetItem(f"{label}  ({count})")
            it.setData(Qt.UserRole, key)
            it.setToolTip(name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked if key in self.profile.disabled else Qt.Checked)
            self.zone_list.addItem(it)
            if key == self.selected_zone:
                sel_row = i
        if sel_row == 0:
            self.selected_zone = ALL_ZONES
        self.zone_list.setCurrentRow(sel_row)
        self.zone_list.blockSignals(False)

    def _current_spec(self) -> EffectSpec:
        if self.selected_zone != ALL_ZONES and self.selected_zone in self.profile.zones:
            return self.profile.zones[self.selected_zone]
        return self.profile.zones[ALL_ZONES]

    def _load_zone_editor(self) -> None:
        was = self._loading
        self._loading = True
        is_zone = self.selected_zone != ALL_ZONES
        has_own = is_zone and self.selected_zone in self.profile.zones
        self.override_cb.setVisible(is_zone)
        self.override_cb.setChecked(has_own)
        editable = (not is_zone) or has_own
        spec = self._current_spec()
        idx = self.effect_combo.findData(spec.effect)
        self.effect_combo.setCurrentIndex(idx)
        eff = get_effect(spec.effect)
        desc = eff.description
        if is_zone and not has_own:
            desc = "This zone follows the effect set for All zones. " + desc
        self.effect_desc.setText(desc)
        self.param_form.set_effect(eff, spec.params)
        self.effect_combo.setEnabled(editable)
        self.param_form.setEnabled(editable)
        self._loading = was

    def _populate_hw_modes(self) -> None:
        self.hw_mode_combo.blockSignals(True)
        self.hw_mode_combo.clear()
        modes = []
        for d in self.engine.devices():
            if d.hardware_modes():
                modes = d.hardware_modes()
                break
        for m in modes:
            self.hw_mode_combo.addItem(m.name, m.id)
        self.hw_mode_combo.setCurrentIndex(max(0, self.hw_mode_combo.findData(self.profile.onboard_mode)))
        self.hw_mode_combo.blockSignals(False)
        self.hw_color.set_color(self.profile.onboard_color)
        has = bool(modes)
        self.hw_save_btn.setEnabled(has and any(d.supports_save for d in self.engine.devices()))
        self.mode_hw.setEnabled(has)
        self._update_hw_color_enabled(modes)

    def _update_hw_color_enabled(self, modes=None) -> None:
        if modes is None:
            modes = next((d.hardware_modes() for d in self.engine.devices() if d.hardware_modes()), [])
        cur = self.hw_mode_combo.currentData()
        m = next((m for m in modes if m.id == cur), None)
        self.hw_color.setEnabled(bool(m and m.takes_color))

    # ============================================================ ui -> data
    def _on_brightness(self, v: int) -> None:
        self.profile.brightness = v / 100.0
        self._changed()

    def _on_power(self, on: bool) -> None:
        self.power_btn.setText("On" if on else "Off")
        if on != self.engine.power:
            self.engine.set_power(on)

    def _on_mode(self, _checked: bool) -> None:
        mode = "software" if self.mode_sw.isChecked() else "onboard"
        if mode != self.profile.mode:
            self.profile.mode = mode
            self._changed()

    def _on_zone_selected(self, cur: QListWidgetItem | None, _prev) -> None:
        self.selected_zone = cur.data(Qt.UserRole) if cur else ALL_ZONES
        self._load_zone_editor()

    def _on_zone_checked(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.UserRole)
        if key == ALL_ZONES:
            return
        off = item.checkState() == Qt.Unchecked
        dis = set(self.profile.disabled)
        dis.add(key) if off else dis.discard(key)
        self.profile.disabled = sorted(dis)
        self._changed()

    def _on_override(self, on: bool) -> None:
        if self._loading or self.selected_zone == ALL_ZONES:
            return
        if on:
            base = self.profile.zones[ALL_ZONES]
            self.profile.zones[self.selected_zone] = EffectSpec(base.effect, dict(base.params))
        else:
            self.profile.zones.pop(self.selected_zone, None)
        self._changed()
        self._populate_zone_list()
        self._load_zone_editor()

    def _on_effect_selected(self, _i: int) -> None:
        if self._loading:
            return
        eid = self.effect_combo.currentData()
        if not eid:
            return
        key = self.selected_zone if self.selected_zone in self.profile.zones else ALL_ZONES
        old = self.profile.zones[key]
        # Keep shared settings (colors, speed) when switching effects.
        eff = get_effect(eid)
        self.profile.zones[key] = EffectSpec(eid, eff.normalize(old.params))
        if self.profile.mode != "software":
            self.mode_sw.setChecked(True)
        self._changed(restart=True)
        self._load_zone_editor()

    def _on_params(self, values: dict) -> None:
        if self._loading:
            return
        key = self.selected_zone if self.selected_zone in self.profile.zones else ALL_ZONES
        spec = self.profile.zones[key]
        self.profile.zones[key] = EffectSpec(spec.effect, values)
        self._changed()

    def _on_hw_changed(self, *_a) -> None:
        if self._loading:
            return
        if self.hw_mode_combo.currentData():
            self.profile.onboard_mode = self.hw_mode_combo.currentData()
        self.profile.onboard_color = self.hw_color.color()
        self._update_hw_color_enabled()
        self._changed()

    def _save_onboard(self) -> None:
        self._on_hw_changed()
        errors = self.engine.save_onboard_to_devices()
        if errors:
            QMessageBox.warning(self, "Save to motherboard", "\n".join(errors))
        else:
            self.status.showMessage("Onboard effect saved to the motherboard.", 5000)
        if self.profile.mode == "software":
            self.status.showMessage(
                "Saved. Live effects are still running; switch to Onboard effect to see it now.", 7000)

    def _resize_zone(self, uid: str, zid: str, count: int) -> None:
        self.engine.resize_zone(uid, zid, count)
        self._populate_zone_list()
        self.preview.updateGeometry()

    def _rescan(self) -> None:
        self.engine.scan()
        self._load_profile_into_ui()

    def _set_setting(self, key, value) -> None:
        self.svc.settings[key] = value
        self.svc.settings.save()

    def _on_autostart(self, on: bool) -> None:
        autostart.set_enabled(on)
        self._set_setting("autostart", on)

    def _on_demo(self, on: bool) -> None:
        self._set_setting("demo_device", on)
        self._rescan()

    # ================================================================ status
    def _refresh_status(self) -> None:
        self._populate_devices()
        self._populate_zone_list()
        self._populate_hw_modes()
        self.power_btn.blockSignals(True)
        self.power_btn.setChecked(self.engine.power)
        self.power_btn.setText("On" if self.engine.power else "Off")
        self.power_btn.blockSignals(False)
        self._refresh_statusbar()

    def _refresh_statusbar(self) -> None:
        st = self.engine.status()
        ok = sum(1 for s in st.devices if s.ok)
        parts = [f"{ok} device(s)"]
        if not self.engine.power:
            parts.append("lights off")
        elif self.profile.mode == "onboard":
            parts.append("onboard effect")
        elif st.fps:
            parts.append(f"{st.fps:.0f} fps")
        if st.audio_error:
            parts.append(st.audio_error)
        elif st.audio_backend:
            parts.append(f"audio via {st.audio_backend}")
        bad = [s for s in st.devices if not s.ok]
        if bad:
            parts.append(f"⚠ {bad[0].device.name}: {bad[0].error}")
        self.status.showMessage("   ·   ".join(parts))

    # ================================================================ window
    def bring_to_front(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e: QCloseEvent) -> None:
        self._flush_save()
        if not self.quitting and self.svc.settings.get("close_to_tray", True) and getattr(self, "tray_available", False):
            e.ignore()
            self.hide()
            return
        e.accept()
