"""Camada de conexão física com a balança.

Fornece duas implementações de :class:`BaseConnection`:

* :class:`SerialConnection` – porta serial RS232 (pyserial);
* :class:`TcpConnection`    – socket TCP/IP.

Ambas expõem uma interface comum de leitura por linha, com timeouts curtos
de leitura (para que o laço de leitura seja responsivo a comandos de
reconexão/parada) e fechamento não bloqueante.

Importante: a leitura **não** mantém o lock interno durante a operação de
I/O bloqueante. Isso evita que :meth:`close` fique travado esperando o lock
enquanto uma leitura está em andamento — problema que impedia a troca de
IP/porta em tempo de execução.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Optional

# Timeout máximo de leitura (segundos). Mantém o laço responsivo a
# reconexões sem consumir CPU (o recv/read retorna periodicamente).
READ_TIMEOUT = 0.5


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
        self._lock = threading.Lock()  # protege apenas o buffer

    def open(self) -> None:
        try:
            import serial  # import tardio: pyserial é opcional
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
        # Timeout de leitura curto para manter o laço responsivo.
        read_timeout = min(float(self.cfg.get("timeout", 1.0)), READ_TIMEOUT)
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
                timeout=read_timeout,
            )
        except Exception as exc:  # serial.SerialException e afins
            raise ConnectionError_(f"Falha ao abrir porta serial: {exc}") from exc

    def close(self) -> None:
        """Fecha a porta sem manter lock (não bloqueia leitura em andamento)."""
        ser = self._serial
        self._serial = None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    @property
    def is_open(self) -> bool:
        return self._serial is not None and getattr(self._serial, "is_open", False)

    def read_line(self) -> Optional[str]:
        ser = self._serial
        if ser is None:
            return None
        try:
            chunk = ser.read(256)
        except Exception as exc:
            # Se foi fechado intencionalmente, não é erro.
            if self._serial is None:
                return None
            raise ConnectionError_(f"Erro de leitura serial: {exc}") from exc
        if chunk:
            with self._lock:
                self._buffer.extend(chunk)
        return self._extract_line()

    def _extract_line(self) -> Optional[str]:
        with self._lock:
            buf = self._buffer
            term = self.terminator.encode(self.encoding, errors="ignore")
            idx = buf.find(term)
            if idx != -1:
                line = bytes(buf[:idx])
                del buf[: idx + len(term)]
                return self._decode(line)
            # Fallback: quebra por \n ou \r isolados.
            for single in (b"\n", b"\r"):
                idx2 = buf.find(single)
                if idx2 != -1:
                    line = bytes(buf[:idx2])
                    del buf[: idx2 + 1]
                    return self._decode(line)
            return None

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
        self._lock = threading.Lock()  # protege apenas o buffer

    def open(self) -> None:
        host = self.cfg.get("host", "127.0.0.1")
        port = int(self.cfg.get("port", 4001))
        connect_timeout = float(self.cfg.get("timeout", 3.0))
        try:
            self._sock = socket.create_connection(
                (host, port), timeout=connect_timeout
            )
            # Timeout de leitura curto para manter o laço responsivo.
            self._sock.settimeout(min(connect_timeout, READ_TIMEOUT))
        except Exception as exc:
            raise ConnectionError_(
                f"Falha ao conectar em {host}:{port} — {exc}"
            ) from exc

    def close(self) -> None:
        """Fecha o socket sem manter lock (não bloqueia recv em andamento)."""
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def read_line(self) -> Optional[str]:
        sock = self._sock
        if sock is None:
            return None
        try:
            chunk = sock.recv(256)
        except socket.timeout:
            # Sem dados dentro do timeout: conexão continua ativa.
            return None
        except OSError as exc:
            # Se foi fechado intencionalmente, não é erro.
            if self._sock is None:
                return None
            raise ConnectionError_(f"Erro de leitura TCP: {exc}") from exc

        if chunk == b"":
            # EOF: o equipamento encerrou a conexão.
            if self._sock is None:
                return None
            raise ConnectionError_("Conexão TCP encerrada pelo equipamento")

        with self._lock:
            self._buffer.extend(chunk)
        return self._extract_line()

    def _extract_line(self) -> Optional[str]:
        with self._lock:
            buf = self._buffer
            term = self.terminator.encode(self.encoding, errors="ignore")
            idx = buf.find(term)
            if idx != -1:
                line = bytes(buf[:idx])
                del buf[: idx + len(term)]
                return self._decode(line)
            for single in (b"\n", b"\r"):
                idx2 = buf.find(single)
                if idx2 != -1:
                    line = bytes(buf[:idx2])
                    del buf[: idx2 + 1]
                    return self._decode(line)
            return None

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
