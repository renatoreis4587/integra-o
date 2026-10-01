"""Parsers de protocolos de balança.

Cada protocolo recebe dados do equipamento e devolve objetos
:class:`WeightReading` com o peso normalizado.

Arquitetura
-----------
Há dois níveis:

* :meth:`BaseProtocol.feed` — recebe **bytes crus** e é responsável pelo
  *framing* (separar mensagens). O comportamento padrão é dividir por linha
  (terminador configurável). Protocolos **binários** (ex.: Mettler Toledo
  TI400 P03) sobrescrevem :meth:`feed` para delimitar por ``STX`` + tamanho
  fixo.
* :meth:`BaseProtocol.parse` / :meth:`BaseProtocol.parse_bytes` — interpretam
  **uma** mensagem e devolvem um :class:`WeightReading` (ou ``None``).

Protocolos de texto suportados:

* ``generic``       – número ASCII simples (ex.: ``  12.345\\r\\n``);
* ``toledo``        – formato contínuo Mettler Toledo / Toledo
                      (ex.: ``ST,GS,+  12.345 kg``);
* ``filizola``      – formato Filizola (comum no Brasil);
* ``cas``           – formato CAS;
* ``aandd``         – formato A&D / AND;
* ``custom_regex``  – expressão regular definida pelo usuário.

Protocolos Mettler Toledo **TI400**:

* ``ti400_p03``  – frame binário do socket de rede (Ethernet/WiFi) e da
                   porta serial P03 (``STX SWA SWB SWC IIIIII TTTTTT CR CS``);
* ``ti400_p10``  – string editável P10/P11 (``<STX>Plataforma <peso> LPFEZKp …``);
* ``ti400_p08``  – P08/P08A (``S  09.076 kg``);
* ``ti400_auto`` – detecção automática (tenta P03 binário e, em seguida,
                   os formatos de texto TI400).

O parser devolve o valor já com casas decimais e multiplicador aplicados, mas
NÃO aplica tara — isso é responsabilidade do :class:`ScaleManager`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class WeightReading:
    """Representa uma leitura de peso normalizada."""

    weight: float          # valor bruto (sem tara), na unidade configurada
    raw: str               # linha/mensagem original (texto ou hex)
    stable: bool = False   # flag de estabilidade reportada pelo equipamento
    overload: bool = False # flag de sobrecarga
    negative: bool = False # sinal negativo
    tare: float = 0.0      # tara informada pelo equipamento (quando houver)
    net: bool = False      # True se o peso exibido é líquido

    def as_dict(self) -> dict:
        return {
            "weight": self.weight,
            "raw": self.raw,
            "stable": self.stable,
            "overload": self.overload,
            "negative": self.negative,
            "tare": self.tare,
            "net": self.net,
        }


def _to_float(text: str, decimal_places: int, multiplier: float) -> Optional[float]:
    """Converte texto em float, tratando vírgula/ponto e casas decimais."""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    # Remove espaços e caracteres não numéricos comuns.
    text = text.replace(" ", "")
    # Se houver vírgula E ponto, o último separador é o decimal.
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        return None
    if decimal_places > 0:
        value = value / (10 ** decimal_places)
    return value * multiplier


def hexdump(data: bytes) -> str:
    """Representação legível (hex + ASCII) de um bloco de bytes."""
    hexpart = " ".join(f"{b:02X}" for b in data)
    asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return f"{hexpart}  |{asciipart}|"


# Compatibilidade interna.
_hexdump = hexdump


class BaseProtocol:
    """Classe base para todos os protocolos."""

    name = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self.encoding = self.cfg.get("encoding") or "ascii"
        self.terminator = self.cfg.get("line_terminator") or "\r\n"
        self._buf = bytearray()

    # -- Framing -----------------------------------------------------------
    def feed(self, data: bytes) -> List[WeightReading]:
        """Recebe bytes crus, separa mensagens (framing por linha) e parseia."""
        if data:
            self._buf.extend(data)
        readings: List[WeightReading] = []
        while True:
            line = self._extract_line()
            if line is None:
                break
            reading = self.parse(line)
            if reading is not None:
                readings.append(reading)
        return readings

    def _extract_line(self) -> Optional[str]:
        buf = self._buf
        term = self.terminator.encode(self.encoding, errors="ignore")
        idx = buf.find(term)
        if idx != -1:
            line = bytes(buf[:idx])
            del buf[: idx + len(term)]
            return line.decode(self.encoding, errors="ignore").strip()
        # Fallback: quebra por \n ou \r isolados.
        for single in (b"\n", b"\r"):
            idx2 = buf.find(single)
            if idx2 != -1:
                line = bytes(buf[:idx2])
                del buf[: idx2 + 1]
                return line.decode(self.encoding, errors="ignore").strip()
        return None

    # -- Parsing -----------------------------------------------------------
    def parse(self, raw: str) -> Optional[WeightReading]:
        raise NotImplementedError

    def parse_bytes(self, data: bytes) -> Optional[WeightReading]:
        """Interpreta uma mensagem binária. Padrão: decodifica e chama parse()."""
        text = data.decode(self.encoding, errors="ignore").strip()
        return self.parse(text)


class GenericProtocol(BaseProtocol):
    """Extrai o primeiro número da linha (ASCII simples)."""

    name = "generic"

    def parse(self, raw: str) -> Optional[WeightReading]:
        if not raw:
            return None
        dp = int(self.cfg.get("decimal_places", 0))
        mult = float(self.cfg.get("multiplier", 1.0))
        neg_char = self.cfg.get("negative_char", "")
        match = re.search(r"[-+]?\d+[\.,]?\d*", raw)
        if not match:
            return None
        token = match.group(0)
        negative = token.startswith("-")
        if neg_char and neg_char in raw:
            negative = True
        value = _to_float(token, dp, mult)
        if value is None:
            return None
        if negative:
            value = -abs(value)
        stable = ("ST" in raw.upper()) or ("EST" in raw.upper())
        overload = ("OL" in raw.upper()) or ("OVR" in raw.upper())
        return WeightReading(
            weight=value, raw=raw, stable=stable,
            overload=overload, negative=negative,
        )


class ToledoProtocol(BaseProtocol):
    """Protocolo contínuo Mettler Toledo / Toledo.

    Formato típico::

        ST,GS,+  12.345 kg\\r\\n
        <status>,<status>,<sinal><espaços><peso><unidade>

    O primeiro campo indica estabilidade (ST = estável, US = instável) e o
    segundo o tipo de peso (GS = bruto, NT = líquido).
    """

    name = "toledo"

    def parse(self, raw: str) -> Optional[WeightReading]:
        if not raw:
            return None
        text = raw.strip()
        parts = [p.strip() for p in text.split(",")]
        stable = False
        overload = False
        if len(parts) >= 2:
            status = parts[0].upper()
            stable = status.startswith("ST")
            overload = "OL" in text.upper()
            weight_part = parts[-1]
        else:
            weight_part = text
        dp = int(self.cfg.get("decimal_places", 0))
        mult = float(self.cfg.get("multiplier", 1.0))
        match = re.search(r"[-+]?\d+[\.,]?\d*", weight_part)
        if not match:
            return None
        token = match.group(0)
        negative = token.startswith("-") or "-" in weight_part[: match.start()]
        value = _to_float(token, dp, mult)
        if value is None:
            return None
        if negative:
            value = -abs(value)
        return WeightReading(
            weight=value, raw=raw, stable=stable,
            overload=overload, negative=negative,
        )


class FilizolaProtocol(BaseProtocol):
    """Protocolo Filizola (comum no Brasil).

    Formato típico (7 caracteres de peso + status)::

        0001234<CR>          (peso inteiro, 4 casas decimais implícitas)
        ou com sinal/estabilidade em campos separados.
    """

    name = "filizola"

    def parse(self, raw: str) -> Optional[WeightReading]:
        if not raw:
            return None
        text = raw.strip()
        dp = int(self.cfg.get("decimal_places", 0))
        mult = float(self.cfg.get("multiplier", 1.0))
        negative = text.startswith("-")
        match = re.search(r"[-+]?\d+[\.,]?\d*", text)
        if not match:
            return None
        value = _to_float(match.group(0), dp, mult)
        if value is None:
            return None
        if negative:
            value = -abs(value)
        stable = True  # Filizola normalmente só envia quando estável.
        return WeightReading(
            weight=value, raw=raw, stable=stable,
            overload=False, negative=negative,
        )


class CASProtocol(BaseProtocol):
    """Protocolo CAS.

    Formato típico::

        ST,GS,  12.345kg\\r\\n
        US,NT,   0.000kg\\r\\n
    """

    name = "cas"

    def parse(self, raw: str) -> Optional[WeightReading]:
        if not raw:
            return None
        text = raw.strip()
        stable = text.upper().startswith("ST")
        overload = "OL" in text.upper()
        dp = int(self.cfg.get("decimal_places", 0))
        mult = float(self.cfg.get("multiplier", 1.0))
        match = re.search(r"[-+]?\d+[\.,]?\d*", text)
        if not match:
            return None
        token = match.group(0)
        negative = token.startswith("-")
        value = _to_float(token, dp, mult)
        if value is None:
            return None
        if negative:
            value = -abs(value)
        return WeightReading(
            weight=value, raw=raw, stable=stable,
            overload=overload, negative=negative,
        )


class AandDProtocol(BaseProtocol):
    """Protocolo A&D (AND).

    Formato típico::

        ST,+0012.345 kg\\r\\n
    """

    name = "aandd"

    def parse(self, raw: str) -> Optional[WeightReading]:
        if not raw:
            return None
        text = raw.strip()
        stable = text.upper().startswith("ST")
        overload = "OL" in text.upper()
        dp = int(self.cfg.get("decimal_places", 0))
        mult = float(self.cfg.get("multiplier", 1.0))
        match = re.search(r"[-+]?\d+[\.,]?\d*", text)
        if not match:
            return None
        token = match.group(0)
        negative = token.startswith("-")
        value = _to_float(token, dp, mult)
        if value is None:
            return None
        if negative:
            value = -abs(value)
        return WeightReading(
            weight=value, raw=raw, stable=stable,
            overload=overload, negative=negative,
        )


class CustomRegexProtocol(BaseProtocol):
    """Protocolo definido pelo usuário via expressão regular.

    A regex deve conter o grupo nomeado ``weight``. Opcionalmente pode conter
    ``unit`` e ``stable``. Exemplo::

        (?P<weight>[-+]?\\d+[\\.,]?\\d*)\\s*(?P<unit>kg|g)
    """

    name = "custom_regex"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        pattern = self.cfg.get("regex") or r"(?P<weight>[-+]?\d+[\.,]?\d*)"
        try:
            self._regex = re.compile(pattern)
        except re.error:
            self._regex = re.compile(r"(?P<weight>[-+]?\d+[\.,]?\d*)")

    def parse(self, raw: str) -> Optional[WeightReading]:
        if not raw:
            return None
        match = self._regex.search(raw)
        if not match:
            return None
        groups = match.groupdict()
        token = groups.get("weight")
        if token is None:
            # Sem grupo nomeado: usa o grupo 1 ou o match inteiro.
            token = match.group(1) if match.groups() else match.group(0)
        dp = int(self.cfg.get("decimal_places", 0))
        mult = float(self.cfg.get("multiplier", 1.0))
        value = _to_float(token, dp, mult)
        if value is None:
            return None
        negative = value < 0
        stable = bool(groups.get("stable")) if "stable" in groups else False
        return WeightReading(
            weight=value, raw=raw, stable=stable,
            overload=False, negative=negative,
        )


# ---------------------------------------------------------------------------
# Mettler Toledo TI400
# ---------------------------------------------------------------------------

# Posição decimal codificada nos bits 0-2 da STATUS WORD "A" (SWA) do P03.
_TI400_P03_DECIMAL = {
    0b001: 10.0,
    0b010: 1.0,
    0b011: 0.1,
    0b100: 0.01,
    0b101: 0.001,
    0b110: 0.0001,
}


class Ti400P03Protocol(BaseProtocol):
    """Protocolo P03 do Mettler Toledo TI400 (socket de rede / serial P03).

    Frame binário de tamanho fixo::

        STX  SWA  SWB  SWC  IIIIII  TTTTTT  CR  CS
        1B   1B   1B   1B   6B      6B      1B  1B   = 18 bytes

    * ``STX`` = 0x02 (início de texto);
    * ``SWA`` = status word A — bits 0-2 codificam a posição decimal;
    * ``SWB`` = status word B — bit0 líquido, bit1 negativo, bit2 sobrecarga,
      bit3 em movimento (instável);
    * ``SWC`` = status word C;
    * ``IIIIII`` = peso indicado no display (6 dígitos ASCII);
    * ``TTTTTT`` = tara (6 dígitos ASCII);
    * ``CR`` = 0x0D;
    * ``CS`` = byte de checksum.

    O socket de rede do TI400 transmite este protocolo **continuamente**.
    """

    name = "ti400_p03"

    def feed(self, data: bytes) -> List[WeightReading]:
        if data:
            self._buf.extend(data)
        readings: List[WeightReading] = []
        while True:
            frame = self._extract_frame()
            if frame is None:
                break
            reading = self.parse_bytes(frame)
            if reading is not None:
                readings.append(reading)
        return readings

    def _extract_frame(self) -> Optional[bytes]:
        buf = self._buf
        length = int(self.cfg.get("frame_length", 18))
        idx = buf.find(0x02)  # STX
        if idx == -1:
            # Sem STX: descarta lixo, mantendo o buffer pequeno.
            if len(buf) > 4096:
                del buf[:-1]
            return None
        if idx > 0:
            del buf[:idx]  # descarta bytes antes do STX
        if len(buf) < length:
            return None
        frame = bytes(buf[:length])
        del buf[:length]
        return frame

    def parse_bytes(self, data: bytes) -> Optional[WeightReading]:
        if not data or len(data) < 18 or data[0] != 0x02:
            return None
        swa, swb, swc = data[1], data[2], data[3]
        w_field = data[4:10]
        t_field = data[10:16]
        w_text = w_field.decode("ascii", errors="ignore")
        t_text = t_field.decode("ascii", errors="ignore")

        negative = bool(swb & 0x02)
        overload = bool(swb & 0x04)
        motion = bool(swb & 0x08)
        is_net = bool(swb & 0x01)

        mult = float(self.cfg.get("multiplier", 1.0))

        # Peso: se o campo já contém separador decimal, usa direto; senão,
        # aplica a posição decimal codificada em SWA.
        if ("." in w_text) or ("," in w_text):
            value = _to_float(w_text, 0, mult)
        else:
            digits = re.sub(r"[^0-9]", "", w_text)
            if not digits:
                return None
            factor = _TI400_P03_DECIMAL.get(swa & 0x07)
            if factor is None:
                # SWA inválido: usa o fallback de casas decimais configurado.
                value = _to_float(w_text, int(self.cfg.get("decimal_places", 0)), mult)
            else:
                value = int(digits) * factor * mult
        if value is None:
            return None
        if negative:
            value = -abs(value)

        # Tara (informativa).
        tare = 0.0
        t_digits = re.sub(r"[^0-9]", "", t_text)
        if t_digits:
            if ("." in t_text) or ("," in t_text):
                tare = _to_float(t_text, 0, 1.0) or 0.0
            else:
                factor = _TI400_P03_DECIMAL.get(swa & 0x07)
                if factor is not None:
                    tare = int(t_digits) * factor

        return WeightReading(
            weight=value,
            raw=_hexdump(data),
            stable=not motion,
            overload=overload,
            negative=negative,
            tare=tare,
            net=is_net,
        )


class Ti400P10Protocol(BaseProtocol):
    """Protocolo P10/P11 do TI400 (string editável, ASCII).

    Exemplo::

        <STX>Plataforma 0,269 LPFEZKp 0,627 0,358 <CR><CS>

    O peso exibido aparece logo antes do bloco de status ``LPFEZKp``:

    * ``L`` = Bruto (B) ou Líquido (L);
    * ``P`` = Positivo (P) ou Negativo (N);
    * ``F`` = Na faixa (F) ou fora/sobrecarga (A);
    * ``E`` = Estável (E) ou Instável (I);
    * ``Z`` = Zero capturado (Z) ou não (n);
    * ``K`` = kg (K) ou lb (L);
    * ``p`` = Demanda (p) ou Contínuo (*).
    """

    name = "ti400_p10"

    _DEFAULT = (
        r"(?P<weight>[-+]?\d+(?:[\.,]\d+)?)\s*"
        r"(?P<status>[BL][PN][FA][EI][Zn][KL][p*])"
    )

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        pattern = self.cfg.get("ti400_p10_regex") or self._DEFAULT
        try:
            self._regex = re.compile(pattern)
        except re.error:
            self._regex = re.compile(self._DEFAULT)

    def parse(self, raw: str) -> Optional[WeightReading]:
        if not raw:
            return None
        # Remove caracteres de controle (STX, CR, checksum).
        text = "".join(ch for ch in raw if 32 <= ord(ch) < 127)
        match = self._regex.search(text)
        if not match:
            return None
        groups = match.groupdict()
        value = _to_float(groups.get("weight"), 0,
                          float(self.cfg.get("multiplier", 1.0)))
        if value is None:
            return None
        status = groups.get("status") or ""
        stable = len(status) >= 4 and status[3] == "E"
        overload = len(status) >= 3 and status[2] == "A"
        negative = (value < 0) or (len(status) >= 2 and status[1] == "N")
        if negative:
            value = -abs(value)
        return WeightReading(
            weight=value, raw=text, stable=stable,
            overload=overload, negative=negative,
        )


class Ti400P08Protocol(BaseProtocol):
    """Protocolo P08/P08A do TI400 (ASCII, sob demanda).

    Formatos::

        S   09.076 kg<CR><LF>   (peso estável)
        S I -<CR><LF>           (abaixo da capacidade)
        S I +<CR><LF>           (acima da capacidade)
        S I<CR><LF>             (peso instável)
    """

    name = "ti400_p08"

    _DEFAULT = r"S\s+(?P<weight>[-+]?\d+(?:[\.,]\d+)?)\s*kg"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        pattern = self.cfg.get("ti400_p08_regex") or self._DEFAULT
        try:
            self._regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            self._regex = re.compile(self._DEFAULT, re.IGNORECASE)

    def parse(self, raw: str) -> Optional[WeightReading]:
        if not raw:
            return None
        text = "".join(ch for ch in raw if 32 <= ord(ch) < 127).strip()
        if not text:
            return None
        match = self._regex.search(text)
        if match:
            value = _to_float(match.group("weight"), 0,
                              float(self.cfg.get("multiplier", 1.0)))
            if value is None:
                return None
            return WeightReading(
                weight=value, raw=text, stable=True,
                overload=False, negative=value < 0,
            )
        # Sem número: pode ser estado instável/sobrecarga.
        if text.upper().startswith("S"):
            upper = text.upper()
            overload = "+" in upper
            return WeightReading(
                weight=0.0, raw=text, stable=False,
                overload=overload, negative=False,
            )
        return None


class Ti400AutoProtocol(BaseProtocol):
    """Detecção automática para o TI400.

    Tenta primeiro o frame binário P03 (se houver ``STX``) e, em seguida,
    interpreta o restante como texto, tentando P10, P08 e, por fim, um
    extrator genérico de número.
    """

    name = "ti400_auto"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._p03 = Ti400P03Protocol(cfg)
        self._p10 = Ti400P10Protocol(cfg)
        self._p08 = Ti400P08Protocol(cfg)
        self._generic = GenericProtocol(cfg)

    def feed(self, data: bytes) -> List[WeightReading]:
        if data:
            self._buf.extend(data)
        readings: List[WeightReading] = []

        # 1) Frames binários P03 (STX + 18 bytes, com CR na posição 16).
        while True:
            idx = self._buf.find(0x02)
            if idx == -1:
                break
            if idx > 0:
                del self._buf[:idx]  # descarta lixo antes do STX
            if len(self._buf) < 18:
                # Possível frame P03 incompleto: espera mais dados para não
                # confundir com texto.
                return readings
            if self._buf[16] == 0x0D:  # CR na posição esperada => P03
                frame = bytes(self._buf[:18])
                reading = self._p03.parse_bytes(frame)
                if reading is not None:
                    del self._buf[:18]
                    readings.append(reading)
                    continue
            # Não é P03: descarta o STX e continua procurando.
            del self._buf[:1]

        # 2) Texto (P10 / P08 / genérico). Aqui não há STX pendente.
        while True:
            line = self._extract_line()
            if line is None:
                break
            reading = (
                self._p10.parse(line)
                or self._p08.parse(line)
                or self._generic.parse(line)
            )
            if reading is not None:
                readings.append(reading)
        return readings


# Mapa de protocolos disponíveis.
PROTOCOLS = {
    "generic": GenericProtocol,
    "toledo": ToledoProtocol,
    "filizola": FilizolaProtocol,
    "cas": CASProtocol,
    "aandd": AandDProtocol,
    "custom_regex": CustomRegexProtocol,
    "ti400_p03": Ti400P03Protocol,
    "ti400_p10": Ti400P10Protocol,
    "ti400_p08": Ti400P08Protocol,
    "ti400_auto": Ti400AutoProtocol,
}


def build_protocol(cfg: dict) -> BaseProtocol:
    """Instancia o parser correto a partir da configuração de protocolo."""
    proto_type = (cfg or {}).get("type", "generic")
    cls = PROTOCOLS.get(proto_type, GenericProtocol)
    return cls(cfg)


def available_protocols() -> list:
    """Lista os protocolos disponíveis para exibição na interface."""
    return [
        {"id": "ti400_auto", "name": "Mettler Toledo TI400 — Automático (recomendado)"},
        {"id": "ti400_p03", "name": "Mettler Toledo TI400 — P03 (rede/TCP e serial)"},
        {"id": "ti400_p10", "name": "Mettler Toledo TI400 — P10 (string editável)"},
        {"id": "ti400_p08", "name": "Mettler Toledo TI400 — P08/P08A"},
        {"id": "generic", "name": "Genérico (número ASCII)"},
        {"id": "toledo", "name": "Mettler Toledo / Toledo (ST,GS,+…)"},
        {"id": "filizola", "name": "Filizola"},
        {"id": "cas", "name": "CAS"},
        {"id": "aandd", "name": "A&D (AND)"},
        {"id": "custom_regex", "name": "Personalizado (Regex)"},
    ]
