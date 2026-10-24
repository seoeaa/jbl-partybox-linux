#!/usr/bin/env bash
set -euo pipefail

script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
repo_root=$(cd -- "$(dirname -- "$script_path")/.." && pwd -P)
version=${1:-}
if [[ -z "$version" ]]; then
    [[ -f "$repo_root/VERSION" ]] || { echo 'VERSION is required or root VERSION must exist' >&2; exit 2; }
    version=$(<"$repo_root/VERSION")
fi
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'VERSION must match N.N.N' >&2; exit 2; }
outdir=${2:-"$repo_root/dist"}
mkdir -p -- "$outdir"
outdir=$(cd -- "$outdir" && pwd -P)

for cmd in dpkg-deb tar gzip sha256sum install; do
    command -v "$cmd" >/dev/null || { echo "Required command not found: $cmd" >&2; exit 127; }
done
for path in README.md LICENSE install.sh run.sh partybox/__main__.py assets/provenance.json assets/icon.png packaging/partybox710.desktop; do
    [[ -f "$repo_root/$path" ]] || { echo "Required source missing: $path" >&2; exit 1; }
done
shopt -s nullglob
python_files=("$repo_root"/partybox/*.py)
icon_files=("$repo_root"/assets/icons/*.png)
((${#python_files[@]} && ${#icon_files[@]})) || { echo 'Python sources and icon sizes are required' >&2; exit 1; }

if [[ -n "${SOURCE_DATE_EPOCH:-}" ]]; then
    epoch=$SOURCE_DATE_EPOCH
elif git -C "$repo_root" rev-parse --verify HEAD >/dev/null 2>&1; then
    epoch=$(git -C "$repo_root" log -1 --format=%ct HEAD)
else
    epoch=0
fi
[[ "$epoch" =~ ^[0-9]+$ ]] || { echo 'SOURCE_DATE_EPOCH must be a non-negative integer' >&2; exit 2; }

stage=$(mktemp -d /tmp/partybox-release-XXXXXX)
cleanup() {
    case "$stage" in
        /tmp/partybox-release-*) [[ -d "$stage" ]] && rm -rf -- "$stage" ;;
        *) echo "Refusing cleanup of unexpected staging path: $stage" >&2; return 1 ;;
    esac
}
trap cleanup EXIT

pkg="$stage/deb"
archive="$stage/archive/partybox710-$version"
mkdir -p "$pkg/DEBIAN" "$pkg/usr/lib/jbl-partybox-linux/partybox" \
    "$pkg/usr/bin" "$pkg/usr/share/applications" "$pkg/usr/share/doc/partybox710" \
    "$pkg/usr/share/icons/hicolor" "$archive/partybox" "$archive/assets/icons"
for file in "${python_files[@]}"; do install -m 0644 -- "$file" "$pkg/usr/lib/jbl-partybox-linux/partybox/$(basename -- "$file")"; done
install -m 0644 -- "$repo_root/README.md" "$pkg/usr/share/doc/partybox710/README.md"
install -m 0644 -- "$repo_root/LICENSE" "$pkg/usr/share/doc/partybox710/copyright"
install -m 0644 -- "$repo_root/packaging/partybox710.desktop" "$pkg/usr/share/applications/org.partybox.Linux710.desktop"
cat > "$pkg/usr/bin/partybox710" <<'LAUNCHER'
#!/bin/sh
set -eu
cd /usr/lib/jbl-partybox-linux
exec /usr/bin/python3 -m partybox "$@"
LAUNCHER
chmod 0755 "$pkg/usr/bin/partybox710"
for icon in "${icon_files[@]}"; do
    size=$(basename -- "$icon" .png)
    [[ "$size" =~ ^(16|32|48|64|128|256|512)$ ]] || continue
    install -D -m 0644 -- "$icon" "$pkg/usr/share/icons/hicolor/${size}x${size}/apps/org.partybox.Linux710.png"
done
cat > "$pkg/DEBIAN/control" <<CONTROL
Package: partybox710
Version: $version
Section: sound
Priority: optional
Architecture: all
Maintainer: seoeaa <3609621+seoeaa@users.noreply.github.com>
Homepage: https://github.com/seoeaa/jbl-partybox-linux
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0 (>= 4.10), gir1.2-adw-1 (>= 1.5), bluez, pulseaudio-utils, systemd, pipewire-pulse, wireplumber, libspa-0.2-bluetooth
Description: GTK4 client for JBL PartyBox 710 on Linux
CONTROL

for file in "${python_files[@]}"; do install -m 0644 -- "$file" "$archive/partybox/$(basename -- "$file")"; done
for file in README.md LICENSE; do install -m 0644 -- "$repo_root/$file" "$archive/$file"; done
install -m 0755 -- "$repo_root/run.sh" "$archive/run.sh"
install -m 0755 -- "$repo_root/install.sh" "$archive/install.sh"
printf '%s\n' "$version" > "$archive/VERSION"
install -m 0644 -- "$repo_root/assets/icon.png" "$archive/assets/icon.png"
install -m 0644 -- "$repo_root/assets/provenance.json" "$archive/assets/provenance.json"
for icon in "${icon_files[@]}"; do install -m 0644 -- "$icon" "$archive/assets/icons/$(basename -- "$icon")"; done

# Normalize staged metadata before building both deliverables.
find "$pkg" "$archive" -exec touch -h -d "@$epoch" {} +
export SOURCE_DATE_EPOCH="$epoch"
dpkg-deb --root-owner-group --build "$pkg" "$outdir/partybox710_${version}_all.deb" >/dev/null
mkdir -p "$stage/archive"
( cd "$stage/archive" && tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="@$epoch" -cf - "partybox710-$version" | gzip -n > "$outdir/partybox710-${version}-linux.tar.gz" )
( cd "$outdir" && sha256sum "partybox710_${version}_all.deb" "partybox710-${version}-linux.tar.gz" > SHA256SUMS )
printf 'Built release artifacts in %s\n' "$outdir"
