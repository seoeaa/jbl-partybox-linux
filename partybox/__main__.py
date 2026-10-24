"""Запуск GUI или безопасное чтение состояния через CLI."""
import argparse
import json
import subprocess
import sys
from .protocol import PartyBox


def discover():
    result = subprocess.run(['bluetoothctl', 'devices', 'Paired'],
                            text=True, capture_output=True, timeout=6, check=True)
    for line in result.stdout.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) == 3 and parts[2].lower() == 'jbl partybox 710':
            return parts[1]
    return ''


def main():
    parser = argparse.ArgumentParser(description='JBL PartyBox 710 для Linux')
    parser.add_argument('--address', help='Bluetooth MAC-адрес своей PartyBox 710')
    parser.add_argument('--read-state', action='store_true', help='Только прочитать состояние, без GUI')
    args = parser.parse_args()
    address = args.address or discover()
    if args.read_state:
        if not address:
            parser.error('Сопряжённая PartyBox 710 не найдена; укажи --address')
        device = PartyBox(address)
        try:
            print(json.dumps(device.connect(), ensure_ascii=False, indent=2))
        finally:
            device.close()
    else:
        from .gui import App
        App(address).mainloop()


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f'Ошибка: {exc}', file=sys.stderr)
        sys.exit(1)
