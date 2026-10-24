"""Выбор A2DP-аудиовыхода, отдельно от BLE-настроек колонки."""
from __future__ import annotations

import json
import re
import subprocess
import time


def _address(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', value):
        raise ValueError('Укажи Bluetooth MAC-адрес в формате AA:BB:CC:DD:EE:FF')
    return value.upper()


def _run(*args: str, timeout=8) -> str:
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f'Команда {args[0]} не установлена') from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'{args[0]} не ответил за {timeout} с') from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f'{args[0]}: {detail or "команда завершилась с ошибкой"}')
    return result.stdout.strip()


def _sinks(address):
    prefix = 'bluez_output.' + address.replace(':', '_') + '.'
    sinks = json.loads(_run('pactl', '-f', 'json', 'list', 'sinks'))
    if not isinstance(sinks, list):
        raise RuntimeError('Аудиосистема вернула неизвестный формат списка выходов')
    return [sink for sink in sinks if sink.get('name', '').upper().startswith(prefix.upper())]


def _volume(sink):
    channels = sink.get('volume', {}).values()
    values = [v.get('value') for v in channels if isinstance(v, dict) and isinstance(v.get('value'), (int, float))]
    return max(values) / 65536 * 100 if values else None


def get_audio_status(address: str) -> dict:
    """Прочитать выход JBL и выбор по умолчанию, ничего не переключая."""
    address = _address(address)
    candidates = _sinks(address)
    if not candidates:
        return {'available': False, 'is_default': False, 'sink': None,
                'description': 'Аудиопрофиль A2DP ещё не подключён', 'volume_percent': None}
    default = _run('pactl', 'get-default-sink')
    sink = next((s for s in candidates if s.get('name') == default), candidates[0])
    return {'available': True, 'is_default': sink['name'] == default, 'sink': sink['name'],
            'description': sink.get('properties', {}).get('device.description', 'JBL PartyBox 710'),
            'volume_percent': _volume(sink)}


def route_audio(address: str) -> dict:
    """По явному действию выбрать JBL; не повышать уровень выше безопасных 20 %."""
    address = _address(address)
    candidates = _sinks(address)
    if not candidates:
        suffix = '/dev_' + address.replace(':', '_')
        paths = [p.strip() for p in _run('busctl', '--list', 'tree', 'org.bluez').splitlines()
                 if re.fullmatch(r'/org/bluez/hci[0-9]+' + re.escape(suffix), p.strip())]
        if not paths:
            raise RuntimeError('Колонка не найдена в BlueZ. Сначала подключи её в настройках Bluetooth.')
        if len(paths) > 1:
            raise RuntimeError('Колонка найдена на нескольких Bluetooth-адаптерах; оставь подключение на одном адаптере.')
        path = paths[0]
        _run('busctl', '--timeout=12', 'call', 'org.bluez', path, 'org.bluez.Device1',
             'ConnectProfile', 's', '0000110b-0000-1000-8000-00805f9b34fb', timeout=14)
    # Новый выход может публиковаться PipeWire немного позже D-Bus ответа.
    deadline = time.monotonic() + 4
    candidates = _sinks(address)
    while not candidates and time.monotonic() < deadline:
        time.sleep(.2)
        candidates = _sinks(address)
    if not candidates:
        raise RuntimeError('A2DP подключён, но PipeWire не создал аудиовыход. Проверь libspa-0.2-bluetooth и WirePlumber.')
    default = _run('pactl', 'get-default-sink')
    sink = next((s for s in candidates if s.get('name') == default), candidates[0])
    volume = _volume(sink)
    if volume is None:
        raise RuntimeError('Не удалось прочитать громкость аудиовыхода; переключение отменено')
    if volume > 20:
        _run('pactl', 'set-sink-volume', sink['name'], '20%')
    _run('pactl', 'set-sink-mute', sink['name'], '0')
    _run('pactl', 'set-default-sink', sink['name'])
    streams = json.loads(_run('pactl', '-f', 'json', 'list', 'sink-inputs'))
    for stream in streams:
        if stream.get('sink') == sink['index']:
            continue
        try:
            _run('pactl', 'move-sink-input', str(stream['index']), sink['name'])
        except RuntimeError:
            # Между перечислением и переносом проигрыватель мог закрыться.
            current = json.loads(_run('pactl', '-f', 'json', 'list', 'sink-inputs'))
            if any(s.get('index') == stream['index'] for s in current):
                raise
    status = get_audio_status(address)
    if not status['is_default']:
        raise RuntimeError('Аудиосистема не подтвердила выбор JBL по умолчанию')
    current = json.loads(_run('pactl', '-f', 'json', 'list', 'sink-inputs'))
    if any(s.get('sink') != sink['index'] for s in current):
        raise RuntimeError('JBL выбрана по умолчанию, но не все текущие потоки переключились; проверь настройки приложения-проигрывателя')
    return status
