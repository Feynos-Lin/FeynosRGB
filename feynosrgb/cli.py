"""Command line entry point: `feynosrgb [command]`."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading

from . import APP_NAME, __version__, ipc

EPILOG = """commands (talk to the running FeynosRGB, or start one):
  feynosrgb                      open the app (or show the running one)
  feynosrgb --minimized          start in the tray (used by autostart)
  feynosrgb apply "Profile name" switch profile
  feynosrgb next | prev          cycle profiles (bind to a keyboard shortcut)
  feynosrgb on | off | toggle    lights on/off
  feynosrgb brightness 0.5       set brightness (0-1)
  feynosrgb status               show devices and the active profile
  feynosrgb profiles             list profiles
  feynosrgb daemon               run without a window (e.g. a systemd user service)
"""


def _print_status(s: dict) -> None:
    print(f"Power: {'on' if s.get('power') else 'off'}   Profile: {s.get('profile')}   FPS: {s.get('fps')}")
    devs = s.get("devices") or []
    if not devs:
        print("No supported devices found.")
    for d in devs:
        state = "OK" if d["ok"] else f"ERROR: {d['error']}"
        print(f"\n{d['name']}  [{state}]")
        print(f"  firmware {d.get('firmware') or '?'}, {d.get('location')}")
        for z in d["zones"]:
            print(f"  - {z['name']}: {z['leds']} LEDs  (id {d['uid']}/{z['id']})")
    for e in s.get("detect_errors") or []:
        print(f"Detection error: {e}")
    if s.get("audio_error"):
        print(f"Audio: {s['audio_error']}")


def _run_daemon(demo: bool, profile: str | None) -> int:
    from .service import Service
    svc = Service(demo=demo)
    server = ipc.Server(svc.handle)
    if not server.start():
        print(f"{APP_NAME} is already running.", file=sys.stderr)
        return 1
    svc.start()
    if profile:
        if not svc.activate(profile):
            print(f"No profile named {profile!r}", file=sys.stderr)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    print(f"{APP_NAME} daemon running (profile: {svc.settings.get('active_profile')}). Ctrl+C to stop.")
    stop.wait()
    server.stop()
    svc.shutdown()
    return 0


def _oneshot_status(demo: bool) -> int:
    """No instance running: detect devices directly and report."""
    from .service import Service
    svc = Service(demo=demo)
    svc.engine.scan()
    _print_status(svc.handle({"cmd": "status"}))
    svc.engine.stop(restore="keep")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="feynosrgb", description=f"{APP_NAME} {__version__} — RGB lighting control",
                                 epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="gui",
                    choices=["gui", "apply", "next", "prev", "on", "off", "toggle", "brightness",
                             "status", "profiles", "daemon", "rescan"])
    ap.add_argument("arg", nargs="?", help="profile name or brightness value")
    ap.add_argument("--minimized", action="store_true", help="start hidden in the system tray")
    ap.add_argument("--demo", action="store_true", help="add a virtual device (no hardware needed)")
    ap.add_argument("--json", action="store_true", help="machine-readable output for status")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if a.command == "gui":
        if ipc.send({"cmd": "show"}) is not None:
            return 0
        from .ui.app import run_gui
        return run_gui(minimized=a.minimized, demo=a.demo)

    if a.command == "daemon":
        return _run_daemon(a.demo, a.arg)

    cmd: dict = {"cmd": a.command}
    if a.command == "apply":
        if not a.arg:
            ap.error("apply needs a profile name")
        cmd["profile"] = a.arg
    if a.command == "brightness":
        try:
            cmd["value"] = float(a.arg)
        except (TypeError, ValueError):
            ap.error("brightness needs a number between 0 and 1")
    if a.command == "profiles":
        cmd = {"cmd": "status"}

    reply = ipc.send(cmd)
    if reply is None:
        if a.command == "status":
            return _oneshot_status(a.demo)
        if a.command == "profiles":
            from .config import ProfileStore
            print("\n".join(ProfileStore().names()))
            return 0
        print(f"{APP_NAME} is not running. Start it with 'feynosrgb' or 'feynosrgb daemon'.",
              file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(reply, indent=2))
    elif a.command == "status":
        _print_status(reply)
    elif a.command == "profiles":
        cur = reply.get("profile")
        for n in reply.get("profiles", []):
            print(("* " if n == cur else "  ") + n)
    elif not reply.get("ok"):
        print(reply.get("error") or "failed", file=sys.stderr)
        return 1
    elif "profile" in reply:
        print(reply["profile"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
