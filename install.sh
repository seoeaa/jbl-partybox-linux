#!/usr/bin/env bash
set -euo pipefail

# Resolve source independently of the current working directory.
script_path=${BASH_SOURCE[0]}
if command -v readlink >/dev/null 2>&1; then
    script_path=$(readlink -f -- "$script_path")
else
    script_path=$(cd -- "$(dirname -- "$script_path")" && pwd -P)/$(basename -- "$script_path")
fi
source_dir=$(cd -- "$(dirname -- "$script_path")" && pwd -P)

fail() { printf 'Ошибка установки: %s\n' "$*" >&2; exit 1; }
warn() { printf 'Предупреждение: %s\n' "$*" >&2; }

[[ -x /usr/bin/python3 ]] || fail 'не найден /usr/bin/python3.'
if ! /usr/bin/python3 - <<'PY'
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk
if Gtk.get_major_version() != 4 or Gtk.get_minor_version() < 10 or Adw.get_minor_version() < 5:
    raise RuntimeError('нужны GTK >= 4.10 и libadwaita >= 1.5')
PY
then
    cat >&2 <<'DEPENDENCIES'
Не найдены системные зависимости GTK4/libadwaita (требуется libadwaita >= 1.5).
Для Debian/Ubuntu установите вручную:
  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libadwaita-1-0
Установщик не запускает apt и не использует pip.
DEPENDENCIES
    exit 1
fi

: "${HOME:?HOME не задан}"
data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
install_dir="$data_home/jbl-partybox-linux"
bin_dir="$HOME/.local/bin"
applications_dir="$data_home/applications"
icons_dir="$data_home/icons/hicolor"
wrapper="$bin_dir/partybox710"
desktop_file="$applications_dir/partybox710.desktop"

# Check prerequisites before changing anything; only install explicit allowlist.
for file in README.md LICENSE run.sh; do
    [[ -f "$source_dir/$file" ]] || fail "не найден обязательный файл $file (репозиторий ещё не готов)."
done
shopt -s nullglob
python_files=("$source_dir"/partybox/*.py)
(( ${#python_files[@]} > 0 )) || fail 'в partybox/ нет Python-файлов.'

mkdir -p -- "$install_dir/partybox" "$bin_dir" "$applications_dir"
for file in "${python_files[@]}"; do
    install -m 0644 -- "$file" "$install_dir/partybox/$(basename -- "$file")"
done
install -m 0755 -- "$source_dir/run.sh" "$install_dir/run.sh"
install -m 0644 -- "$source_dir/README.md" "$install_dir/README.md"
install -m 0644 -- "$source_dir/LICENSE" "$install_dir/LICENSE"

# Quote a literal installed path for a Bash command in the launcher.
shell_quote() { local value=$1; value=${value//\'/\'\\\'\'}; printf "'%s'" "$value"; }
printf '#!/usr/bin/env bash\nset -euo pipefail\nexec %s "$@"\n' "$(shell_quote "$install_dir/run.sh")" > "$wrapper"
chmod 0755 -- "$wrapper"

# GLib serializes Desktop Entry string escapes; Exec has a separate argument layer.
/usr/bin/python3 - "$install_dir" "$desktop_file" <<'PYDESKTOP'
from pathlib import Path
import sys
from gi.repository import GLib
install_dir, desktop_file = sys.argv[1:]
def quote_argument(value):
    value = value.replace('%', '%%')
    return '"' + ''.join('\\' + c if c in '\\`"$' else c for c in value) + '"'
keyfile = GLib.KeyFile()
values = {
    'Type': 'Application', 'Name': 'PartyBox 710',
    'Comment': 'Управление JBL PartyBox 710 по Bluetooth LE и вывод звука Linux',
    'Exec': quote_argument(str(Path(install_dir) / 'run.sh')),
    'Path': install_dir, 'Icon': 'org.partybox.Linux710',
    'Categories': 'AudioVideo;Audio;', 'StartupWMClass': 'org.partybox.Linux710',
}
for key, value in values.items():
    keyfile.set_string('Desktop Entry', key, value)
keyfile.set_boolean('Desktop Entry', 'Terminal', False)
keyfile.set_boolean('Desktop Entry', 'StartupNotify', True)
Path(desktop_file).write_text(keyfile.to_data()[0])
PYDESKTOP

icon_installed=0
for size in 16 32 48 64 128 256 512; do
    icon="$source_dir/assets/icons/$size.png"
    if [[ -f "$icon" ]]; then
        target_dir="$icons_dir/${size}x${size}/apps"
        mkdir -p -- "$target_dir"
        install -m 0644 -- "$icon" "$target_dir/org.partybox.Linux710.png"
        mkdir -p -- "$install_dir/assets/icons"
        install -m 0644 -- "$icon" "$install_dir/assets/icons/$size.png"
        icon_installed=1
    else
        warn "нет assets/icons/$size.png; пропускаю этот размер."
    fi
done
if [[ -f "$source_dir/assets/icon.png" ]]; then
    mkdir -p -- "$install_dir/assets"
    install -m 0644 -- "$source_dir/assets/icon.png" "$install_dir/assets/icon.png"
else
    warn 'нет assets/icon.png; пропускаю основной PNG.'
fi
(( icon_installed == 1 )) || warn 'не установлен ни один размер значка; значок приложения может не отображаться.'

if command -v update-desktop-database >/dev/null 2>&1; then
    if ! update-desktop-database "$applications_dir"; then
        warn "update-desktop-database завершился с ошибкой для $applications_dir."
    fi
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1 && [[ -d "$icons_dir" ]]; then
    if ! gtk-update-icon-cache -f -t "$icons_dir"; then
        warn "gtk-update-icon-cache завершился с ошибкой для $icons_dir."
    fi
fi
printf 'Установлено для текущего пользователя.\nПриложение: %s\nКоманда: %s\nЯрлык: %s\n' "$install_dir" "$wrapper" "$desktop_file"
