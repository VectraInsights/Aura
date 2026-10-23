"""Analisador de Day Trade com Inteligência Artificial.

Coleta métricas intraday (Preço, Abertura, Máxima, Mínima, VWAP, IFR/RSI,
médias móveis e volume) e usa o Google Gemini para sugerir operações reais de
COMPRA (Long) e VENDA (Short) ao longo do pregão, com pontos de entrada,
alvo de lucro (Take Profit), stop loss de proteção e relação risco:retorno.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from ..ia.gemini import ClienteGemini, ErroIA

log = logging.getLogger(__name__)

#: Universo padrão com as ações mais líquidas e negociadas da B3 para Day Trade
TICKERS_DAYTRADE_PADRAO = [
    "PETR4",
    "VALE3",
    "ITUB4",
    "BBDC4",
    "BBAS3",
    "PRIO3",
    "MGLU3",
    "RENT3",
    "HAPV3",
    "CSNA3",
    "GGBR4",
    "WEGE3",
    "B3SA3",
    "SUZB3",
    "ABEV3",
    "JBSS3",
]

#: Cache rápido em memória para evitar chamadas repetidas ao Yahoo Finance (TTL 60s)
_CACHE_INTRADAY: dict[str, tuple[float, MetricasIntraday]] = {}


@dataclass
class MetricasIntraday:
    """Métricas técnicas de preço e volume no intraday (gráfico de 5 minutos)."""

    ticker: str
    preco_atual: float
    abertura: float
    maxima: float
    minima: float
    variacao_dia_pct: float
    amplitude_dia_pct: float
    posicao_range_pct: float  # 0% = na mínima, 100% = na máxima
    vwap: float
    distancia_vwap_pct: float  # positivo: acima da vwap, negativo: abaixo
    rsi_14: float
    ema_9: float
    ema_21: float
    tendencia: str  # "ALTA FORTE", "ALTA", "BAIXA FORTE", "BAIXA", "LATERAL"
    volume_total: int
    data_ref: str

    def linha_resumo(self) -> str:
        """Formata linha legível para o prompt da IA."""
        return (
            f"{self.ticker}: Preço R$ {self.preco_atual:.2f} | "
            f"Abertura R$ {self.abertura:.2f} | "
            f"Máx R$ {self.maxima:.2f} | Mín R$ {self.minima:.2f} | "
            f"Var Dia: {self.variacao_dia_pct:+.2f}% | "
            f"Amplitude: {self.amplitude_dia_pct:.2f}% | "
            f"Posição no Range: {self.posicao_range_pct:.1f}% | "
            f"VWAP: R$ {self.vwap:.2f} (Dist: {self.distancia_vwap_pct:+.2f}%) | "
            f"IFR/RSI(14): {self.rsi_14:.1f} | "
            f"Tendência 5m: {self.tendencia} | "
            f"Volume: {self.volume_total:n}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "preco_atual": round(self.preco_atual, 2),
            "abertura": round(self.abertura, 2),
            "maxima": round(self.maxima, 2),
            "minima": round(self.minima, 2),
            "variacao_dia_pct": round(self.variacao_dia_pct, 2),
            "amplitude_dia_pct": round(self.amplitude_dia_pct, 2),
            "posicao_range_pct": round(self.posicao_range_pct, 1),
            "vwap": round(self.vwap, 2),
            "distancia_vwap_pct": round(self.distancia_vwap_pct, 2),
            "rsi_14": round(self.rsi_14, 1),
            "ema_9": round(self.ema_9, 2),
            "ema_21": round(self.ema_21, 2),
            "tendencia": self.tendencia,
            "volume_total": self.volume_total,
            "data_ref": self.data_ref,
        }


def calcular_metricas_intraday(ticker: str, usar_cache: bool = True) -> MetricasIntraday | None:
    """Baixa velas de 5 minutos e calcula indicadores técnicos intraday para Day Trade."""
    t_clean = ticker.strip().upper()
    agora = time.time()

    if usar_cache and t_clean in _CACHE_INTRADAY:
        ts, obj = _CACHE_INTRADAY[t_clean]
        if agora - ts < 60:
            return obj

    try:
        import yfinance as yf

        simbolo = f"{t_clean}.SA" if not t_clean.endswith(".SA") else t_clean
        ticker_obj = yf.Ticker(simbolo)
        # 5d com 5m garante dados da sessão atual ou da última sessão encerrada
        df = ticker_obj.history(period="5d", interval="5m", auto_adjust=False)
        if df is None or df.empty:
            return None

        dias = df.index.normalize().unique()
        if len(dias) == 0:
            return None

        ultimo_dia = dias[-1]
        df_dia = df[df.index.normalize() == ultimo_dia].copy()
        if df_dia.empty or len(df_dia) < 3:
            return None

        ultimo = float(df_dia["Close"].iloc[-1])
        abertura = float(df_dia["Open"].iloc[0])
        maxima = float(df_dia["High"].max())
        minima = float(df_dia["Low"].min())

        var_dia_pct = ((ultimo / abertura) - 1) * 100 if abertura > 0 else 0.0
        amp_dia_pct = ((maxima - minima) / minima) * 100 if minima > 0 else 0.0
        pos_range_pct = (
            ((ultimo - minima) / (maxima - minima)) * 100 if maxima > minima else 50.0
        )

        # VWAP (Preço médio ponderado por volume)
        tp = (df_dia["High"] + df_dia["Low"] + df_dia["Close"]) / 3
        vol = df_dia["Volume"]
        vol_sum = float(vol.sum())
        if vol_sum > 0:
            vwap = float(((tp * vol).cumsum() / vol.cumsum()).iloc[-1])
        else:
            vwap = ultimo
        dist_vwap_pct = ((ultimo / vwap) - 1) * 100 if vwap > 0 else 0.0

        # RSI (14 períodos no gráfico de 5m)
        diff = df_dia["Close"].diff()
        gain = diff.clip(lower=0)
        loss = -diff.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

        # EMA 9 e 21
        ema9 = float(df_dia["Close"].ewm(span=9, adjust=False).mean().iloc[-1])
        ema21 = float(df_dia["Close"].ewm(span=21, adjust=False).mean().iloc[-1])

        tendencia = "LATERAL"
        if ultimo > ema9 > ema21 and ultimo > vwap:
            tendencia = "ALTA FORTE"
        elif ultimo > vwap and ema9 > ema21:
            tendencia = "ALTA"
        elif ultimo < ema9 < ema21 and ultimo < vwap:
            tendencia = "BAIXA FORTE"
        elif ultimo < vwap and ema9 < ema21:
            tendencia = "BAIXA"

        res = MetricasIntraday(
            ticker=t_clean,
            preco_atual=ultimo,
            abertura=abertura,
            maxima=maxima,
            minima=minima,
            variacao_dia_pct=var_dia_pct,
            amplitude_dia_pct=amp_dia_pct,
            posicao_range_pct=pos_range_pct,
            vwap=vwap,
            distancia_vwap_pct=dist_vwap_pct,
            rsi_14=rsi_val,
            ema_9=ema9,
            ema_21=ema21,
            tendencia=tendencia,
            volume_total=int(vol_sum),
            data_ref=str(ultimo_dia.date()),
        )

        _CACHE_INTRADAY[t_clean] = (agora, res)
        return res

    except Exception as exc:  # noqa: BLE001
        log.warning("Falha ao coletar métricas intraday para %s: %s", ticker, exc)
        return None


def coletar_metricas_lote(
    tickers: list[str] | None = None,
    max_workers: int = 6,
) -> list[MetricasIntraday]:
    """Coleta métricas intraday em paralelo para a lista de tickers."""
    alvos = tickers or TICKERS_DAYTRADE_PADRAO
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        resultados = list(pool.map(calcular_metricas_intraday, alvos))
    return [m for m in resultados if m is not None]


# --------------------------------------------------------------------- Prompts & Schema

INSTRUCAO_SISTEMA_DAYTRADE = """\
Você é um estrategista profissional sênior de Day Trade e Scalping na B3 (Bolsa Brasileira).
Seu objetivo é analisar os dados técnicos e quantitativos INTRADAY dos ativos fornecidos e
identificar oportunidades REAIS e CONCRETAS para operar hoje (compras long ou vendas short).

Princípios operacionais obrigatórios:
1. FOCO EM COMPRA E VENDA NO MESMO DIA: Não analise balanço nem fundamentos de anos.
   O foco absoluto é preço, variação intraday, VWAP, suporte/resistência, IFR e momentum.
2. PRECISÃO NUMÉRICA: Todo trade sugerido DEVE informar:
   - Tipo: "COMPRA" ou "VENDA"
   - Estratégia: Ex: "Rompimento de Topo", "Repique na VWAP", "Reversão em Sobrevenda", "Pullback na Média"
   - Preço de Entrada: Valor em R$ compatível com o preço atual
   - Alvo de Lucro (Gain): Preço em R$ e % de ganho esperado (geralmente entre +0.8% e +2.5%)
   - Stop Loss de Proteção: Preço em R$ e % de perda máxima (geralmente entre -0.5% e -1.2%)
   - Relação Risco:Retorno: Proporção matemática mínima de 1.5:1 (ex: "1:1.8" ou "1:2")
   - Motivo Técnico: Explicação lógica citando os números reais (VWAP, IFR, amplitude, máximas/mínimas)
3. FILTRAGEM RIGOROSA (EVITAR): Se um ativo estiver em consolidação/lateral sem direção,
   com IFR neutro ou volume anêmico, coloque-o na lista 'evitar' com justificativa técnica.
4. GESTÃO DE RISCO: O trader NUNCA deve entrar sem stop loss definido.
"""


def schema_radar_daytrade():
    """Schema JSON do Radar de Day Trade."""
    from google.genai import types

    oportunidade = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "ticker": types.Schema(type=types.Type.STRING),
            "tipo": types.Schema(type=types.Type.STRING, description="COMPRA ou VENDA"),
            "estrategia": types.Schema(type=types.Type.STRING, description="Nome do setup técnico"),
            "preco_entrada": types.Schema(type=types.Type.NUMBER, description="Preço sugerido de entrada"),
            "alvo_lucro": types.Schema(type=types.Type.NUMBER, description="Preço alvo para realização de lucro"),
            "alvo_lucro_pct": types.Schema(type=types.Type.NUMBER, description="Percentual positivo de ganho esperado"),
            "stop_loss": types.Schema(type=types.Type.NUMBER, description="Preço de stop loss para sair na perda"),
            "stop_loss_pct": types.Schema(type=types.Type.NUMBER, description="Percentual máximo de perda no trade"),
            "risco_retorno": types.Schema(type=types.Type.STRING, description="Relação risco:retorno, ex: 1:1.8"),
            "motivo_tecnico": types.Schema(type=types.Type.STRING, description="Justificativa com números reais"),
            "forca_sinal": types.Schema(type=types.Type.STRING, description="ALTA ou MEDIA"),
            "tempo_estimado": types.Schema(type=types.Type.STRING, description="Estimativa de duração, ex: 15-45 min"),
        },
        required=[
            "ticker",
            "tipo",
            "estrategia",
            "preco_entrada",
            "alvo_lucro",
            "alvo_lucro_pct",
            "stop_loss",
            "stop_loss_pct",
            "risco_retorno",
            "motivo_tecnico",
            "forca_sinal",
        ],
    )

    evitar = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "ticker": types.Schema(type=types.Type.STRING),
            "motivo": types.Schema(type=types.Type.STRING, description="Por que não operar hoje"),
        },
        required=["ticker", "motivo"],
    )

    return types.Schema(
        type=types.Type.OBJECT,
        properties={
            "resumo_mercado": types.Schema(
                type=types.Type.STRING,
                description="Visão geral do pregão e comportamento dos ativos no dia",
            ),
            "oportunidades": types.Schema(type=types.Type.ARRAY, items=oportunidade),
            "evitar": types.Schema(type=types.Type.ARRAY, items=evitar),
            "alertas_operacionais": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="Avisos sobre horários de liquidez, notícias ou volatilidade",
            ),
        },
        required=["resumo_mercado", "oportunidades", "evitar"],
    )


def schema_analise_individual_daytrade():
    """Schema JSON para análise de um ativo específico para Day Trade."""
    from google.genai import types

    return types.Schema(
        type=types.Type.OBJECT,
        properties={
            "ticker": types.Schema(type=types.Type.STRING),
            "veredito": types.Schema(type=types.Type.STRING, description="COMPRAR, VENDER ou AGUARDAR"),
            "estrategia": types.Schema(type=types.Type.STRING),
            "score_oportunidade": types.Schema(type=types.Type.INTEGER, description="Nota de 0 a 10 para o trade agora"),
            "preco_atual": types.Schema(type=types.Type.NUMBER),
            "preco_entrada": types.Schema(type=types.Type.NUMBER),
            "alvo_lucro": types.Schema(type=types.Type.NUMBER),
            "alvo_lucro_pct": types.Schema(type=types.Type.NUMBER),
            "stop_loss": types.Schema(type=types.Type.NUMBER),
            "stop_loss_pct": types.Schema(type=types.Type.NUMBER),
            "risco_retorno": types.Schema(type=types.Type.STRING),
            "racional_tecnico": types.Schema(type=types.Type.STRING, description="Análise detalhada do momento intraday"),
            "gatilho_confirmacao": types.Schema(type=types.Type.STRING, description="O que deve acontecer para abrir a ordem"),
            "pontos_atencao": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
        },
        required=[
            "ticker",
            "veredito",
            "score_oportunidade",
            "preco_entrada",
            "alvo_lucro",
            "stop_loss",
            "racional_tecnico",
            "gatilho_confirmacao",
        ],
    )


def schema_avaliacao_posicao_ia():
    """Schema para reavaliar uma posição aberta de Day Trade com a IA."""
    from google.genai import types

    return types.Schema(
        type=types.Type.OBJECT,
        properties={
            "ticker": types.Schema(type=types.Type.STRING),
            "acao_recomendada": types.Schema(
                type=types.Type.STRING,
                description="REALIZAR_LUCRO, ZERAR_IMEDIATAMENTE, SUBIR_STOP ou MANTER",
            ),
            "novo_stop_sugerido": types.Schema(type=types.Type.NUMBER, description="Valor em R$ se sugerir subir o stop"),
            "parecer_tatico": types.Schema(type=types.Type.STRING, description="Leitura técnica do trade agora"),
            "urgencia": types.Schema(type=types.Type.STRING, description="ALTA, MEDIA ou NORMAL"),
        },
        required=["ticker", "acao_recomendada", "parecer_tatico", "urgencia"],
    )


# --------------------------------------------------------------------- Funções Principais

def gerar_radar_daytrade_ia(
    cliente_ia: ClienteGemini,
    tickers: list[str] | None = None,
) -> dict[str, Any]:
    """Escaneia os ativos mais líquidos da B3 e gera com a IA as sugestões de compra/venda do dia."""
    metricas = coletar_metricas_lote(tickers)
    if not metricas:
        raise ErroIA("Não foi possível obter dados intraday dos ativos para análise.")

    linhas = [m.linha_resumo() for m in metricas]
    prompt = (
        "Analise os seguintes ativos negociados no pregão de hoje na B3 para operações de DAY TRADE:\n\n"
        + "\n".join(f"{i}. {l}" for i, l in enumerate(linhas, 1))
        + "\n\n"
        "Com base estritamente nestes dados intraday (VWAP, IFR, tendência, amplitude e máximas/mínimas), "
        "recomende as MELHORES OPORTUNIDADES de COMPRA e VENDA para hoje com preços de entrada, alvo e stop loss claros. "
        "Separe os ativos que devem ser EVITADOS devido à indefinição ou baixa volatilidade."
    )

    resultado = cliente_ia.gerar(
        prompt=prompt,
        instrucao_sistema=INSTRUCAO_SISTEMA_DAYTRADE,
        schema=schema_radar_daytrade(),
    )

    dados = resultado.dados or {}
    dados["gerado_em"] = datetime.now().isoformat(timespec="seconds")
    dados["total_ativos_analisados"] = len(metricas)
    dados["metricas_brutas"] = [m.as_dict() for m in metricas]
    return dados


def analisar_ativo_daytrade_ia(
    ticker: str,
    cliente_ia: ClienteGemini,
) -> dict[str, Any]:
    """Realiza uma análise técnica aprofundada de um ativo específico para Day Trade."""
    m = calcular_metricas_intraday(ticker, usar_cache=False)
    if not m:
        raise ErroIA(f"Não foi possível obter dados intraday de {ticker.upper()} na B3.")

    prompt = (
        f"Analise o ativo {m.ticker} para operação de DAY TRADE no pregão de hoje:\n\n"
        f"{m.linha_resumo()}\n\n"
        "Avalie se vale a pena abrir posição de COMPRA, VENDA ou AGUARDAR neste exato momento. "
        "Especifique preço de entrada ideal, alvo de realização, stop loss técnico, relação risco:retorno "
        "e o gatilho gráfico exato de confirmação."
    )

    resultado = cliente_ia.gerar(
        prompt=prompt,
        instrucao_sistema=INSTRUCAO_SISTEMA_DAYTRADE,
        schema=schema_analise_individual_daytrade(),
    )

    dados = resultado.dados or {}
    dados["metricas"] = m.as_dict()
    dados["gerado_em"] = datetime.now().isoformat(timespec="seconds")
    return dados


def avaliar_posicao_aberta_ia(
    ticker: str,
    preco_compra: float,
    cliente_ia: ClienteGemini,
    preco_venda_meta: float | None = None,
) -> dict[str, Any]:
    """A IA avalia taticamente uma operação aberta pelo investidor."""
    m = calcular_metricas_intraday(ticker, usar_cache=False)
    if not m:
        raise ErroIA(f"Não foi possível obter cotação intraday para {ticker.upper()}.")

    var_trade = ((m.preco_atual / preco_compra) - 1) * 100
    meta_info = f" | Meta de Venda Alvo: R$ {preco_venda_meta:.2f}" if preco_venda_meta else ""

    prompt = (
        f"O investidor possui uma operação aberta de DAY TRADE em {m.ticker}:\n"
        f"- Preço de Compra: R$ {preco_compra:.2f}\n"
        f"- Preço Atual: R$ {m.preco_atual:.2f} ({var_trade:+.2f}% de resultado agora){meta_info}\n\n"
        f"Situação intraday do ativo:\n{m.linha_resumo()}\n\n"
        "Qual é a melhor conduta tática agora? "
        "Deve REALIZAR_LUCRO, ZERAR_IMEDIATAMENTE (stop), SUBIR_STOP para proteger capital ou MANTER até o alvo?"
    )

    resultado = cliente_ia.gerar(
        prompt=prompt,
        instrucao_sistema=INSTRUCAO_SISTEMA_DAYTRADE,
        schema=schema_avaliacao_posicao_ia(),
    )

    dados = resultado.dados or {}
    dados["preco_atual"] = m.preco_atual
    dados["variacao_trade_pct"] = round(var_trade, 2)
    dados["gerado_em"] = datetime.now().isoformat(timespec="seconds")
    return dados
