"""Preços de mercado para o monitor de regras.

Fontes (nenhuma exige chave):
  * yfinance  — ações B3, Ibovespa, cripto e pares de câmbio (USDBRL=X)
  * AwesomeAPI — fallback para dólar, sem chave e sem scraping

A resolução de símbolos é tolerante: "dolar", "USD", "USDBRL" e "USDBRL=X"
apontam para o mesmo par.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: Apelidos -> símbolo do yfinance.
APELIDOS: dict[str, str] = {
    "dolar": "USDBRL=X",
    "usd": "USDBRL=X",
    "usdbrl": "USDBRL=X",
    "dolar/comercial": "USDBRL=X",
    "euro": "EURBRL=X",
    "eurbrl": "EURBRL=X",
    "ibov": "^BVSP",
    "ibovespa": "^BVSP",
    "dowjones": "^DJI",
    "nasdaq": "^IXIC",
    "ouro": "GC=F",
    "ouro_ouro": "GC=F",
    "petroleo": "BZ=F",
    "wti": "CL=F",
    "btc": "BTC-USD",
    "bitcoin": "BTC-USD",
    "eth": "ETH-USD",
    "s&p500": "^GSPC",
    "s&p": "^GSPC",
}


@dataclass
class Cotacao:
    """Último preço conhecido de um instrumento."""

    simbolo: str
    preco: float | None
    variacao_dia: float | None = None
    fonte: str = ""
    timestamp: str = ""

    @property
    def valido(self) -> bool:
        return self.preco is not None and self.preco > 0


def resolver(ativo: str) -> str:
    """'dolar' -> 'USDBRL=X'; 'PETR4' -> 'PETR4.SA'."""
    alvo = (ativo or "").strip().lower()
    if alvo in APELIDOS:
        return APELIDOS[alvo]
    normalizado = re.sub(r"[^A-Za-z0-9^=.\-]", "", alvo.upper())
    if not normalizado:
        return ""
    if normalizado.endswith("=X") or normalizado.startswith("^"):
        return normalizado
    if "." in normalizado:
        return normalizado
    # Ticker da B3: 4 letras + classe (PETR4, ITSA4, TAEE11 -> TAEE11.SA)
    if re.fullmatch(r"[A-Z]{4}\d{1,2}", normalizado):
        return f"{normalizado}.SA"
    return normalizado


def _historico(simbolo: str) -> tuple[float | None, float | None]:
    """(último preço, variação %) via yfinance."""
    try:
        import yfinance as yf

        hist = yf.Ticker(simbolo).history(period="5d", auto_adjust=False)
        if hist is None or hist.empty:
            return None, None
        ultimo = float(hist["Close"].iloc[-1])
        variacao = None
        if len(hist) >= 2:
            base = float(hist["Close"].iloc[-2])
            if base:
                variacao = (ultimo / base - 1) * 100
        return ultimo, variacao
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance falhou para %s: %s", simbolo, exc)
        return None, None


def _awesomeapi(par: str) -> tuple[float | None, float | None]:
    """Fallback pela AwesomeAPI (sem chave).

    Cobre USD-BRL, EUR-BRL e, importante, cripto em Real (BTC-BRL, ETH-BRL) —
    que o Yahoo Finance não oferece.
    """
    try:
        import requests

        r = requests.get(
            f"https://economia.awesomeapi.com.br/json/last/{par}", timeout=12
        )
        r.raise_for_status()
        dados = r.json()[par]
        preco = float(dados["bid"])
        variacao = float(dados.get("pctChange", "0"))
        return preco, variacao
    except Exception as exc:  # noqa: BLE001
        log.warning("AwesomeAPI falhou para %s: %s", par, exc)
        return None, None


#: Pares da AwesomeAPI usados como fallback.
PARES_AWESOME = {"USDBRL=X": "USD-BRL", "EURBRL=X": "EUR-BRL"}


def cotacao(ativo: str) -> Cotacao:
    """Busca o preço atual, com fallback automático."""
    simbolo = resolver(ativo)
    if not simbolo:
        return Cotacao(simbolo="", preco=None)

    preco, variacao = _historico(simbolo)
    fonte = "yfinance"

    if preco is None:
        # Cripto em Real não existe no Yahoo: BTC-BRL, ETH-BRL...
        base = simbolo.replace("-USD", "-BRL")
        if base != simbolo:
            preco, variacao = _awesomeapi(base)
            fonte = "awesomeapi"

    if preco is None and simbolo in PARES_AWESOME:
        preco, variacao = _awesomeapi(PARES_AWESOME[simbolo])
        fonte = "awesomeapi"

    from datetime import datetime

    return Cotacao(
        simbolo=simbolo,
        preco=preco,
        variacao_dia=variacao,
        fonte=fonte if preco else "",
        timestamp=datetime.now().isoformat(timespec="seconds"),
    )
