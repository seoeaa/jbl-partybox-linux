"""Сырой транспорт ATT для JBL PartyBox 710 на Linux."""

from __future__ import annotations

import ctypes
import errno
import select
import socket
import struct
import time
import uuid
from collections import deque


_AF_BLUETOOTH = 31
_BTPROTO_L2CAP = 0
_L2CAP_CID_ATT = 4
_BT_LE_PUBLIC = 1
_ATT_DEFAULT_MTU = 23
_ATT_MAX_MTU = 517
_ATT_RX_TIMEOUT = 3.0

_SERVICE_UUID = uuid.UUID("65786365-6c70-6f69-6e74-2e636f6d0000")
_TX_UUID = uuid.UUID("65786365-6c70-6f69-6e74-2e636f6d0002")
_RX_UUID = uuid.UUID("65786365-6c70-6f69-6e74-2e636f6d0001")


class ATTError(OSError):
    """Ошибка, полученная от ATT-сервера."""

    def __init__(self, request_opcode: int, handle: int, code: int):
        self.request_opcode = request_opcode
        self.handle = handle
        self.code = code
        super().__init__(
            f"ATT Error Response: request=0x{request_opcode:02x}, "
            f"handle=0x{handle:04x}, code=0x{code:02x}"
        )


class ATTTransport:
    """Подключение к ATT-каналу JBL без управления BlueZ или аудио."""

    def __init__(self, address: str, timeout: float = 8):
        self.address = address
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError("timeout должен быть больше нуля")
        self.tx_handle: int | None = None
        self.rx_handle: int | None = None
        self.mtu = _ATT_DEFAULT_MTU
        self._sock: socket.socket | None = None
        self._notifications: deque[bytes] = deque()

    @property
    def tx_handle_value(self) -> int | None:
        """Дескриптор характеристики отправки, если обнаружен."""
        return self.tx_handle

    @property
    def rx_handle_value(self) -> int | None:
        """Дескриптор характеристики приёма, если обнаружен."""
        return self.rx_handle

    def connect(self) -> None:
        """Открыть отдельный L2CAP ATT-сокет и обнаружить характеристики."""
        if self._sock is not None:
            raise RuntimeError("ATTTransport уже подключён")
        try:
            remote = bytes.fromhex(self.address.replace(":", ""))
        except ValueError as exc:
            raise ValueError("Некорректный Bluetooth MAC-адрес") from exc
        if len(remote) != 6:
            raise ValueError("Bluetooth MAC-адрес должен содержать 6 байт")

        sock = socket.socket(_AF_BLUETOOTH, socket.SOCK_SEQPACKET, _BTPROTO_L2CAP)
        self._sock = sock
        try:
            sock.setblocking(False)
            local_addr = struct.pack("<HH6sHBx", _AF_BLUETOOTH, 0, b"\0" * 6,
                                     _L2CAP_CID_ATT, _BT_LE_PUBLIC)
            remote_addr = struct.pack("<HH6sHBx", _AF_BLUETOOTH, 0, remote[::-1],
                                      _L2CAP_CID_ATT, _BT_LE_PUBLIC)
            libc = ctypes.CDLL(None, use_errno=True)
            bind_fn = libc.bind
            bind_fn.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_uint)
            bind_fn.restype = ctypes.c_int
            connect_fn = libc.connect
            connect_fn.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_uint)
            connect_fn.restype = ctypes.c_int
            local_buf = ctypes.create_string_buffer(local_addr)
            remote_buf = ctypes.create_string_buffer(remote_addr)
            if bind_fn(sock.fileno(), local_buf, len(local_addr)) != 0:
                code = ctypes.get_errno()
                raise OSError(code, f"bind L2CAP: {errno.errorcode.get(code, code)}")
            result = connect_fn(sock.fileno(), remote_buf, len(remote_addr))
            if result != 0:
                code = ctypes.get_errno()
                if code not in (errno.EINPROGRESS, errno.EAGAIN, errno.EWOULDBLOCK):
                    raise OSError(code, f"connect L2CAP: {errno.errorcode.get(code, code)}")
                _, writable, exceptional = select.select([], [sock], [sock], self.timeout)
                if exceptional or not writable:
                    raise TimeoutError("Истекло время подключения к ATT")
                so_error = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if so_error:
                    raise OSError(so_error, f"connect L2CAP: {errno.errorcode.get(so_error, so_error)}")

            sock.settimeout(_ATT_RX_TIMEOUT)
            self._discover()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Закрыть только созданный этим объектом сокет."""
        sock, self._sock = self._sock, None
        if sock is not None:
            sock.close()
        self.tx_handle = None
        self.rx_handle = None
        self.mtu = _ATT_DEFAULT_MTU
        self._notifications.clear()

    def __enter__(self) -> "ATTTransport":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def write(self, frame: bytes) -> None:
        """Отправить кадр JBL командой ATT Write Command (без подтверждения)."""
        sock = self._require_socket()
        if self.tx_handle is None:
            raise RuntimeError("Характеристика передачи ещё не обнаружена")
        if not isinstance(frame, (bytes, bytearray, memoryview)):
            raise TypeError("frame должен быть байтовой последовательностью")
        packet = b"\x52" + struct.pack("<H", self.tx_handle) + bytes(frame)
        if len(packet) > self.mtu:
            raise ValueError(f"ATT-пакет превышает согласованный MTU {self.mtu}")
        sock.sendall(packet)

    def receive(self, timeout: float) -> bytes:
        """Вернуть следующее уведомление JBL; таймаут относится к ожиданию."""
        sock = self._require_socket()
        if timeout < 0:
            raise ValueError("timeout не может быть отрицательным")
        if self._notifications:
            return self._notifications.popleft()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Истекло время ожидания уведомления JBL")
            try:
                sock.settimeout(remaining)
                packet = sock.recv(self.mtu)
            except socket.timeout as exc:
                raise TimeoutError("Истекло время ожидания уведомления JBL") from exc
            if not packet:
                raise ConnectionError("ATT-соединение закрыто удалённой стороной")
            if self._handle_async(packet):
                if self._notifications:
                    return self._notifications.popleft()

    def _require_socket(self) -> socket.socket:
        if self._sock is None:
            raise RuntimeError("ATTTransport не подключён")
        return self._sock

    def _recv_packet(self, expected_opcode: int) -> bytes:
        sock = self._require_socket()
        deadline = time.monotonic() + _ATT_RX_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Истекло время ожидания ATT-ответа")
            sock.settimeout(remaining)
            try:
                packet = sock.recv(self.mtu)
            except socket.timeout as exc:
                raise TimeoutError("Истекло время ожидания ATT-ответа") from exc
            if not packet:
                raise ConnectionError("ATT-соединение закрыто удалённой стороной")
            if self._handle_async(packet):
                continue
            opcode = packet[0]
            if opcode == 0x01:
                if len(packet) != 5:
                    raise ValueError("Некорректный ATT Error Response")
                raise ATTError(packet[1], struct.unpack_from("<H", packet, 2)[0], packet[4])
            if opcode != expected_opcode:
                raise ValueError(
                    f"Неожиданный ATT opcode 0x{opcode:02x}; ожидался 0x{expected_opcode:02x}"
                )
            return packet

    def _request(self, request: bytes, expected_opcode: int) -> bytes:
        self._require_socket().sendall(request)
        return self._recv_packet(expected_opcode)

    def _handle_async(self, packet: bytes) -> bool:
        opcode = packet[0]
        if opcode == 0x02:
            if len(packet) != 3:
                raise ValueError("Некорректный ATT Exchange MTU Request")
            peer_mtu = struct.unpack_from("<H", packet, 1)[0]
            if peer_mtu < _ATT_DEFAULT_MTU:
                raise ValueError(f"Удалённый ATT MTU недопустим: {peer_mtu}")
            self.mtu = min(_ATT_MAX_MTU, peer_mtu)
            self._require_socket().sendall(b"\x03" + struct.pack("<H", _ATT_MAX_MTU))
            return True
        if opcode in (0x1B, 0x1D):
            if len(packet) < 3:
                raise ValueError("Обрезанное ATT уведомление/индикация")
            handle = struct.unpack_from("<H", packet, 1)[0]
            if self.rx_handle is not None and handle == self.rx_handle:
                self._notifications.append(packet[3:])
            if opcode == 0x1D:
                self._require_socket().sendall(b"\x1e")
            return True
        return False

    @staticmethod
    def _uuid_from_wire(value: bytes) -> uuid.UUID:
        if len(value) == 2:
            return uuid.UUID(int=int.from_bytes(value, "little"))
        if len(value) == 16:
            return uuid.UUID(bytes=value[::-1])
        raise ValueError(f"Недопустимая длина UUID: {len(value)}")

    def _discover(self) -> None:
        start, end = 1, 0xFFFF
        service_start = service_end = None
        while start <= end:
            try:
                response = self._request(b"\x10" + struct.pack("<HHH", start, end, 0x2800), 0x11)
            except ATTError as exc:
                if exc.request_opcode == 0x10 and exc.code == 0x0A:
                    break
                raise
            if len(response) < 2:
                raise ValueError("Обрезанный ATT Read By Group Type Response")
            entry_len = response[1]
            if entry_len not in (6, 20) or (len(response) - 2) % entry_len:
                raise ValueError("Некорректные записи сервисов в ATT-ответе")
            entries = [response[i:i + entry_len] for i in range(2, len(response), entry_len)]
            for entry in entries:
                begin, finish = struct.unpack_from("<HH", entry)
                if begin == 0 or finish < begin:
                    raise ValueError("Некорректный диапазон ATT-сервиса")
                if self._uuid_from_wire(entry[4:]) == _SERVICE_UUID:
                    service_start, service_end = begin, finish
                    break
            if service_start is not None:
                break
            last_end = struct.unpack_from("<H", entries[-1], 2)[0]
            if last_end >= end:
                break
            start = last_end + 1
        if service_start is None:
            raise LookupError(f"Сервис {_SERVICE_UUID} не найден")

        start, end = service_start, service_end
        while start <= end:
            try:
                response = self._request(b"\x08" + struct.pack("<HHH", start, end, 0x2803), 0x09)
            except ATTError as exc:
                if exc.request_opcode == 0x08 and exc.code == 0x0A:
                    break
                raise
            if len(response) < 2:
                raise ValueError("Обрезанный ATT Read By Type Response")
            entry_len = response[1]
            if entry_len not in (7, 21) or (len(response) - 2) % entry_len:
                raise ValueError("Некорректные записи характеристик в ATT-ответе")
            entries = [response[i:i + entry_len] for i in range(2, len(response), entry_len)]
            for entry in entries:
                declaration = struct.unpack_from("<H", entry)[0]
                value_handle = struct.unpack_from("<H", entry, 3)[0]
                if value_handle <= declaration:
                    raise ValueError("Некорректные дескрипторы ATT-характеристики")
                char_uuid = self._uuid_from_wire(entry[5:])
                if char_uuid == _TX_UUID:
                    self.tx_handle = value_handle
                elif char_uuid == _RX_UUID:
                    self.rx_handle = value_handle
            last = struct.unpack_from("<H", entries[-1], 0)[0]
            if last >= end:
                break
            start = last + 1
        if self.tx_handle is None or self.rx_handle is None:
            raise LookupError("Не найдены обе характеристики JBL (TX/RX)")
