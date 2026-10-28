"""Capture what the computer is playing (PipeWire/PulseAudio monitor) and
turn it into frequency bands for the audio effects."""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
import threading
import time
from collections import deque

from .effects import AudioFrame

log = logging.getLogger(__name__)

try:
    import numpy as np
except ImportError:  # audio effects degrade to volume-only
    np = None

RATE = 44100
CHUNK = 1024             # samples per analysis frame (~23 ms)
NUM_BANDS = 16
F_MIN, F_MAX = 40.0, 16000.0

CAPTURE_COMMANDS = [
    # pipewire-pulse / pulseaudio: default output's monitor, raw PCM on stdout
    ["parec", "--device=@DEFAULT_MONITOR@", "--format=s16le", f"--rate={RATE}",
     "--channels=1", "--raw", "--latency-msec=20"],
    # native PipeWire tool; capture the default sink
    ["pw-record", "--raw", f"--rate={RATE}", "--channels=1", "--format=s16",
     "-P", "{ stream.capture.sink=true }", "-"],
]


def _band_edges() -> list[tuple[int, int]]:
    freqs_per_bin = RATE / CHUNK
    edges = []
    for i in range(NUM_BANDS):
        lo = F_MIN * (F_MAX / F_MIN) ** (i / NUM_BANDS)
        hi = F_MIN * (F_MAX / F_MIN) ** ((i + 1) / NUM_BANDS)
        a = max(1, int(lo / freqs_per_bin))
        b = max(a + 1, int(hi / freqs_per_bin))
        edges.append((a, b))
    return edges


class Analyzer:
    """Pure analysis, separated from capture so it can be tested."""

    def __init__(self) -> None:
        self.edges = _band_edges()
        self.band_peaks = [1e-3] * NUM_BANDS
        self.level_peak = 1e-3
        self.bass_hist: deque[float] = deque(maxlen=43)  # ~1 s
        self.last_beat = 0.0
        self.window = np.hanning(CHUNK) if np is not None else None

    def process(self, pcm: bytes, now: float | None = None) -> AudioFrame:
        now = time.monotonic() if now is None else now
        if np is None:
            return self._process_plain(pcm)
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        if samples.size < CHUNK:
            samples = np.pad(samples, (0, CHUNK - samples.size))
        samples = samples[:CHUNK]
        rms = float(np.sqrt(np.mean(samples * samples)))
        spec = np.abs(np.fft.rfft(samples * self.window))
        raw = [float(spec[a:b].mean()) if b <= spec.size else 0.0 for a, b in self.edges]
        # Compress dynamics so quiet passages still move the lights.
        raw = [math.sqrt(v) for v in raw]
        bands = []
        for i, v in enumerate(raw):
            self.band_peaks[i] = max(v, self.band_peaks[i] * 0.995, 0.02)
            bands.append(min(1.0, v / self.band_peaks[i]))
        self.level_peak = max(rms, self.level_peak * 0.997, 0.01)
        level = min(1.0, rms / self.level_peak) if rms > 0.0005 else 0.0
        bass_raw = sum(raw[:3]) / 3
        bass = sum(bands[:3]) / 3 if rms > 0.0005 else 0.0
        beat = False
        if len(self.bass_hist) > 10:
            avg = sum(self.bass_hist) / len(self.bass_hist)
            if bass_raw > avg * 1.45 and bass_raw > 0.05 and now - self.last_beat > 0.15:
                beat = True
                self.last_beat = now
        self.bass_hist.append(bass_raw)
        if rms <= 0.0005:
            bands = [0.0] * NUM_BANDS
        return AudioFrame(bands=bands, level=level, bass=bass, beat=beat)

    def _process_plain(self, pcm: bytes) -> AudioFrame:
        n = len(pcm) // 2
        if n == 0:
            return AudioFrame([0.0] * NUM_BANDS, 0.0, 0.0)
        total = 0
        for i in range(0, n * 2, 2):
            s = int.from_bytes(pcm[i:i + 2], "little", signed=True)
            total += s * s
        rms = math.sqrt(total / n) / 32768.0
        self.level_peak = max(rms, self.level_peak * 0.997, 0.01)
        level = min(1.0, rms / self.level_peak)
        return AudioFrame([level] * NUM_BANDS, level, level)


class AudioCapture:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._frame = AudioFrame([0.0] * NUM_BANDS, 0.0, 0.0)
        self._beat_pending = False
        self.error: str | None = None
        self.backend: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="audio-capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None

    def snapshot(self) -> AudioFrame:
        with self._lock:
            f = self._frame
            beat = self._beat_pending
            self._beat_pending = False
        return AudioFrame(f.bands, f.level, f.bass, beat)

    def _run(self) -> None:
        analyzer = Analyzer()
        while not self._stop.is_set():
            started = False
            for cmd in CAPTURE_COMMANDS:
                if self._stop.is_set():
                    return
                if not shutil.which(cmd[0]):
                    continue
                try:
                    self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                                  stderr=subprocess.DEVNULL, bufsize=0)
                except OSError as e:
                    log.warning("audio: %s failed: %s", cmd[0], e)
                    continue
                self.backend = cmd[0]
                t0 = time.monotonic()
                got_data = self._pump(analyzer)
                started = started or got_data
                if self._stop.is_set():
                    return
                if got_data or time.monotonic() - t0 > 3:
                    break  # it worked for a while; restart the same backend
            if not started:
                self.error = ("Could not capture audio. Install 'pulseaudio-utils' "
                              "(parec) or 'pipewire-utils' (pw-record).")
                log.warning(self.error)
            self._stop.wait(2.0)

    def _pump(self, analyzer: Analyzer) -> bool:
        proc = self._proc
        assert proc and proc.stdout
        nbytes = CHUNK * 2
        got = False
        buf = b""
        while not self._stop.is_set():
            data = proc.stdout.read(nbytes - len(buf))
            if not data:
                break
            buf += data
            if len(buf) < nbytes:
                continue
            got = True
            self.error = None
            frame = analyzer.process(buf)
            buf = b""
            with self._lock:
                self._frame = frame
                if frame.beat:
                    self._beat_pending = True
        if proc.poll() is None:
            proc.terminate()
        return got
