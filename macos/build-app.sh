#!/bin/sh
set -eu

package_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$package_dir/.." && pwd)
artifact_dir="$repo_dir/build/macos"
app_dir="$artifact_dir/ZFly.app"
mkdir -p "$artifact_dir/cache" "$artifact_dir/config" "$artifact_dir/security" "$artifact_dir/modules"
export CLANG_MODULE_CACHE_PATH="$artifact_dir/modules"
export SWIFTPM_MODULECACHE_OVERRIDE="$artifact_dir/modules"
if [ "${ZFLY_SWIFTPM_DISABLE_SANDBOX:-0}" = 1 ]; then
  set -- --disable-sandbox
else
  set --
fi

swift build --package-path "$package_dir" -c release -debug-info-format none \
  --scratch-path "$artifact_dir/swift" \
  --cache-path "$artifact_dir/cache" --config-path "$artifact_dir/config" \
  --security-path "$artifact_dir/security" --manifest-cache local \
  --disable-netrc --disable-keychain "$@"
binary_dir=$(swift build --package-path "$package_dir" -c release -debug-info-format none --show-bin-path \
  --scratch-path "$artifact_dir/swift" \
  --cache-path "$artifact_dir/cache" --config-path "$artifact_dir/config" \
  --security-path "$artifact_dir/security" --manifest-cache local \
  --disable-netrc --disable-keychain "$@")

mkdir -p "$app_dir/Contents/MacOS" "$app_dir/Contents/Resources"
cp "$binary_dir/ZFly" "$app_dir/Contents/MacOS/ZFly"
for bundle in "$binary_dir"/*.bundle; do
  [ -d "$bundle" ] || continue
  bundle_name=$(basename "$bundle")
  mkdir -p "$app_dir/Contents/Resources/$bundle_name"
  cp -R "$bundle/." "$app_dir/Contents/Resources/$bundle_name/"
done
for locale_dir in "$binary_dir"/ZFly_ZFly.bundle/Contents/Resources/*.lproj; do
  [ -d "$locale_dir" ] || continue
  locale_name=$(basename "$locale_dir")
  mkdir -p "$app_dir/Contents/Resources/$locale_name"
  cp -R "$locale_dir/." "$app_dir/Contents/Resources/$locale_name/"
done
cat > "$app_dir/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>ZFly</string>
  <key>CFBundleIdentifier</key><string>io.github.zfly.ZFly</string>
  <key>CFBundleName</key><string>ZFly</string>
  <key>CFBundleDisplayName</key><string>ZFly</string>
  <key>CFBundleDevelopmentRegion</key><string>zh-Hans</string>
  <key>CFBundleLocalizations</key><array><string>zh-Hans</string><string>en</string></array>
  <key>CFBundleIconFile</key><string>ZFly</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.2.0</string>
  <key>CFBundleVersion</key><string>2</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict>
    <key>NSAllowsLocalNetworking</key><true/>
    <key>NSExceptionDomains</key><dict>
      <key>127.0.0.1</key><dict><key>NSExceptionAllowsInsecureHTTPLoads</key><true/></dict>
      <key>localhost</key><dict><key>NSExceptionAllowsInsecureHTTPLoads</key><true/></dict>
    </dict>
  </dict>
</dict></plist>
PLIST
cp "$repo_dir/assets/icons/ZFly.icns" "$app_dir/Contents/Resources/ZFly.icns"
python3 "$repo_dir/tools/bundle-runtime.py" "$app_dir"
codesign --force --sign - "$app_dir"
codesign --verify --deep --strict "$app_dir"
printf '%s\n' "$app_dir"
