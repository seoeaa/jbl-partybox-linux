"""Подтверждённые команды JBL PartyBox 710; без прошивки и power-команд."""
from __future__ import annotations

import time
from collections import deque
from .transport import ATTTransport

PATTERNS = {0: 'Выключено', 1: 'Rock', 4: 'Flow', 5: 'Ripple', 6: 'Cross', 7: 'Flash'}
ZONES = {0x38: 'Кольца динамиков', 0x39: 'Боковая подсветка', 0x40: 'Световые полосы'}


class ProtocolError(RuntimeError):
    pass


def tlvs(payload: bytes) -> dict[int, bytes]:
    """Первый байт TLV-ответа — индекс устройства, а не длина тега."""
    if not payload:
        raise ProtocolError('Пустой TLV-ответ')
    result = {}
    i = 1
    while i < len(payload):
        if i + 2 > len(payload):
            raise ProtocolError('Обрезанный заголовок TLV')
        tag, size = payload[i:i + 2]
        i += 2
        if i + size > len(payload):
            raise ProtocolError(f'Обрезанное значение TLV {tag:02x}')
        result[tag] = payload[i:i + size]
        i += size
    return result


class PartyBox:
    def __init__(self, address: str, trace=None):
        self.transport = ATTTransport(address)
        self.address = address
        self.trace = trace or (lambda direction, frame: None)
        self.pages: dict[int, dict[int, bytes]] = {}
        self.bass: int | None = None
        self._buffer = bytearray()
        self._frames = deque()

    def connect(self):
        try:
            self.transport.connect()
            # Начальные уведомления одновременно прогревают control-канал.
            self._collect(1.8)
            self.refresh()
            if self.pages.get(0x12, {}).get(0x31) != b'\x1f\x61':
                raise ProtocolError('Устройство не подтвердило модель PartyBox 710 (1F61)')
        except BaseException:
            self.close()
            raise
        return self.snapshot()

    def close(self):
        self.transport.close()
        self.pages.clear()
        self.bass = None
        self._buffer.clear()
        self._frames.clear()

    def _send(self, command: int, payload=b''):
        frame = bytes([0xaa, command, len(payload)]) + payload
        self.transport.write(frame)
        self.trace('tx', frame.hex())

    def _next_frame(self, timeout):
        end = time.monotonic() + timeout
        while not self._frames:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Нет ответа JBL')
            self._buffer.extend(self.transport.receive(remaining))
            if len(self._buffer) > 4096:
                raise ProtocolError('Слишком большой незавершённый кадр JBL')
            while self._buffer:
                if self._buffer[0] != 0xaa:
                    raise ProtocolError('Некорректный маркер кадра JBL')
                if len(self._buffer) < 3:
                    break
                size = self._buffer[2] + 3
                if len(self._buffer) < size:
                    break
                frame = bytes(self._buffer[:size])
                del self._buffer[:size]
                self._frames.append(frame)
        frame = self._frames.popleft()
        self.trace('rx', frame.hex())
        command, payload = frame[1], frame[3:]
        if command in (0x12, 0x32, 0x42):
            self.pages.setdefault(command, {}).update(tlvs(payload))
        elif command == 0x62:
            if len(payload) != 1:
                raise ProtocolError('Некорректный ответ Bass Boost')
            self.bass = payload[0]
        return command, payload

    def _collect(self, duration):
        end = time.monotonic() + duration
        while time.monotonic() < end:
            try:
                self._next_frame(end - time.monotonic())
            except TimeoutError:
                break

    def _query(self, command):
        self._send(command)
        end = time.monotonic() + 3
        while time.monotonic() < end:
            op, _ = self._next_frame(end - time.monotonic())
            if op == command + 1:
                # Страница может приходить несколькими отдельными кадрами.
                self._collect(.35)
                return
        raise TimeoutError(f'Нет ответа на команду {command:02x}')

    def refresh(self):
        for command in (0x11, 0x31, 0x41, 0x61):
            self._query(command)
        return self.snapshot()

    def snapshot(self):
        device = self.pages.get(0x12, {})
        light = self.pages.get(0x32, {})
        player = self.pages.get(0x42, {})
        def number(page, tag):
            value = page.get(tag)
            return value[0] if value and len(value) == 1 else None
        firmware = device.get(0x41, b'')
        return {
            'address': self.address,
            'product_id': device.get(0x31, b'').hex(),
            'firmware': '.'.join(str(v) for v in firmware) or None,
            'volume': number(player, 0x42),
            'bass': self.bass,
            'pattern': number(light, 0x31),
            'rgb': list(light[0x32]) if len(light.get(0x32, b'')) == 3 else None,
            'custom_color': number(light, 0x36),
            'zones': {f'{tag:02x}': number(light, tag) for tag in ZONES},
            'raw_pages': {f'{op:02x}': {f'{k:02x}': v.hex() for k,v in page.items()}
                          for op, page in self.pages.items()},
        }

    def _change(self, command, payload, query, check):
        # Сначала обработать накопившиеся уведомления, затем ждать ACK именно записи.
        self._collect(.05)
        self._send(command, payload)
        end = time.monotonic() + 3
        accepted = False
        while time.monotonic() < end:
            op, data = self._next_frame(end - time.monotonic())
            if op == 0 and len(data) == 2 and data[0] == command:
                if data[1] != 0:
                    raise ProtocolError(f'Колонка отклонила команду {command:02x}: {data[1]:02x}')
                accepted = True
                break
        if not accepted:
            raise ProtocolError('Нет подтверждения команды; прочитай состояние перед повтором')
        self._query(query)
        state = self.snapshot()
        if not check(state):
            raise ProtocolError('Команда принята, но новое значение не подтверждено чтением')
        return state

    def set_volume(self, value: int):
        if type(value) is not int or not 0 <= value <= 32:
            raise ValueError('Громкость должна быть целым числом от 0 до 32')
        return self._change(0x43, bytes([0, 0x42, 1, value]), 0x41,
                            lambda s: s['volume'] == value)

    def set_bass(self, value: int):
        if type(value) is not int or value not in (0, 1, 2):
            raise ValueError('Bass Boost: только 0, 1 или 2')
        return self._change(0x63, bytes([value]), 0x61, lambda s: s['bass'] == value)

    def set_pattern(self, value: int):
        if type(value) is not int or value not in PATTERNS:
            raise ValueError('Неизвестный режим подсветки 710')
        return self._change(0x33, bytes([0, 0x31, 1, value]), 0x31,
                            lambda s: s['pattern'] == value)

    def set_color(self, rgb):
        if len(rgb) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in rgb):
            raise ValueError('Цвет: три целых значения RGB от 0 до 255')
        payload = bytes([0, 0x36, 1, 1, 0x32, 3, *rgb])
        return self._change(0x33, payload, 0x31,
                            lambda s: s['rgb'] == list(rgb) and s['custom_color'] == 1)

    def set_auto_color(self):
        return self._change(0x33, bytes([0, 0x36, 1, 0]), 0x31,
                            lambda s: s['custom_color'] == 0)

    def set_zone(self, tag: int, enabled: bool):
        if tag not in ZONES or type(enabled) is not bool:
            raise ValueError('Недопустимая зона подсветки')
        return self._change(0x33, bytes([0, tag, 1, int(enabled)]), 0x31,
                            lambda s: s['zones'].get(f'{tag:02x}') == int(enabled))
