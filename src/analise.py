"""Orquestrador: triagem -> contexto da empresa -> parecer da IA.

É o único ponto que as outras camadas conhecem. A API (FastAPI) e a CLI
consomem apenas estas funções.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .dados import Cache, FundamentusClient, YahooClient
from .ia import ClienteGemini, ErroIA, construir_prompt, schema_relatorio
from .ia.prompt import INSTRUCAO_SISTEMA
from .screen import PERFIS_TELA, graham_number, montar_ranking, obter_perfil, resumo_colunas

log = logging.getLogger(__name__)

#: Perfis de tela que não têm um foco de IA próprio.
PERFIL_IA_PADRAO: dict[str, str] = {
    "graham": "valor",
    "bazin": "dividendario",
}


@dataclass
class ContextoEmpresa:
    """Todos os números conhecidos sobre uma empresa, prontos para o prompt."""

    ticker: str
    nome: str | None = None
    tipo: str = "Ação"
    tipo_papel: str | None = None
    setor: str | None = None
    subsetor: str | None = None
    cotacao: float | None = None
    valor_mercado: float | None = None
    ultimo_balanco: str | None = None
    nro_acoes: float | None = None
    valor_firma: float | None = None
    indicadores: dict[str, Any] = field(default_factory=dict)
    balanco: dict[str, Any] = field(default_factory=dict)
    dre_anual: dict[str, Any] = field(default_factory=dict)
    dre_trimestral: dict[str, Any] = field(default_factory=dict)
    proventos: dict[str, Any] = field(default_factory=dict)
    performance: dict[str, Any] = field(default_factory=dict)
    criterios_tela: list[str] = field(default_factory=list)
    notas: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "nome": self.nome,
            "tipo": self.tipo,
            "tipo_papel": self.tipo_papel,
            "setor": self.setor,
            "subsetor": self.subsetor,
            "cotacao": self.cotacao,
            "valor_mercado": self.valor_mercado,
            "ultimo_balanco": self.ultimo_balanco,
            "nro_acoes": self.nro_acoes,
            "valor_firma": self.valor_firma,
            "indicadores": self.indicadores,
            "balanco": self.balanco,
            "dre_anual": self.dre_anual,
            "dre_trimestral": self.dre_trimestral,
            "proventos": self.proventos,
            "performance": self.performance,
            "criterios_tela": self.criterios_tela,
            "notas": self.notas,
        }


class Analista:
    def __init__(self, usar_cache: bool = True, cliente_ia: ClienteGemini | None = None):
        self.cache = Cache()
        self.fundamentus = FundamentusClient(self.cache, usar_cache=usar_cache)
        self.yahoo = YahooClient(self.cache, usar_cache=usar_cache)
        self._cliente_ia = cliente_ia

    # -------------------------------------------------------------------- IA
    @property
    def cliente_ia(self) -> ClienteGemini:
        if self._cliente_ia is None:
            self._cliente_ia = ClienteGemini()
        return self._cliente_ia

    # ---------------------------------------------------------------- varredura
    def varredura(
        self,
        perfil_chave: str = "qualidade",
        limite: int = 50,
        incluir_fiis: bool = False,
        enriquecer: bool = True,
    ) -> dict[str, Any]:
        """Varre a B3 aplicando o perfil de triagem. Retorna dados já serializáveis."""
        perfil = obter_perfil(perfil_chave)
        # Uma única requisição traz a B3 inteira; o filtro roda localmente em pandas
        # (escala exata, sem rate limit do Fundamentus).
        df = self.fundamentus.mercado_completo()
        ranking = montar_ranking(df, perfil)

        if enriquecer:
            # head() devolve uma cópia: enriquece a cópia e traz de volta por merge.
            topo = ranking.head(min(limite, 40)).copy()
            self._enriquecer(topo)
            extras = topo[["ticker", "nome", "tipo_papel", "eh_fii", "setor"]]
            ranking = ranking.merge(extras, on="ticker", how="left")
            ranking["eh_fii"] = ranking["eh_fii"].fillna(False).astype(bool)
            for coluna in ["nome", "tipo_papel", "setor"]:
                if coluna in ranking.columns:
                    ranking[coluna] = ranking[coluna].where(ranking[coluna].notna(), None)

        # Unidades de FII (tipo "ON NM") não são comparáveis a ações: ficam de fora.
        if "eh_fii" in ranking.columns and not incluir_fiis:
            ranking = ranking[ranking["eh_fii"] != True].copy()  # noqa: E712
            ranking = ranking.reset_index(drop=True)
            ranking["posicao"] = range(1, len(ranking) + 1)

        registros = ranking.head(limite).to_dict(orient="records")
        return {
            "perfil": {
                "chave": perfil.chave,
                "rotulo": perfil.rotulo,
                "descricao": perfil.descricao,
                "criterios": perfil.como_texto_criterios(),
                "extras": perfil.criterios,
                "ordenacao": perfil.ordenacao,
            },
            "total_mercado": int(len(df)),
            "total_aprovado": int(len(ranking)),
            "colunas": resumo_colunas(ranking),
            "resultados": registros,
        }

    def _enriquecer(self, df: pd.DataFrame) -> None:
        """Preenche nome, tipo de papel e marca FII no DataFrame (in place)."""
        if df.empty:
            return

        def _buscar(ticker: str) -> tuple[str, dict[str, Any]]:
            try:
                detalhe = self.fundamentus.detalhes(ticker)
                return ticker, {
                    "nome": detalhe.empresa,
                    "tipo_papel": detalhe.tipo_papel,
                    "eh_fii": detalhe.eh_fii,
                    "setor": detalhe.setor,
                }
            except Exception:  # noqa: BLE001
                return ticker, {}

        dados: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            for ticker, info in pool.map(_buscar, df["ticker"].tolist()):
                dados[ticker] = info

        for coluna, padrao in [("nome", None), ("tipo_papel", None), ("eh_fii", False), ("setor", None)]:
            df[coluna] = [dados.get(t, {}).get(coluna, padrao) for t in df["ticker"]]

    # ---------------------------------------------------------------- contexto
    def contexto(self, ticker: str, criterios_tela: list[str] | None = None) -> ContextoEmpresa:
        """Monta o dossiê numérico de uma empresa."""
        ticker = ticker.strip().upper().replace(".SA", "")
        ctx = ContextoEmpresa(ticker=ticker, criterios_tela=criterios_tela or [])

        # 1) Fundamentus: indicadores, balanço e DRE (fonte primária).
        try:
            detalhe = self.fundamentus.detalhes(ticker)
            ctx.indicadores = detalhe.indicadores
            ctx.balanco = detalhe.balanco
            ctx.dre_anual = detalhe.dre_anual
            ctx.dre_trimestral = detalhe.dre_trimestral
            ctx.ultimo_balanco = detalhe.ultimo_balanco
            ctx.cotacao = detalhe.cotacao or ctx.cotacao
            ctx.valor_mercado = detalhe.valor_mercado or ctx.valor_mercado
            ctx.nro_acoes = detalhe.nro_acoes
            ctx.valor_firma = detalhe.valor_firma
            ctx.nome = detalhe.empresa or ctx.nome
            ctx.setor = detalhe.setor
            ctx.subsetor = detalhe.subsetor
            ctx.tipo_papel = detalhe.tipo_papel
        except Exception as exc:  # noqa: BLE001
            log.warning("Fundamentus sem dados para %s: %s", ticker, exc)
            ctx.notas.append(f"Fundamentus não retornou dados para {ticker}.")

        # FII? Três fontes, porque nenhuma é completa:
        #   - o Fundamentus marca unidades de FII como "ON NM" (sinal confiável);
        #   - a lista do Fundamentus cobre os 383 FIIs de código terminado em 11;
        #   - o yfinance classifica FIIs como setor "Real Estate".
        ctx.tipo = "Ação"
        nome_upper = (ctx.nome or "").upper()
        if "FII" in nome_upper or "FUNDO DE INVESTIMENTO" in nome_upper:
            ctx.tipo = "FII"
        if ctx.tipo_papel and "NM" in ctx.tipo_papel.upper():
            ctx.tipo = "FII"
        try:
            if ticker in self.fundamentus.lista_fiis():
                ctx.tipo = "FII"
        except Exception:  # noqa: BLE001
            pass
        try:
            if (self.yahoo.perfil(ticker).get("setor") or "") == "Real Estate":
                ctx.tipo = "FII"
        except Exception:  # noqa: BLE001
            pass

        # 2) yfinance: nome, cotação, proventos e performance.
        try:
            perfil_mercado = self.yahoo.perfil(ticker)
            ctx.nome = perfil_mercado.get("nome") or ctx.nome
            ctx.cotacao = ctx.cotacao or perfil_mercado.get("cotacao")
            ctx.valor_mercado = ctx.valor_mercado or perfil_mercado.get("valor_mercado")
            ctx.setor = ctx.setor or perfil_mercado.get("setor")
        except Exception as exc:  # noqa: BLE001
            log.warning("yfinance sem perfil para %s: %s", ticker, exc)

        try:
            prov = self.yahoo.proventos(ticker)
            ctx.proventos = {
                "dividendos_12m_por_acao": prov.por_acao_12m,
                "dividendos_12m_total": None,
                "n_pagamentos": prov.n_pagamentos,
                "ultimo": prov.ultimo,
                "payout": None,
                "pagamentos": prov.pagamentos,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("yfinance sem proventos para %s: %s", ticker, exc)

        # Total em R$ e payout: cruzamento entre o dividendo por ação (yfinance),
        # o número de ações e o lucro por ação (ambos do Fundamentus).
        por_acao = ctx.proventos.get("dividendos_12m_por_acao")
        if por_acao and ctx.nro_acoes:
            ctx.proventos["dividendos_12m_total"] = por_acao * ctx.nro_acoes
            lpa = ctx.indicadores.get("lpa")
            if lpa:
                ctx.proventos["payout"] = (por_acao / lpa) * 100

        try:
            perf = self.yahoo.performance(ticker)
            ctx.performance = {
                "dia": _pct(perf.get("dia")),
                "mes": _pct(perf.get("mes")),
                "3 meses": _pct(perf.get("3meses")),
                "12 meses": _pct(perf.get("12meses")),
                "ano": _pct(perf.get("ano")),
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("yfinance sem performance para %s: %s", ticker, exc)

        if not ctx.nome:
            ctx.notas.append("Nome da empresa não foi identificado (fonte de mercado indisponível).")

        return ctx

    # ----------------------------------------------------------------- parecer
    def parecer(
        self,
        ticker: str,
        perfil_ia: str = "qualidade",
        criterios_tela: list[str] | None = None,
    ) -> dict[str, Any]:
        """Gera o parecer técnico da IA para uma empresa."""
        ctx = self.contexto(ticker, criterios_tela=criterios_tela)
        if not ctx.indicadores and not ctx.balanco:
            raise ErroIA(
                f"Não foi possível obter dados de {ticker.upper()} para análise. "
                "Verifique se o ticker existe na B3."
            )

        prompt = construir_prompt(
            ticker=ctx.ticker,
            nome=ctx.nome,
            tipo=ctx.tipo,
            setor=ctx.setor,
            subsetor=ctx.subsetor,
            indicadores=ctx.indicadores,
            balanco=ctx.balanco,
            dre_anual=ctx.dre_anual,
            dre_trimestral=ctx.dre_trimestral,
            perfil=perfil_ia,
            cotacao=ctx.cotacao,
            valor_mercado=ctx.valor_mercado,
            ultimo_balanco=ctx.ultimo_balanco,
            proventos=ctx.proventos,
            performance=ctx.performance,
            criterios_tela=ctx.criterios_tela,
            nro_acoes=ctx.nro_acoes,
            valor_firma=ctx.valor_firma,
        )

        resultado = self.cliente_ia.gerar(
            prompt=prompt,
            instrucao_sistema=INSTRUCAO_SISTEMA,
            schema=schema_relatorio(),
        )
        return {
            "contexto": ctx.as_dict(),
            "relatorio": resultado.dados or {"resumo": resultado.texto},
            "meta": {
                "modelo": resultado.modelo,
                "tokens_entrada": resultado.tokens_entrada,
                "tokens_saida": resultado.tokens_saida,
                "segundos": round(resultado.segundos, 1),
            },
        }


def _pct(valor: Any) -> str | None:
    if valor is None:
        return None
    try:
        return f"{float(valor):.1f}%"
    except (TypeError, ValueError):
        return None
