# ZFly

ZFly is a standalone application. It owns profiles, subscriptions, core processes, persistent settings and system proxy leases. It must work without a running or installed FlClash app. `FlClash/` is an optional external upstream checkout, absent in this workspace; if present, keep its history and working tree unchanged. `build/flclash-host/` is the current read-only source snapshot.

- macOS: SwiftUI app `macos/Sources/ZFly`, shared adapter `macos/Sources/ZFlyKit`. Build with `sh macos/build-app.sh`.
- Linux: GTK 4 / libadwaita app `linux/zfly`. Package with the root `flake.nix`.
- Editable icon: `assets/icon.svg`. Regenerate PNG and ICNS outputs with `python3 tools/render-icons.py`.
- Launch previews only with `--safe-mode`. On macOS this starts an isolated bundled Python preview runtime with a fresh 0700 temporary data directory and a simulated core; it never starts a real core, accesses external networks or modifies system proxies. The only listener is the private 127.0.0.1 management port; temporary data is removed at exit. Linux keeps its native preview adapters. Preview YAML/schema checks do not replace real-core semantic validation. Never stop a user-owned FlClash/core process or change host networking during verification. Only terminate processes started by this task, using recorded PIDs.
- Shared runtime: `runtime/zfly_runtime`. The native apps launch a private local service and an app-owned core automatically. Never ask users to connect a FlClash management bridge. `integrations/flclash/` is historical migration material only.
- Keep tokens and subscriptions private (0700 data directory, 0600 state). Persist mutations atomically, reject stale writes, roll back failed core applies, and restore only system proxy settings still owned by ZFly.
- Do not claim TUN, autostart, script execution or platform parity without implementation and platform verification. Surface capability limits honestly.
- Do not edit generated SwiftPM, Flutter or icon outputs by hand. Follow `FlClash/AGENTS.md` for changes inside that repository.
- Validate renamed source modules, packaged preview resources, app metadata and icons when changing paths or packaging. State missing platform checks precisely.
