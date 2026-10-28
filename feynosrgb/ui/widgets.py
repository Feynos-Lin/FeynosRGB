"""Reusable Qt widgets: color pickers, the effect parameter form and the live LED preview."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox,
                               QFormLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QSlider, QSpinBox, QToolButton, QWidget)

from ..color import parse_hex, to_hex
from ..effects import Effect, Param


class ColorButton(QPushButton):
    changed = Signal(str)

    def __init__(self, color: str = "#ffffff", parent=None):
        super().__init__(parent)
        self.setFixedSize(QSize(46, 28))
        self.setCursor(Qt.PointingHandCursor)
        self._color = to_hex(parse_hex(color))
        self.clicked.connect(self._pick)
        self._refresh()

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        self._color = to_hex(parse_hex(color))
        self._refresh()

    def _refresh(self) -> None:
        self.setToolTip(self._color)
        self.setStyleSheet(
            f"QPushButton {{ background: {self._color}; border: 2px solid rgba(255,255,255,40);"
            f" border-radius: 6px; }} QPushButton:hover {{ border-color: rgba(255,255,255,140); }}")

    def _pick(self) -> None:
        dlg = QColorDialog(QColor(self._color), self)
        dlg.setOption(QColorDialog.DontUseNativeDialog, True)
        # Live preview on the hardware while the dialog is open.
        original = self._color
        dlg.currentColorChanged.connect(lambda c: self._apply(c.name()))
        if dlg.exec():
            self._apply(dlg.selectedColor().name())
        else:
            self._apply(original)

    def _apply(self, hex_color: str) -> None:
        if hex_color != self._color:
            self._color = hex_color
            self._refresh()
            self.changed.emit(hex_color)


class ColorListEditor(QWidget):
    changed = Signal(list)

    def __init__(self, colors: list[str], parent=None, max_colors: int = 8):
        super().__init__(parent)
        self.max_colors = max_colors
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._buttons: list[ColorButton] = []
        self._add = QToolButton(text="+")
        self._add.setToolTip("Add a color")
        self._add.clicked.connect(self._add_color)
        self._remove = QToolButton(text="−")
        self._remove.setToolTip("Remove the last color")
        self._remove.clicked.connect(self._remove_color)
        self.set_colors(colors)

    def colors(self) -> list[str]:
        return [b.color() for b in self._buttons]

    def set_colors(self, colors: list[str]) -> None:
        for b in self._buttons:
            b.deleteLater()
        self._buttons = []
        while self._layout.count():
            self._layout.takeAt(0)
        for c in colors or ["#ffffff"]:
            self._append(c)
        self._layout.addWidget(self._add)
        self._layout.addWidget(self._remove)
        self._layout.addStretch(1)
        self._update_buttons()

    def _append(self, c: str) -> None:
        b = ColorButton(c)
        b.changed.connect(lambda _c: self.changed.emit(self.colors()))
        self._buttons.append(b)
        self._layout.insertWidget(len(self._buttons) - 1, b)

    def _update_buttons(self) -> None:
        self._add.setEnabled(len(self._buttons) < self.max_colors)
        self._remove.setEnabled(len(self._buttons) > 1)

    def _add_color(self) -> None:
        last = self._buttons[-1].color() if self._buttons else "#ffffff"
        self._append(last)
        self._update_buttons()
        self.changed.emit(self.colors())

    def _remove_color(self) -> None:
        if len(self._buttons) > 1:
            b = self._buttons.pop()
            b.deleteLater()
            self._update_buttons()
            self.changed.emit(self.colors())


class FloatSlider(QWidget):
    changed = Signal(float)

    def __init__(self, p: Param, value: float, parent=None):
        super().__init__(parent)
        self.p = p
        self.steps = max(1, int(round((p.max - p.min) / (p.step or 0.01))))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, self.steps)
        self.label = QLabel()
        self.label.setMinimumWidth(64)
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.label)
        self.set_value(value)
        self.slider.valueChanged.connect(self._moved)

    def _to_value(self, pos: int) -> float:
        return self.p.min + (self.p.max - self.p.min) * pos / self.steps

    def set_value(self, v: float) -> None:
        pos = int(round((v - self.p.min) / (self.p.max - self.p.min) * self.steps)) if self.p.max > self.p.min else 0
        self.slider.blockSignals(True)
        self.slider.setValue(pos)
        self.slider.blockSignals(False)
        self._label(v)

    def _label(self, v: float) -> None:
        if self.p.suffix.strip() == "°C":
            txt = f"{v:.0f}{self.p.suffix}"
        elif self.p.max <= 1.0 and not self.p.suffix:
            txt = f"{v * 100:.0f}%"
        else:
            txt = f"{v:.2f}".rstrip("0").rstrip(".") + self.p.suffix
        self.label.setText(txt)

    def _moved(self, pos: int) -> None:
        v = round(self._to_value(pos), 4)
        self._label(v)
        self.changed.emit(v)


class ParamForm(QWidget):
    """Builds editors for an effect's parameters."""
    changed = Signal(dict)

    def __init__(self, sensor_source: Callable[[], dict[str, str]] | None = None, parent=None):
        super().__init__(parent)
        self.sensor_source = sensor_source or (lambda: {})
        self._form = QFormLayout(self)
        self._form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._form.setHorizontalSpacing(16)
        self._form.setVerticalSpacing(10)
        self._values: dict = {}
        self._effect: Effect | None = None

    def set_effect(self, effect: Effect, values: dict) -> None:
        self._effect = effect
        self._values = effect.normalize(values)
        while self._form.rowCount():
            self._form.removeRow(0)
        if not effect.params:
            self._form.addRow(QLabel("This effect has no settings."))
        for p in effect.params:
            self._form.addRow(p.label, self._editor(p, self._values[p.id]))

    def values(self) -> dict:
        return dict(self._values)

    def _set(self, pid: str, v) -> None:
        self._values[pid] = v
        self.changed.emit(dict(self._values))

    def _editor(self, p: Param, v) -> QWidget:
        if p.kind == "color":
            w = ColorButton(v)
            w.changed.connect(lambda c, pid=p.id: self._set(pid, c))
            return w
        if p.kind == "colors":
            w = ColorListEditor(v)
            w.changed.connect(lambda cs, pid=p.id: self._set(pid, cs))
            return w
        if p.kind == "float":
            w = FloatSlider(p, float(v))
            w.changed.connect(lambda x, pid=p.id: self._set(pid, x))
            return w
        if p.kind == "int":
            w = QSpinBox()
            w.setRange(int(p.min), int(p.max))
            w.setValue(int(v))
            w.valueChanged.connect(lambda x, pid=p.id: self._set(pid, int(x)))
            return w
        if p.kind == "bool":
            w = QCheckBox()
            w.setChecked(bool(v))
            w.toggled.connect(lambda x, pid=p.id: self._set(pid, bool(x)))
            return w
        if p.kind in ("choice", "sensor"):
            w = QComboBox()
            items = p.choices if p.kind == "choice" else list(self.sensor_source().items())
            for val, label in items:
                w.addItem(label, val)
            idx = w.findData(v)
            if idx < 0 and p.kind == "sensor":
                w.addItem(f"{v} (not found)", v)
                idx = w.count() - 1
            w.setCurrentIndex(max(0, idx))
            w.currentIndexChanged.connect(lambda _i, pid=p.id, cb=w: self._set(pid, cb.currentData()))
            return w
        return QLabel(str(v))


class LedPreview(QWidget):
    """Draws the colors the engine is currently sending, one row per zone."""

    def __init__(self, frame_source: Callable[[], dict], zones_source: Callable[[], list], parent=None):
        super().__init__(parent)
        self.frame_source = frame_source
        self.zones_source = zones_source
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(33)
        self._rows = 1

    def sizeHint(self) -> QSize:
        return QSize(600, 24 + 30 * max(1, len(self.zones_source())))

    def minimumSizeHint(self) -> QSize:
        return QSize(300, 24 + 30 * max(1, len(self.zones_source())))

    def showEvent(self, e):
        self._timer.start(33)
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def paintEvent(self, _e):
        zones = self.zones_source()
        if len(zones) != self._rows:
            self._rows = len(zones)
            self.updateGeometry()
        frame = self.frame_source() or {}
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        label_w = min(230, int(self.width() * 0.35))
        y = 8
        fm = p.fontMetrics()
        if not zones:
            p.setPen(self.palette().placeholderText().color())
            p.drawText(self.rect(), Qt.AlignCenter, "No devices yet — see the Devices tab")
            return
        for key, name, count in zones:
            p.setPen(self.palette().text().color())
            short = name.split(" · ", 1)[-1]
            p.drawText(QRectF(4, y, label_w - 8, 22), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(short, Qt.ElideRight, label_w - 8))
            colors = frame.get(key) or []
            n = max(1, count)
            avail = self.width() - label_w - 8
            d = max(3.0, min(16.0, avail / n - 2))
            gap = (avail - n * d) / max(1, n) if n > 1 else 0
            gap = min(gap, 6.0)
            x = label_w
            for i in range(count):
                c = QColor(*colors[i]) if i < len(colors) else QColor(30, 30, 36)
                if c.red() + c.green() + c.blue() < 18:
                    c = QColor(30, 30, 36)
                p.setPen(QPen(QColor(255, 255, 255, 25), 1))
                p.setBrush(c)
                p.drawEllipse(QRectF(x, y + 11 - d / 2, d, d))
                x += d + gap
            y += 30
        p.end()
