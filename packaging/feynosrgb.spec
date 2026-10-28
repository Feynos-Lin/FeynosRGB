Name:           feynosrgb
Version:        1.0.0
Release:        1%{?dist}
Summary:        Standalone RGB lighting control with effects, music sync and temperature colors

License:        GPL-3.0-or-later
URL:            https://github.com/Feynos-Lin/FeynosRGB
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  systemd-rpm-macros
BuildRequires:  desktop-file-utils

Requires:       python3 >= 3.9
# not python3dist(): the package lives outside site-packages on purpose
Requires:       python3-pyside6
Requires:       python3-numpy
Requires:       hicolor-icon-theme
# audio-reactive effects capture the default output through one of these
Recommends:     pulseaudio-utils
Suggests:       pipewire-utils

%description
FeynosRGB controls RGB lighting directly through the hardware's own USB
protocol: no vendor software, no background services and no root needed.

Supported now: ASUS Aura USB motherboards (onboard LEDs, 12V RGB headers
and ARGB headers), including the ROG Strix B550-F Gaming. Features live
effects, audio-reactive modes, CPU/GPU temperature colors, onboard effects
saved to the motherboard, profiles, a tray icon, start at login and a
command line for keyboard shortcuts.

%prep
%autosetup -n %{name}-%{version}

%build
# pure Python, nothing to build

%install
# Installed to a private, Python-version-independent directory so the same
# noarch RPM works across Fedora releases with different Python versions.
install -d %{buildroot}%{_datadir}/feynosrgb
cp -a feynosrgb %{buildroot}%{_datadir}/feynosrgb/
find %{buildroot}%{_datadir}/feynosrgb -name '__pycache__' -prune -exec rm -rf {} +
%py_byte_compile %{python3} %{buildroot}%{_datadir}/feynosrgb/feynosrgb

install -Dm0755 packaging/feynosrgb-launcher %{buildroot}%{_bindir}/feynosrgb
install -Dm0644 data/feynosrgb.desktop %{buildroot}%{_datadir}/applications/feynosrgb.desktop
install -Dm0644 data/feynosrgb.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/feynosrgb.svg
install -Dm0644 data/io.github.feynos_lin.FeynosRGB.metainfo.xml %{buildroot}%{_metainfodir}/io.github.feynos_lin.FeynosRGB.metainfo.xml
install -Dm0644 data/60-feynosrgb.rules %{buildroot}%{_udevrulesdir}/60-feynosrgb.rules
install -Dm0644 data/feynosrgb.service %{buildroot}%{_userunitdir}/feynosrgb.service

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/feynosrgb.desktop
PYTHONPATH=%{buildroot}%{_datadir}/feynosrgb %{python3} -c "import feynosrgb.engine, feynosrgb.cli"

%post
# Apply the device permissions now so a reboot isn't needed.
udevadm control --reload-rules >/dev/null 2>&1 || :
udevadm trigger --subsystem-match=hidraw --action=change >/dev/null 2>&1 || :

%postun
udevadm control --reload-rules >/dev/null 2>&1 || :

%files
%license LICENSE
%doc README.md
%{_datadir}/feynosrgb/
%{_bindir}/feynosrgb
%{_datadir}/applications/feynosrgb.desktop
%{_datadir}/icons/hicolor/scalable/apps/feynosrgb.svg
%{_metainfodir}/io.github.feynos_lin.FeynosRGB.metainfo.xml
%{_udevrulesdir}/60-feynosrgb.rules
%{_userunitdir}/feynosrgb.service

%changelog
* Wed Oct 28 2026 Feynos-Lin <Feynos-Lin@users.noreply.github.com> - 1.0.0-1
- First release: ASUS Aura USB mainboards, live/audio/temperature effects,
  onboard effects, profiles, tray, autostart and CLI
