"""Utilidades de formatação e parsing (números em padrão brasileiro)."""

from __future__ import annotations

import re
from typing import Any

_RE_NUM_BR = re.compile(r"-?\d{1,3}(?:\.\d{3})*(?:,\d+)?|-?\d+(?:,\d+)?")
_RE_APENAS_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def parse_br(valor: Any) -> float | None:
    """Converte texto numérico pt-BR ('1.279.020.000.000', '58,21', '-0,57%') em float.

    Retorna None quando não há número interpretável.
    """
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor) if valor == valor else None  # NaN -> None

    texto = str(valor).strip()
    if not texto:
        return None

    negativo = texto.startswith("-") or (texto.startswith("(") and texto.endswith(")"))

    limpo = texto.replace("R$", "").replace("%", "").replace("+", "").replace(" ", "")
    limpo = re.sub(r"[^\d,.\-]", "", limpo)
    if not limpo or limpo in {"-", ".", ","}:
        return None

    # Milhar com ponto + decimal com vírgula  ->  remove pontos, troca vírgula
    if "," in limpo and "." in limpo:
        if limpo.rfind(",") > limpo.rfind("."):
            limpo = limpo.replace(".", "").replace(",", ".")
        else:
            limpo = limpo.replace(",", "")
    elif "," in limpo:
        limpo = limpo.replace(",", ".")
    elif limpo.count(".") > 1:
        # Só separadores de milhar, sem parte decimal: "1.279.020.000.000"
        limpo = limpo.replace(".", "")
    elif limpo.count(".") == 1 and len(limpo.split(".")[1]) == 3:
        # "1.279" com 3 casas após o ponto é separador de milhar, não decimal.
        limpo = limpo.replace(".", "")

    try:
        numero = float(limpo)
    except ValueError:
        return None

    return -numero if negativo and numero > 0 else numero


def parse_percent(valor: Any) -> float | None:
    """Interpreta '6,3%' como 6.3 (percentual em ponto flutuante, não fração)."""
    numero = parse_br(valor)
    return numero


def parse_numero_curto(valor: Any) -> str:
    """Formata 1_279_020_000_000 como '1,28 tri' (escala brasileira)."""
    numero = parse_br(valor)
    if numero is None:
        return "n/d"
    absoluto = abs(numero)
    if absoluto >= 1_000_000_000_000:
        return f"{numero / 1_000_000_000_000:,.2f} tri".replace(".", ",")
    if absoluto >= 1_000_000_000:
        return f"{numero / 1_000_000_000:,.2f} bi".replace(".", ",")
    if absoluto >= 1_000_000:
        return f"{numero / 1_000_000:,.2f} mi".replace(".", ",")
    if absoluto >= 1_000:
        return f"{numero / 1_000:,.1f} mil".replace(".", ",")
    return f"{numero:,.2f}".replace(".", ",")


def formatar_pct(valor: Any, casas: int = 1) -> str:
    numero = parse_br(valor)
    if numero is None:
        return "n/d"
    return f"{numero:.{casas}f}%".replace(".", ",")


def formatar_num(valor: Any, casas: int = 2) -> str:
    numero = parse_br(valor)
    if numero is None:
        return "n/d"
    return f"{numero:,.{casas}f}".replace(",", "@").replace(".", ",").replace("@", ".")


def limpar_label(texto: str) -> str:
    """Remove os caracteres decorativos (?) e espaços extras dos rótulos do Fundamentus."""
    limpo = str(texto).replace("\ufffd", "").replace("?", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", limpo).strip()


def ticker_yahoo(ticker: str) -> str:
    """'PETR4' -> 'PETR4.SA' (B3). Others pass through untouched."""
    t = ticker.strip().upper().replace(" ", "")
    if t.endswith(".SA") or "." in t:
        return t
    return f"{t}.SA"
