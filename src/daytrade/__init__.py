"""Day Trade — monitoramento e análise com Inteligência Artificial para operações intraday.

Recursos:
  * Radar de Oportunidades com IA (Scanner em tempo real de ativos líquidos da B3)
  * Sugestões automáticas de COMPRA e VENDA intraday com Preço de Entrada, Alvo e Stop Loss
  * Análise sob demanda de qualquer ativo pelo modelo Gemini
  * Avaliação tática com IA de posições abertas
  * Registro e monitoramento de operações com alertas

Não é recomendação de investimento. Ferramenta analítica e educacional.
"""

from .analisador import (
    TICKERS_DAYTRADE_PADRAO,
    MetricasIntraday,
    analisar_ativo_daytrade_ia,
    avaliar_posicao_aberta_ia,
    calcular_metricas_intraday,
    coletar_metricas_lote,
    gerar_radar_daytrade_ia,
)
from .monitor import MonitorDayTrade, OperacaoDayTrade, SinalDayTrade

__all__ = [
    "MonitorDayTrade",
    "OperacaoDayTrade",
    "SinalDayTrade",
    "MetricasIntraday",
    "TICKERS_DAYTRADE_PADRAO",
    "calcular_metricas_intraday",
    "coletar_metricas_lote",
    "gerar_radar_daytrade_ia",
    "analisar_ativo_daytrade_ia",
    "avaliar_posicao_aberta_ia",
]
