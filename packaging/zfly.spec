%global debug_package %{nil}

Name:           zfly
Version:        0.2.0
Release:        1
Summary:        Standalone GTK proxy and subscription manager
License:        GPL-3.0-only AND MIT
BuildArch:      x86_64
Requires:       python3 >= 3.11
Requires:       python3-gobject >= 3.42
Requires:       gtk4
Requires:       libadwaita >= 1.4
Requires:       gdk-pixbuf2
Requires:       glib2
Requires:       gsettings-desktop-schemas
Requires:       adwaita-icon-theme
Requires:       ca-certificates

%description
ZFly manages profiles, subscriptions, proxy cores, persistent settings, and
system proxy settings. It includes a private mihomo core and a native GTK 4
and libadwaita interface.

%prep
# Sources are staged by tools/build-distro-packages.py.

%install
rm -rf "%{buildroot}"
mkdir -p "%{buildroot}"
cp -a "%{zfly_stage}/usr" "%{buildroot}/usr"

%files
%defattr(-,root,root,-)
/usr/bin/zfly
/usr/lib/zfly/mihomo
/usr/share/zfly
/usr/share/applications/io.github.zfly.ZFly.desktop
/usr/share/icons/hicolor
%license /usr/share/doc/zfly/LICENSE
%license /usr/share/doc/zfly/PyYAML-LICENSE
%license /usr/share/doc/zfly/copyright

%changelog
* Sat Oct 24 2026 ZFly contributors <zfly@users.noreply.github.com> - 0.2.0-1
- Build the Linux distribution package.
