# FeynosRGB

Standalone RGB lighting control for Fedora and other RPM-based Linux distros.
FeynosRGB talks to your lighting controller directly over USB: no vendor
software, no OpenRGB, no root.

## Supported hardware

| Device | Status |
| --- | --- |
| ASUS Aura USB motherboards (ROG Strix **B550-F Gaming**, other X570/B550/Z490+ boards with USB IDs `0b05:18f3`, `1939`, `19af`, `1aa6`, `1bed`) | Onboard LEDs, 12V RGB headers, ARGB headers, onboard effects, save to motherboard |

The driver layer is built so more devices (keyboards, mice, RAM, GPUs, fan
hubs) can be added; see [Adding a device](#adding-a-device).

## Features

- **Live effects**: static, breathing, spectrum cycle, rainbow wave, gradient,
  comet, twinkle, fire, strobe
- **Audio reactive**: spectrum analyzer, beat pulse and VU meter, synced to
  whatever is playing (PipeWire or PulseAudio)
- **System sync**: colors follow CPU or GPU temperature (AMD, Intel, NVIDIA)
- **Per-zone effects**: give each ARGB header its own effect, or switch zones off
- **Onboard effects** that run on the motherboard itself, and **Save to
  motherboard** so they persist through reboots, in the BIOS and at shutdown
- **Profiles**, brightness, power toggle
- **Tray icon** and **start at login**
- **Command line** for keyboard shortcuts: `feynosrgb next`, `feynosrgb toggle`,
  `feynosrgb apply "Music"`

## Install

Download the latest `feynosrgb-*.noarch.rpm` from
[Releases](../../releases/latest), then:

```bash
sudo dnf install ./feynosrgb-*.noarch.rpm
```

Open **FeynosRGB** from your app menu. The package installs a udev rule that
gives your login session access to the controller; it is applied on install,
but if the device shows a permission error, run `sudo udevadm trigger` or reboot.

**First run:** open the **Devices** tab and set each ARGB header to the number
of LEDs connected to it (0 if nothing is plugged in).

GNOME shows tray icons only with the AppIndicator extension:
`sudo dnf install gnome-shell-extension-appindicator` (then log out and in).
Without it, FeynosRGB still works; closing the window quits it.

Don't run OpenRGB or Armoury Crate-style tools at the same time, since two
programs writing to the controller will fight.

## Command line

```
feynosrgb                      open the app (or show the running one)
feynosrgb apply "Profile name" switch profile
feynosrgb next | prev          cycle profiles
feynosrgb on | off | toggle    lights on/off
feynosrgb brightness 0.5       brightness 0-1
feynosrgb status               devices, zones and active profile
feynosrgb daemon               run without a window
feynosrgb --demo               add a virtual device (no hardware needed)
```

Headless instead of the tray app: `systemctl --user enable --now feynosrgb`.

Settings and profiles live in `~/.config/feynosrgb/`.

## Build the RPM yourself

```bash
sudo dnf install rpm-build python3-devel python3-rpm-macros systemd-rpm-macros desktop-file-utils git
VERSION=$(rpmspec -q --srpm --qf '%{version}' packaging/feynosrgb.spec)
mkdir -p ~/rpmbuild/SOURCES
git archive --prefix=feynosrgb-$VERSION/ -o ~/rpmbuild/SOURCES/feynosrgb-$VERSION.tar.gz HEAD
rpmbuild -ba packaging/feynosrgb.spec
```

Every push to `main` is tested, built in a Fedora container, install-tested
on several Fedora releases, and published to Releases by
`.github/workflows/build-rpm.yml`. Bump `Version:` in the spec to cut a new
release.

Run from source: `python3 -m feynosrgb --demo` (needs `python3-pyside6` and
`python3-numpy`). Tests: `python3 -m pytest tests`.

## Adding a device

1. Create `feynosrgb/devices/<vendor>_<thing>.py` with a class derived from
   `RGBDevice` (`devices/base.py`): describe its zones, implement
   `open`, `close`, `enter_direct_mode` and `set_zone_colors`, and optionally
   `hardware_modes`/`set_hardware_mode`/`save_to_device`.
2. Add a `detect(settings)` function and append it to `DETECTORS` in
   `devices/__init__.py`, plus its USB IDs to `SUPPORTED_USB_IDS`.
3. Add a matching line to `data/60-feynosrgb.rules` (a test checks this).

`hidraw.py` handles USB HID devices without extra dependencies.

## Credits and license

The ASUS Aura USB protocol is documented by the
[OpenRGB](https://gitlab.com/CalcProgrammer1/OpenRGB) project and
[liquidctl](https://github.com/liquidctl/liquidctl); FeynosRGB's driver is an
independent Python implementation based on that work.

GPL-3.0-or-later.
