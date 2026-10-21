"""Cliente yfinance: cotação, nome da empresa, histórico e proventos.

Complementa o Fundamentus (que é a fonte primária de fundamentalistas) com dados
de mercado que o Fundamentus não entrega bem: série de preços e histórico de
dividendos. Toda chamada é tolerante a falha: devolve None em vez de quebrar,
porque o Fundamentus já cobre o essencial.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..util import ticker_yahoo
from .cache import Cache

log = logging.getLogger(__name__)

#: Sobe quando o formato de parsing muda, para invalidar o cache antigo.
VERSAO_CACHE = "v2"


@dataclass
class Proventos:
    """Proventos dos últimos 12 meses.

    ATENÇÃO: yfinance devolve os dividendos **por ação**, não o total em R$.
    O total é calculado em src/analise.py cruzando com o nº de ações do Fundamentus.
    """

    por_acao_12m: float | None = None
    n_pagamentos: int | None = None
    ultimo: str | None = None
    pagamentos: list[dict] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.pagamentos is None:
            self.pagamentos = []


class YahooClient:
    def __init__(self, cache: Cache | None = None, usar_cache: bool = True):
        self.cache = cache or Cache()
        self.usar_cache = usar_cache
        self._cache_precos: dict[str, pd.DataFrame] = {}

    @staticmethod
    def _ticker(ticker: str):
        import yfinance as yf

        return yf.Ticker(ticker_yahoo(ticker))

    # ------------------------------------------------------------------ perfil
    def perfil(self, ticker: str) -> dict[str, Any]:
        """Nome, setor, preço atual e market cap. Vazio em caso de falha."""
        chave = ticker_yahoo(ticker)
        if self.usar_cache:
            guardado = self.cache.get("yahoo_perfil", f"{VERSAO_CACHE}:{chave}")
            if guardado is not None:
                return guardado

        dados: dict[str, Any] = {}
        try:
            info = self._ticker(ticker).info or {}
            dados = {
                "nome": info.get("longName") or info.get("shortName"),
                "setor": info.get("sector"),
                "industria": info.get("industry"),
                "cotacao": info.get("currentPrice") or info.get("regularMarketPrice"),
                "valor_mercado": info.get("marketCap"),
                "dividend_yield": info.get("dividendYield"),
                "trailing_eps": info.get("trailingEps"),
                "beta": info.get("beta"),
            }
        except Exception as exc:  # noqa: BLE001 - yfinance lança vários tipos
            log.warning("yfinance.perfil falhou para %s: %s", chave, exc)

        if self.usar_cache:
            self.cache.set("yahoo_perfil", chave, dados)
        return dados

    # --------------------------------------------------------------- histórico
    def historico(self, ticker: str, periodo: str = "1y") -> pd.DataFrame:
        """Série OHLCV. Retorna DataFrame vazio em caso de falha."""
        chave = ticker_yahoo(ticker)
        if chave in self._cache_precos:
            return self._cache_precos[chave]

        if self.usar_cache:
            guardado = self.cache.get("yahoo_historico", f"{chave}|{periodo}")
            if guardado is not None:
                df = pd.DataFrame(guardado)
                if not df.empty:
                    df.index = pd.to_datetime(df.index)
                    self._cache_precos[chave] = df
                return df

        df = pd.DataFrame()
        try:
            df = self._ticker(ticker).history(period=periodo, auto_adjust=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("yfinance.historico falhou para %s: %s", chave, exc)

        if self.usar_cache and not df.empty:
            self.cache.set(
                "yahoo_historico",
                f"{chave}|{periodo}",
                df.reset_index().to_dict(orient="records"),
            )
        self._cache_precos[chave] = df
        return df

    def performance(self, ticker: str) -> dict[str, float | None]:
        """Retorno da preço em 1d, 1m, 3m, 6m, 1a e no ano."""
        df = self.historico(ticker, periodo="2y")
        if df.empty:
            return {}

        preco_hoje = float(df["Close"].iloc[-1])
        indexes = df.index

        def _retorno(dias: int) -> float | None:
            if len(indexes) <= dias:
                return None
            alvo = indexes[-1 - dias]
            base = float(df.loc[alvo, "Close"])
            return (preco_hoje / base - 1) * 100 if base else None

        ano = indexes[-1].year
        try:
            base_ano = float(df[df.index.year == ano]["Close"].iloc[0])
            var_ano = (preco_hoje / base_ano - 1) * 100
        except (IndexError, ValueError):
            var_ano = None

        return {
            "dia": _retorno(1),
            "semana": _retorno(5),
            "mes": _retorno(21),
            "3meses": _retorno(63),
            "6meses": _retorno(126),
            "12meses": _retorno(252),
            "ano": var_ano,
        }

    # ---------------------------------------------------------------- proventos
    def proventos(self, ticker: str) -> Proventos:
        """Dividendos dos últimos 12 meses.

        Não calcula payout aqui: o denominador correto (nº de ações) vem do
        Fundamentus, e o lucro por ação também. Ver src/analise.py.
        """
        chave = ticker_yahoo(ticker)
        if self.usar_cache:
            guardado = self.cache.get("yahoo_proventos", f"{VERSAO_CACHE}:{chave}")
            if guardado is not None:
                return Proventos(**guardado)

        resultado = Proventos()
        try:
            t = self._ticker(ticker)
            div = t.dividends
            if div is not None and not div.empty:
                agora = pd.Timestamp.now(tz=div.index.tz) if div.index.tz else pd.Timestamp.now()
                janela = div[div.index >= (agora - pd.Timedelta(days=365))]
                if not janela.empty:
                    resultado.por_acao_12m = float(janela.sum())
                    resultado.n_pagamentos = int(len(janela))
                    resultado.ultimo = f"{janela.index[-1].date()} — R$ {float(janela.iloc[-1]):.2f}"
                    resultado.pagamentos = [
                        {"data": str(idx.date()), "valor": float(val)}
                        for idx, val in janela.items()
                    ]
        except Exception as exc:  # noqa: BLE001
            log.warning("yfinance.proventos falhou para %s: %s", chave, exc)

        if self.usar_cache:
            self.cache.set("yahoo_proventos", chave, resultado.__dict__)
        return resultado
