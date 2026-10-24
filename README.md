**English** | [简体中文](README.zh-CN.md)

# ZFly

ZFly is a standalone native proxy client. It manages subscriptions, configurations, proxy cores, persistent settings, and the system proxy itself. FlClash does not need to be installed or running, and no management bridge address or controller secret is required.

The macOS app supports Apple Silicon (arm64) only, uses SwiftUI and system materials, and has no Intel build. Linux uses GTK 4 / libadwaita. Proxy protocols are provided by the app's bundled FlClash Go core or mihomo.

<img src="assets/icons/zfly-256.png" width="160" height="160" alt="ZFly app icon">

## Usage

1. In **Subscriptions** on macOS or **Profiles & Subscriptions** on Linux, add a Clash-format subscription, import YAML, or create a custom configuration.
2. Select a configuration and click **Start Proxy**. On the proxy page, select nodes, pin nodes for automatic groups, and run latency tests.
3. Enable **System Proxy** as needed so applications that follow system proxy settings use ZFly. The default mixed port is `127.0.0.1:7890`.
4. When the proxy stops or ZFly quits, ZFly restores system proxy settings that it still owns.

On macOS, `--safe-mode` starts an isolated demo runtime with a temporary data directory and a mock core. Data is deleted on exit; no real core starts, no external network is accessed, and the system proxy is not changed. Linux still uses the original demo adapter. See the [usage guide](docs/usage.md) for full details and feature scope.

## Implemented Features and Limitations

- Automatically starts its own private local service and core; the full macOS app includes Python, the core, Geo data, and licenses.
- Subscription downloads, scheduled updates, ETag, usage and expiration information; local YAML viewing, editing, and export; custom nodes and proxy groups.
- Rule, Global, and Direct modes; Selector selection; pinning and restoring automatic selection for URLTest / Fallback; cancellable latency tests for individual nodes and entire groups; connection viewing and closing.
- macOS dashboard, traffic trends, menu bar, themes, and transparent sidebar materials; Chinese and English on both macOS and GTK, although runtime messages may still contain Chinese.
- macOS login items, Linux start at login for the current user and hidden-window startup; GTK system tray and background operation. See the [validation record](docs/audit-validation-2026-10-21.md) for the platforms covered by this round of validation.
- Persistent core settings, DNS / hosts, configuration overrides, additional rules, provider and Geo resource updates, and network diagnostics.
- Local JSON and HTTPS WebDAV backup and restore; private data permissions, atomic saves, rejection of expired writes, rollback after configuration failures, and system proxy restoration records.

The macOS TUN privilege helper, system DNS takeover, JavaScript scripting, global hotkeys, Wi-Fi policies, QR codes, and in-app self-updates are not implemented. Linux TUN requires additional core privileges; real network interception and system proxy restoration still require platform validation. Older macOS versions and aarch64 Linux have not completed this round of validation.

## Building on macOS

Build on an Apple Silicon Mac with Swift 6 and the macOS 26 or newer SDK. The minimum deployment target is macOS 14; compatibility with older systems still needs testing on physical devices.

```sh
sh macos/build-app.sh

# Isolated demo runtime; launch the binary directly, separately from any running instance
build/macos/ZFly.app/Contents/MacOS/ZFly --safe-mode
```

When developing the demo UI directly through SwiftPM, explicitly specify the Python and shared runtime directories (these variables are read only in `--safe-mode`):

```sh
cd macos
ZFLY_DEV_PYTHON=/absolute/path/to/python3 \
ZFLY_DEV_RUNTIME=/absolute/path/to/zfly/runtime \
swift run ZFly --safe-mode
```

Demo operations pass through the real Service's validation, version checks, transactions, rollback, and temporary persistence. The mock core validates only YAML / schema and cannot check the real core's protocol semantics (such as unknown proxy types). It does not perform real latency tests, DNS, WebDAV, Geo or provider downloads, and provides no Age decryption. See [demo mode differences](docs/usage.md#演示模式).

The output is `build/macos/ZFly.app`. The app uses a local ad hoc signature; formal releases still require developer signing and notarization.

Packaging requires a relocatable CPython 3.12, a standalone core, four non-empty Geo data files, and both core licenses:

```sh
ZFLY_PYTHON_HOME=/path/to/python-build-standalone \
ZFLY_CORE=/path/to/ZFlyCore \
ZFLY_CORE_KIND=ipc \
ZFLY_GEODATA=/path/to/geodata \
ZFLY_CORE_LICENSE=/path/to/FlClash-LICENSE \
ZFLY_MIHOMO_LICENSE=/path/to/mihomo-LICENSE \
sh macos/build-app.sh
```

Python can be placed in `build/dependencies/python`; the script also searches for CPython 3.12 installed by the local uv. The core defaults to `build/dependencies/ZFlyCore` or `build/dependencies/mihomo`; specify `ZFLY_CORE_KIND=mihomo` for standard mihomo. The build does not read a core from an installed app.

Geo data is searched in `FlClash/assets/data`, then `build/flclash-host/assets/data`, and must include `ASN.mmdb`, `GEOIP.dat`, `GEOIP.metadb`, and `GEOSITE.dat`. Licenses are read from `LICENSE` and `core/Clash.Meta/LICENSE` under those source roots, respectively. Environment variables can override these paths; the build fails if resources are missing. At the end, the build validates executables, metadata, icons, previews, localization, Geo data, licenses, and the core checksum.

## Linux / NixOS

Run the following commands from the root of the source checkout:

```sh
nix profile install .#
nix run .#
nix run .# -- --safe-mode

nix build .#
nix flake check
```

`flake.nix` configures GTK, Python, nixpkgs mihomo, desktop entries, and icons for `x86_64-linux` and `aarch64-linux`, and runs tests during the build. GNOME system proxy settings are managed through gsettings; other desktops can use manual local proxy settings. On 2026-10-24, the Nix build completed on x86_64 Linux, with 48 GTK UI tests and 12 tray tests also passing separately. Safe mode was used to validate Chinese and English, wide and narrow windows, and native back navigation. For gaps such as real proxy interception, see the [validation record](docs/audit-validation-2026-10-21.md).

With GTK 4, libadwaita, and PyGObject installed, you can also develop from source:

```sh
PYTHONPATH=linux:runtime python3 -m zfly --safe-mode
# A standalone core is required for normal operation
PYTHONPATH=linux:runtime ZFLY_CORE=/path/to/mihomo ZFLY_CORE_KIND=mihomo python3 -m zfly
```

### DEB and RPM Packages (x86_64)

Locally built packages are placed in `build/packages`; the current packages target x86_64 only. Install them from that directory:

```sh
cd build/packages

# Debian / Ubuntu
sudo apt install ./zfly_0.2.0-1_amd64.deb

# Fedora / RHEL
sudo dnf install ./zfly-0.2.0-1.x86_64.rpm
```

## Continuous Integration

The [GitHub Actions build workflow](.github/workflows/build.yml) runs on pushes and pull requests to `main`, and can also be started manually. It builds the Nix package, runs offline, GTK, and tray tests, builds and installs DEB/RPM packages, and uploads them with SHA-256 checksums for 14 days. A separate macOS job builds and tests the app, bundled core, and DMG on macOS 26, verifies the packaged preview and runtime, and uploads the DMG for 14 days. Signing and notarization are not performed.

## Validation

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=runtime python3 -m unittest discover -s runtime/tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=linux:runtime python3 -m unittest discover -s linux/tests -v
swift build --package-path macos
swift test --package-path macos
sh macos/build-app.sh
python3 tools/verify-preview.py --app build/macos/ZFly.app
python3 tools/verify-runtime.py --app build/macos/ZFly.app
```

The integration scripts use only temporary data and loopback ports, and record and clean up their own runtime and core. They do not enable the system proxy, TUN, or external downloads. See the [usage guide](docs/usage.md#开发验证) for GTK graphical interaction validation. For phased results, test coverage, and unvalidated areas, see the [audit remediation validation](docs/audit-validation-2026-10-21.md).

## Source and Licensing

`macos/` contains the SwiftUI app, `linux/zfly/` contains the GTK app, and `runtime/zfly_runtime/` contains the shared standalone runtime. The sole source of preview data for both frontends is `runtime/zfly_runtime/preview.json`. `assets/icon.svg` is the editable icon source, rendered with `python3 tools/render-icons.py`.

`FlClash/` is an optional external upstream checkout and is not present in the current workspace; if present, its history and worktree must be preserved. `build/flclash-host/` is a read-only source snapshot. Legacy management bridge patches, benchmarks, and migration scripts are in [integrations/flclash](integrations/flclash/README.md) and are not used by the current app.

ZFly builds on work related to FlClash / mihomo and is licensed under [GPL-3.0](LICENSE), retaining the original copyright and license notices. Third-party Python / PyYAML and core licenses are distributed with the macOS app.
