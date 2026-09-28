"""Parsers de protocolos de balança.

Cada protocolo recebe uma "linha" (bytes já decodificados ou bytes crus) e
devolve um objeto :class:`WeightReading` com o peso normalizado, ou ``None``
quando a linha não contém uma leitura válida.

Protocolos suportados:

* ``generic``       – número ASCII simples (ex.: ``  12.345\r\n``);
* ``toledo``        – formato contínuo Mettler Toledo / Toledo;
* ``filizola``      – formato Filizola (comum no Brasil);
* ``cas``           – formato CAS;
* ``aandd``         – formato A&D / AND;
* ``custom_regex``  – expressão regular definida pelo usuário.

O parser devolve o valor já com casas decimais e multiplicador aplicados, mas
NÃO aplica tara — isso é responsabilidade do :class:`ScaleManager`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class WeightReading:
    """Representa uma leitura de peso normalizada."""

    weight: float          # valor bruto (sem tara), na unidade configurada
    raw: str               # linha original
    stable: bool = False   # flag de estabilidade reportada pelo equipamento
    overload: bool = False # flag de sobrecarga
    negative: bool = False # sinal negativo

    def as_dict(self) -> dict:
        return {
            "weight": self.weight,
            "raw": self.raw,
            "stable": self.stable,
            "overload": self.overload,
            "negative": self.negative,
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


class BaseProtocol:
    """Classe base para todos os protocolos."""

    name = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}

    def parse(self, raw: str) -> Optional[WeightReading]:
        raise NotImplementedError


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

        ST,GS,+  12.345 kg\r\n
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

        ST,GS,  12.345kg\r\n
        US,NT,   0.000kg\r\n
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

        ST,+0012.345 kg\r\n
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


# Mapa de protocolos disponíveis.
PROTOCOLS = {
    "generic": GenericProtocol,
    "toledo": ToledoProtocol,
    "filizola": FilizolaProtocol,
    "cas": CASProtocol,
    "aandd": AandDProtocol,
    "custom_regex": CustomRegexProtocol,
}


def build_protocol(cfg: dict) -> BaseProtocol:
    """Instancia o parser correto a partir da configuração de protocolo."""
    proto_type = (cfg or {}).get("type", "generic")
    cls = PROTOCOLS.get(proto_type, GenericProtocol)
    return cls(cfg)


def available_protocols() -> list:
    """Lista os protocolos disponíveis para exibição na interface."""
    return [
        {"id": "generic", "name": "Genérico (número ASCII)"},
        {"id": "toledo", "name": "Mettler Toledo / Toledo"},
        {"id": "filizola", "name": "Filizola"},
        {"id": "cas", "name": "CAS"},
        {"id": "aandd", "name": "A&D (AND)"},
        {"id": "custom_regex", "name": "Personalizado (Regex)"},
    ]
