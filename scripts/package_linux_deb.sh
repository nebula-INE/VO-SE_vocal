#!/usr/bin/env bash
# Build the official Ubuntu/Linux Mint installer for VO-SE vocal.
# Usage: bash scripts/package_linux_deb.sh <frozen-binary> <output-deb> <version>
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "Usage: $0 <frozen-binary> <output-deb> <version>" >&2
    exit 2
fi

binary_path="$(realpath "$1")"
output_path="$(realpath -m "$2")"
package_version="$3"

if [[ ! -f "$binary_path" ]]; then
    echo "Linux application binary not found: $binary_path" >&2
    exit 1
fi
if ! dpkg --validate-version "$package_version"; then
    echo "Invalid Debian package version: $package_version" >&2
    exit 1
fi
if [[ "$(dpkg --print-architecture)" != "amd64" ]]; then
    echo "This installer currently supports amd64 only." >&2
    exit 1
fi

mkdir -p "$(dirname "$output_path")"
package_root="$(mktemp -d)"
cleanup() {
    rm -rf "$package_root"
}
trap cleanup EXIT

install -d \
    "$package_root/DEBIAN" \
    "$package_root/opt/vo-se-vocal" \
    "$package_root/usr/share/applications" \
    "$package_root/usr/share/pixmaps"

install -m 0755 "$binary_path" "$package_root/opt/vo-se-vocal/VO-SE_vocal_Linux"

if [[ -f assets/icon.png ]]; then
    install -m 0644 assets/icon.png "$package_root/usr/share/pixmaps/vo-se-vocal.png"
fi

cat > "$package_root/DEBIAN/control" <<CONTROL
Package: vo-se-vocal
Version: $package_version
Section: sound
Priority: optional
Architecture: amd64
Maintainer: Axral <support@axral.dev>
Depends: libc6, libstdc++6, libgl1, libegl1, libopengl0, libxkbcommon0, libxkbcommon-x11-0, libdbus-1-3, libxcb-cursor0, libxcb-icccm4, libxcb-keysyms1, libxcb-shape0, libxrender1, libxi6, libsm6, libxext6, libfontconfig1, libpulse0, libasound2 | libasound2t64, libsndfile1, libportaudio2
Description: VO-SE vocal desktop application
 VO-SE vocal for Linux (CUDA-enabled build).
 Qt/XCB and audio runtime dependencies are installed by the package manager.
CONTROL

cat > "$package_root/usr/share/applications/vo-se-vocal.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=VO-SE vocal
Comment=AI vocal synthesis
Exec=/opt/vo-se-vocal/VO-SE_vocal_Linux
Terminal=false
Categories=AudioVideo;Audio;Music;
StartupNotify=true
DESKTOP

if [[ -f "$package_root/usr/share/pixmaps/vo-se-vocal.png" ]]; then
    echo "Icon=vo-se-vocal" >> "$package_root/usr/share/applications/vo-se-vocal.desktop"
fi

dpkg-deb --root-owner-group --build "$package_root" "$output_path"
dpkg-deb --info "$output_path"
echo "Created Linux installer: $output_path"
