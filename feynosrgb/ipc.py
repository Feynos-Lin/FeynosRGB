"""Tiny line-based JSON IPC over a Unix socket, so `feynosrgb apply X` (or a
keyboard shortcut) can control the instance that is already running."""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/feynosrgb-{os.getuid()}"
    Path(runtime).mkdir(parents=True, exist_ok=True, mode=0o700)
    return Path(runtime) / "feynosrgb.sock"


def send(command: dict, timeout: float = 2.0) -> dict | None:
    """Send a command to the running instance. Returns None if none is running."""
    path = socket_path()
    if not path.exists():
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall(json.dumps(command).encode() + b"\n")
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        return json.loads(data or b"{}")
    except (OSError, ValueError):
        return None


class Server:
    def __init__(self, handler: Callable[[dict], dict]):
        self.handler = handler
        self.path = socket_path()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Returns False if another instance already owns the socket."""
        if self.path.exists():
            if send({"cmd": "ping"}, timeout=0.5) is not None:
                return False
            try:
                self.path.unlink()
            except OSError:
                pass
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self._sock.listen(4)
        self._thread = threading.Thread(target=self._serve, name="ipc", daemon=True)
        self._thread.start()
        return True

    def _serve(self) -> None:
        assert self._sock
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(2)
                    data = b""
                    while not data.endswith(b"\n") and len(data) < 65536:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    reply = self.handler(json.loads(data or b"{}"))
                except Exception as e:  # never kill the server thread
                    reply = {"ok": False, "error": str(e)}
                try:
                    conn.sendall(json.dumps(reply).encode() + b"\n")
                except OSError:
                    pass

    def stop(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            finally:
                self._sock = None
        try:
            self.path.unlink()
        except OSError:
            pass
