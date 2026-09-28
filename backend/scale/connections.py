"""Camada de conexão física com a balança.

Fornece duas implementações de :class:`BaseConnection`:

* :class:`SerialConnection` – porta serial RS232 (pyserial);
* :class:`TcpConnection`    – socket TCP/IP.

Ambas expõem uma interface comum de leitura por linha, com timeouts e
tratamento de erros, permitindo que o :class:`ScaleManager` seja agnóstico
quanto ao meio físico.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Optional


class ConnectionError_(Exception):
    """Erro genérico de conexão com a balança."""


class BaseConnection:
    """Interface comum para conexões com a balança."""

    def open(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def read_line(self) -> Optional[str]:
        """Lê uma linha (até o terminador) e devolve texto, ou ``None``."""
        raise NotImplementedError

    @property
    def is_open(self) -> bool:
        raise NotImplementedError

    def describe(self) -> str:
        return self.__class__.__name__


class SerialConnection(BaseConnection):
    """Conexão RS232 via pyserial."""

    def __init__(self, cfg: dict, encoding: str = "ascii",
                 terminator: str = "\r\n"):
        self.cfg = cfg or {}
        self.encoding = encoding or "ascii"
        self.terminator = terminator or "\r\n"
        self._serial = None
        self._buffer = bytearray()
        self._lock = threading.Lock()

    def open(self) -> None:
        try:
            import serial  # import tardio: pyserial é opcional em ambientes sem serial
        except ImportError as exc:  # pragma: no cover
            raise ConnectionError_(
                "Biblioteca pyserial não instalada. Execute: pip install pyserial"
            ) from exc

        parity_map = {
            "N": serial.PARITY_NONE,
            "E": serial.PARITY_EVEN,
            "O": serial.PARITY_ODD,
            "M": serial.PARITY_MARK,
            "S": serial.PARITY_SPACE,
        }
        stopbits_map = {
            1: serial.STOPBITS_ONE,
            1.5: serial.STOPBITS_ONE_POINT_FIVE,
            2: serial.STOPBITS_TWO,
        }
        try:
            self._serial = serial.Serial(
                port=self.cfg.get("port", "COM1"),
                baudrate=int(self.cfg.get("baudrate", 9600)),
                bytesize=int(self.cfg.get("bytesize", 8)),
                parity=parity_map.get(
                    str(self.cfg.get("parity", "N")).upper(), serial.PARITY_NONE
                ),
                stopbits=stopbits_map.get(
                    float(self.cfg.get("stopbits", 1)), serial.STOPBITS_ONE
                ),
                timeout=float(self.cfg.get("timeout", 1.0)),
            )
        except Exception as exc:  # serial.SerialException e afins
            raise ConnectionError_(f"Falha ao abrir porta serial: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None

    @property
    def is_open(self) -> bool:
        return self._serial is not None and getattr(self._serial, "is_open", False)

    def read_line(self) -> Optional[str]:
        if not self.is_open:
            return None
        with self._lock:
            try:
                # Lê bytes disponíveis e acumula no buffer.
                chunk = self._serial.read(256)
            except Exception as exc:
                raise ConnectionError_(f"Erro de leitura serial: {exc}") from exc
        if chunk:
            self._buffer.extend(chunk)
        return self._extract_line()

    def _extract_line(self) -> Optional[str]:
        term = self.terminator.encode(self.encoding, errors="ignore")
        idx = self._buffer.find(term)
        if idx == -1:
            # Fallback: quebra por \n ou \r isolados.
            for single in (b"\n", b"\r"):
                idx2 = self._buffer.find(single)
                if idx2 != -1:
                    line = bytes(self._buffer[:idx2])
                    del self._buffer[: idx2 + 1]
                    return self._decode(line)
            return None
        line = bytes(self._buffer[:idx])
        del self._buffer[: idx + len(term)]
        return self._decode(line)

    def _decode(self, data: bytes) -> str:
        return data.decode(self.encoding, errors="ignore").strip()

    def describe(self) -> str:
        return (
            f"RS232 {self.cfg.get('port')} @ {self.cfg.get('baudrate')} baud "
            f"({self.cfg.get('bytesize')}{self.cfg.get('parity')}{self.cfg.get('stopbits')})"
        )


class TcpConnection(BaseConnection):
    """Conexão TCP/IP com a balança (socket cliente)."""

    def __init__(self, cfg: dict, encoding: str = "ascii",
                 terminator: str = "\r\n"):
        self.cfg = cfg or {}
        self.encoding = encoding or "ascii"
        self.terminator = terminator or "\r\n"
        self._sock: Optional[socket.socket] = None
        self._buffer = bytearray()
        self._lock = threading.Lock()

    def open(self) -> None:
        host = self.cfg.get("host", "127.0.0.1")
        port = int(self.cfg.get("port", 4001))
        timeout = float(self.cfg.get("timeout", 3.0))
        try:
            self._sock = socket.create_connection((host, port), timeout=timeout)
            self._sock.settimeout(timeout)
        except Exception as exc:
            raise ConnectionError_(
                f"Falha ao conectar em {host}:{port} — {exc}"
            ) from exc

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def read_line(self) -> Optional[str]:
        if not self.is_open:
            return None
        with self._lock:
            try:
                chunk = self._sock.recv(256)
            except socket.timeout:
                # Sem dados dentro do timeout: conexão continua ativa.
                return None
            except Exception as exc:
                raise ConnectionError_(f"Erro de leitura TCP: {exc}") from exc
        if chunk == b"":
            # recv vazio (EOF) significa que o equipamento encerrou a conexão.
            raise ConnectionError_("Conexão TCP encerrada pelo equipamento")
        self._buffer.extend(chunk)
        return self._extract_line()

    def _extract_line(self) -> Optional[str]:
        term = self.terminator.encode(self.encoding, errors="ignore")
        idx = self._buffer.find(term)
        if idx == -1:
            for single in (b"\n", b"\r"):
                idx2 = self._buffer.find(single)
                if idx2 != -1:
                    line = bytes(self._buffer[:idx2])
                    del self._buffer[: idx2 + 1]
                    return self._decode(line)
            return None
        line = bytes(self._buffer[:idx])
        del self._buffer[: idx + len(term)]
        return self._decode(line)

    def _decode(self, data: bytes) -> str:
        return data.decode(self.encoding, errors="ignore").strip()

    def describe(self) -> str:
        return f"TCP {self.cfg.get('host')}:{self.cfg.get('port')}"


def build_connection(conn_cfg: dict, protocol_cfg: dict) -> BaseConnection:
    """Cria a conexão apropriada a partir da configuração."""
    conn_type = (conn_cfg or {}).get("type", "serial")
    encoding = (protocol_cfg or {}).get("encoding", "ascii")
    terminator = (protocol_cfg or {}).get("line_terminator", "\r\n")
    if conn_type == "tcp":
        return TcpConnection(conn_cfg.get("tcp", {}), encoding, terminator)
    return SerialConnection(conn_cfg.get("serial", {}), encoding, terminator)


def list_serial_ports() -> list:
    """Lista as portas seriais disponíveis no sistema."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    ports = []
    for p in list_ports.comports():
        ports.append({
            "device": p.device,
            "description": p.description or "",
            "hwid": p.hwid or "",
        })
    return ports
