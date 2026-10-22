"""Projeção de rendimento e preço justo baseada em dados passados.

Calcula, a partir dos números coletados do Fundamentus e yfinance:

* Projeção de dividendos nos próximos 12 meses (DY atual × crescimento de lucro).
* Preço justo via Modelo de Gordon Crescimento:
    Preço justo = Div_1 / (taxa_desconto - crescimento)
* Cenários (otimista / base / pessimista) com base na variação histórica.
* Indicador de "bom momento": preço atual abaixo do preço justo E
  dividend yield atraente.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: Taxa de desconto padrão (exigência mínima de retorno do investidor).
TAXA_DESCONTO_PADRAO = 0.10  # 10% a.a.

#: Crescimento conservador assumido quando não há histórico suficiente.
CRESCIMENTO_CONSERVADOR = 0.03  # 3% a.a.


@dataclass
class ProjecaoRendimento:
    """Resultado da projeção de rendimento."""

    ticker: str
    preco_atual: float | None = None
    dy_atual: float | None = None  # dividend yield atual (%)
    dividendos_12m: float | None = None  # total projetado para os próximos 12m
    preco_justo: float | None = None  # preço justo (Modelo de Gordon)
    upside: float | None = None  # (preco_justo - preco_atual) / preco_atual
    bom_momento: bool = False
    motivo: str = ""
    cenarios: dict[str, dict[str, Any]] = field(default_factory=dict)
    alertas: list[str] = field(default_factory=list)


def _pct(valor: Any) -> float | None:
    if valor is None:
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _crescimento_lucro(dre_anual: dict[str, Any], dre_trimestral: dict[str, Any]) -> float | None:
    """Estima o crescimento anual do lucro a partir do DRE.

    Usa a comparação entre o último ano e o período anterior disponível.
    Devolve None se não houver dados suficientes.
    """
    lucro_atual = _pct(dre_anual.get("lucro_liquido"))
    lucro_anterior = _pct(dre_trimestral.get("lucro_liquido"))
    if lucro_atual is None or lucro_anterior is None or lucro_anterior <= 0:
        return None
    # O DRE trimestral é do último trimestre; anualiza aproximando x4.
    lucro_anterior_anual = lucro_anterior * 4
    if lucro_anterior_anual <= 0:
        return None
    cresc = (lucro_atual / lucro_anterior_anual - 1)
    # Limita o crescimento a valores realistas para não distorrer a projeção.
    return max(min(cresc, 0.30), -0.20)


def projetar(
    ticker: str,
    contexto: dict[str, Any],
    taxa_desconto: float = TAXA_DESCONTO_PADRAO,
) -> ProjecaoRendimento:
    """Gera a projeção de rendimento e preço justo para um ticker."""

    proj = ProjecaoRendimento(ticker=ticker)

    indicadores = contexto.get("indicadores") or {}
    balanco = contexto.get("balanco") or {}
    dre_anual = contexto.get("dre_anual") or {}
    dre_trimestral = contexto.get("dre_trimestral") or {}
    proventos = contexto.get("proventos") or {}
    performance = contexto.get("performance") or {}

    proj.preco_atual = _pct(contexto.get("cotacao"))
    proj.dy_atual = _pct(indicadores.get("divyield"))

    # ------------------------------------------------------------- dividendos
    div_por_acao_12m = _pct(proventos.get("dividendos_12m_por_acao"))
    nro_acoes = _pct(indicadores.get("nro_acoes")) or _pct(contexto.get("nro_acoes"))
    lpa = _pct(indicadores.get("lpa"))

    # Total de dividendos pagos nos últimos 12 meses (R$).
    if div_por_acao_12m is not None and nro_acoes is not None:
        proj.dividendos_12m = div_por_acao_12m * nro_acoes
    elif div_por_acao_12m is not None:
        # Sem número de ações: projeta por ação mesmo.
        proj.dividendos_12m = div_por_acao_12m

    # ------------------------------------------------------------- crescimento
    cresc = _crescimento_lucro(dre_anual, dre_trimestral)
    if cresc is None:
        cresc = CRESCIMENTO_CONSERVADOR
        proj.alertas.append("Sem histórico suficiente de lucro; usado crescimento conservador de 3% a.a.")

    # ------------------------------------------------------------- preço justo (Gordon)
    # Div_1 = dividendo esperado nos próximos 12 meses.
    div_1 = proj.dividendos_12m
    if div_1 is None and div_por_acao_12m is not None and proj.preco_atual is not None and proj.dy_atual is not None:
        # Reconstroi o dividendo total a partir do DY e preço atual.
        div_1 = proj.preco_atual * (proj.dy_atual / 100)

    if div_1 is not None and taxa_desconto > cresc:
        proj.preco_justo = div_1 / (taxa_desconto - cresc)

    # ------------------------------------------------------------- upside
    if proj.preco_justo is not None and proj.preco_atual is not None and proj.preco_atual > 0:
        proj.upside = (proj.preco_justo / proj.preco_atual - 1) * 100

    # ------------------------------------------------------------- cenários
    variacao_12m = _pct(performance.get("12 meses"))
    if variacao_12m is None:
        variacao_12m = 0.0

    base_preco = proj.preco_atual if proj.preco_atual is not None else (proj.preco_justo or 0)
    if base_preco <= 0:
        base_preco = proj.preco_justo or 0

    if base_preco > 0:
        proj.cenarios = {
            "pessimista": {
                "variacao": variacao_12m - 10,
                "preco": base_preco * (1 + (variacao_12m - 10) / 100),
                "rendimento_12m": proj.dividendos_12m,
            },
            "base": {
                "variacao": variacao_12m,
                "preco": base_preco * (1 + variacao_12m / 100),
                "rendimento_12m": proj.dividendos_12m,
            },
            "otimista": {
                "variacao": variacao_12m + 10,
                "preco": base_preco * (1 + (variacao_12m + 10) / 100),
                "rendimento_12m": proj.dividendos_12m,
            },
        }

    # ------------------------------------------------------------- bom momento
    motivos: list[str] = []
    pontos = 0

    if proj.preco_justo is not None and proj.preco_atual is not None and proj.preco_atual > 0:
        desconto = (proj.preco_justo - proj.preco_atual) / proj.preco_justo
        if desconto >= 0.15:
            motivos.append(f"Preço {proj.preco_atual:,.2f} está {desconto*100:.1f}% abaixo do justo ({proj.preco_justo:,.2f})")
            pontos += 1
        elif desconto >= 0.05:
            motivos.append(f"Preço levemente abaixo do justo ({proj.preco_justo:,.2f})")
            pontos += 0.5

    if proj.dy_atual is not None:
        if proj.dy_atual >= 6:
            motivos.append(f"Dividend yield de {proj.dy_atual:.1f}% acima da média")
            pontos += 1
        elif proj.dy_atual >= 4:
            motivos.append(f"Dividend yield de {proj.dy_atual:.1f}% razoável")
            pontos += 0.5

    if proj.upside is not None and proj.upside >= 15:
        motivos.append(f"Upside potencial de {proj.upside:.1f}%")
        pontos += 1

    div_liq_patrimonio = _pct(indicadores.get("div_liq_patrimonio"))
    if div_liq_patrimonio is not None and div_liq_patrimonio <= 0.5:
        motivos.append(f"Endividamento baixo (Dív.Líq/PL {div_liq_patrimonio:.2f})")
        pontos += 0.5
    elif div_liq_patrimonio is not None and div_liq_patrimonio > 1.0:
        motivos.append(f"Endividamento elevado (Dív.Líq/PL {div_liq_patrimonio:.2f})")
        pontos -= 0.5

    proj.bom_momento = pontos >= 1.5

    if motivos:
        proj.motivo = " · ".join(motivos)
    else:
        proj.motivo = "Sem sinais claros de desconto ou yield atrativo nos dados disponíveis."

    return proj


def projecao_para_texto(proj: ProjecaoRendimento) -> str:
    """Gera um texto resumido da projeção para exibição."""
    linhas: list[str] = []
    if proj.preco_atual is not None:
        linhas.append(f"Preço atual: R$ {proj.preco_atual:,.2f}")
    if proj.preco_justo is not None:
        linhas.append(f"Preço justo (Gordon): R$ {proj.preco_justo:,.2f}")
    if proj.upside is not None:
        sinal = "+" if proj.upside >= 0 else ""
        linhas.append(f"Upside: {sinal}{proj.upside:.1f}%")
    if proj.dy_atual is not None:
        linhas.append(f"Dividend yield: {proj.dy_atual:.1f}%")
    if proj.dividendos_12m is not None:
        linhas.append(f"Dividendos proj. 12m: R$ {proj.dividendos_12m:,.2f}")
    linhas.append(f"Bom momento: {'Sim' if proj.bom_momento else 'Não'}")
    if proj.motivo:
        linhas.append(f"Motivo: {proj.motivo}")
    return " | ".join(linhas)


# ============================================================================
# SUGESTÃO DE OPERAÇÃO (Compra / Venda / Manter) baseada em fatos
# ============================================================================

from enum import Enum


class SugestaoOperacao(str, Enum):
    COMPRA = "COMPRA"
    VENDA = "VENDA"
    MANTER = "MANTER"


@dataclass
class ResultadoSugestao:
    """Resultado da análise de sugestão de operação."""

    ticker: str
    sugestao: SugestaoOperacao
    confianca: str  # Alta / Media / Baixa
    score: float  # pontuação de -5 a +5
    fatos_compra: list[str] = field(default_factory=list)
    fatos_venda: list[str] = field(default_factory=list)
    fatos_neutro: list[str] = field(default_factory=list)
    texto_whatsapp: str = ""
    alertas: list[str] = field(default_factory=list)


def sugerir_operacao(
    ticker: str,
    contexto: dict[str, Any],
    taxa_desconto: float = TAXA_DESCONTO_PADRAO,
) -> ResultadoSugestao:
    """Analisa os fatos da empresa e sugere COMPRA, VENDA ou MANTER.

    A sugestão é baseada exclusivamente em números fornecidos (Fundamentus +
    yfinance), não em opinião da IA. Cada fato positivo soma pontos para compra,
    cada fato negativo soma pontos para venda. O saldo final determina a sugestão.
    """

    proj = projetar(ticker, contexto, taxa_desconto=taxa_desconto)

    indicadores = contexto.get("indicadores") or {}
    balanco = contexto.get("balanco") or {}
    dre_anual = contexto.get("dre_anual") or {}
    performance = contexto.get("performance") or {}

    fatos_compra: list[str] = []
    fatos_venda: list[str] = []
    fatos_neutro: list[str] = []
    score = 0.0

    # -------- Preço vs justo --------
    if proj.preco_justo is not None and proj.preco_atual is not None and proj.preco_atual > 0:
        desconto = (proj.preco_justo - proj.preco_atual) / proj.preco_justo
        if desconto >= 0.15:
            fatos_compra.append(
                f"Preço {proj.preco_atual:,.2f} está {desconto*100:.1f}% abaixo do justo ({proj.preco_justo:,.2f})"
            )
            score += 2
        elif desconto >= 0.05:
            fatos_compra.append(
                f"Preço levemente abaixo do justo ({proj.preco_justo:,.2f})"
            )
            score += 1
        elif desconto <= -0.15:
            fatos_venda.append(
                f"Preço {proj.preco_atual:,.2f} está {abs(desconto)*100:.1f}% acima do justo ({proj.preco_justo:,.2f})"
            )
            score -= 2
        elif desconto <= -0.05:
            fatos_venda.append(
                f"Preço levemente acima do justo ({proj.preco_justo:,.2f})"
            )
            score -= 1
        else:
            fatos_neutro.append(f"Preço próximo ao justo ({proj.preco_justo:,.2f})")

    # -------- Dividend yield --------
    if proj.dy_atual is not None:
        if proj.dy_atual >= 8:
            fatos_compra.append(f"Dividend yield de {proj.dy_atual:.1f}% — excelente para renda")
            score += 2
        elif proj.dy_atual >= 5:
            fatos_compra.append(f"Dividend yield de {proj.dy_atual:.1f}% — bom para renda")
            score += 1
        elif proj.dy_atual >= 3:
            fatos_neutro.append(f"Dividend yield de {proj.dy_atual:.1f}% — razoável")
        elif proj.dy_atual > 0:
            fatos_venda.append(f"Dividend yield baixo ({proj.dy_atual:.1f}%)")
            score -= 0.5
        else:
            fatos_neutro.append("Sem dividend yield informado")

    # -------- Upside potencial --------
    if proj.upside is not None:
        if proj.upside >= 25:
            fatos_compra.append(f"Upside potencial de {proj.upside:.1f}%")
            score += 2
        elif proj.upside >= 10:
            fatos_compra.append(f"Upside potencial de {proj.upside:.1f}%")
            score += 1
        elif proj.upside <= -25:
            fatos_venda.append(f"Downside de {abs(proj.upside):.1f}%")
            score -= 2
        elif proj.upside <= -10:
            fatos_venda.append(f"Downside de {abs(proj.upside):.1f}%")
            score -= 1

    # -------- Endividamento --------
    div_liq_pl = _pct(indicadores.get("div_liq_patrimonio"))
    if div_liq_pl is not None:
        if div_liq_pl <= 0.3:
            fatos_compra.append(f"Endividamento muito baixo (Dív.Líq/PL {div_liq_pl:.2f})")
            score += 1.5
        elif div_liq_pl <= 0.6:
            fatos_compra.append(f"Endividamento baixo (Dív.Líq/PL {div_liq_pl:.2f})")
            score += 0.5
        elif div_liq_pl > 1.5:
            fatos_venda.append(f"Endividamento elevado (Dív.Líq/PL {div_liq_pl:.2f})")
            score -= 1.5
        elif div_liq_pl > 1.0:
            fatos_venda.append(f"Endividamento moderado-alto (Dív.Líq/PL {div_liq_pl:.2f})")
            score -= 0.5

    # -------- ROE --------
    roe = _pct(indicadores.get("roe"))
    if roe is not None:
        if roe >= 15:
            fatos_compra.append(f"ROE forte de {roe:.1f}%")
            score += 1
        elif roe >= 8:
            fatos_compra.append(f"ROE razoável de {roe:.1f}%")
            score += 0.5
        elif roe < 0:
            fatos_venda.append(f"ROE negativo ({roe:.1f}%)")
            score -= 1.5

    # -------- ROIC --------
    roic = _pct(indicadores.get("roic"))
    if roic is not None:
        if roic >= 12:
            fatos_compra.append(f"ROIC forte de {roic:.1f}%")
            score += 1
        elif roic < 0:
            fatos_venda.append(f"ROIC negativo ({roic:.1f}%)")
            score -= 1

    # -------- Crescimento da receita --------
    cresc_rec = _pct(indicadores.get("crescimento_receita_5a"))
    if cresc_rec is not None:
        if cresc_rec >= 10:
            fatos_compra.append(f"Crescimento de receita 5a de {cresc_rec:.1f}% ao ano")
            score += 1
        elif cresc_rec >= 3:
            fatos_compra.append(f"Crescimento de receita 5a de {cresc_rec:.1f}% ao ano")
            score += 0.5
        elif cresc_rec < -5:
            fatos_venda.append(f"Receita encolhendo {cresc_rec:.1f}% ao ano (5a)")
            score -= 1

    # -------- Margem EBIT --------
    margem_ebit = _pct(indicadores.get("margem_ebit"))
    if margem_ebit is not None:
        if margem_ebit >= 15:
            fatos_compra.append(f"Margem EBIT de {margem_ebit:.1f}%")
            score += 0.5
        elif margem_ebit < 0:
            fatos_venda.append(f"Margem EBIT negativa ({margem_ebit:.1f}%)")
            score -= 1

    # -------- Momentum (preço 12 meses) --------
    var_12m = _pct(performance.get("12 meses"))
    if var_12m is not None:
        if var_12m >= 20:
            fatos_compra.append(f"Momentum forte: +{var_12m:.1f}% em 12 meses")
            score += 0.5
        elif var_12m <= -20:
            fatos_venda.append(f"Momentum fraco: {var_12m:.1f}% em 12 meses")
            score -= 0.5
        elif var_12m <= -35:
            fatos_venda.append(f"Momentum muito fraco: {var_12m:.1f}% em 12 meses")
            score -= 1

    # -------- Liquidez corrente --------
    liq_corr = _pct(indicadores.get("liquidez_corrente"))
    if liq_corr is not None:
        if liq_corr < 0.8:
            fatos_venda.append(f"Liquidez corrente baixa ({liq_corr:.2f})")
            score -= 0.5
        elif liq_corr >= 1.5:
            fatos_compra.append(f"Liquidez corrente confortável ({liq_corr:.2f})")
            score += 0.5

    # -------- Determinação da sugestão --------
    if score >= 2.0:
        sugestao = SugestaoOperacao.COMPRA
        confianca = "Alta" if score >= 3.5 else "Media"
    elif score <= -2.0:
        sugestao = SugestaoOperacao.VENDA
        confianca = "Alta" if score <= -3.5 else "Media"
    else:
        sugestao = SugestaoOperacao.MANTER
        confianca = "Media" if abs(score) >= 1.0 else "Baixa"

    resultado = ResultadoSugestao(
        ticker=ticker,
        sugestao=sugestao,
        confianca=confianca,
        score=score,
        fatos_compra=fatos_compra,
        fatos_venda=fatos_venda,
        fatos_neutro=fatos_neutro,
        alertas=proj.alertas,
    )

    # -------- Texto para WhatsApp --------
    resultado.texto_whatsapp = _montar_texto_whatsapp(resultado, proj)

    return resultado


def _montar_texto_whatsapp(res: ResultadoSugestao, proj: ProjecaoRendimento) -> str:
    """Monta uma mensagem formatada para envio via WhatsApp."""
    emoji = {"COMPRA": "🟢", "VENDA": "🔴", "MANTER": "🟡"}[res.sugestao]
    linhas = [
        f"{emoji} *Aura — Sugestão para {res.ticker}*",
        f"Sugestão: *{res.sugestao.value}* (confiança: {res.confianca})",
        f"Score: {res.score:+.1f} de 5",
        "",
        "*Preço e projeção:*",
        f"• Preço atual: R$ {proj.preco_atual:,.2f}" if proj.preco_atual else "• Preço atual: n/d",
        f"• Preço justo (Gordon): R$ {proj.preco_justo:,.2f}" if proj.preco_justo else "• Preço justo: n/d",
        f"• Upside: {proj.upside:+.1f}%" if proj.upside is not None else "• Upside: n/d",
        f"• Dividend yield: {proj.dy_atual:.1f}%" if proj.dy_atual else "• Dividend yield: n/d",
        f"• Dividendos proj. 12m: R$ {proj.dividendos_12m:,.2f}" if proj.dividendos_12m else "",
        "",
        "*Por que COMPRA:*",
    ]
    if res.fatos_compra:
        for f in res.fatos_compra:
            linhas.append(f"• ✅ {f}")
    else:
        linhas.append("• Nenhum fato favorável identificado.")

    linhas.append("")
    linhas.append("*Por que VENDA:*")
    if res.fatos_venda:
        for f in res.fatos_venda:
            linhas.append(f"• ❌ {f}")
    else:
        linhas.append("• Nenhum fato desfavorável identificado.")

    if res.alertas:
        linhas.append("")
        linhas.append("*⚠️ Atenção:*")
        for a in res.alertas:
            linhas.append(f"• {a}")

    linhas.append("")
    linhas.append("_Aura analisa apenas números públicos (Fundamentus + yfinance). Não é recomendação de compra/venda. Você decide._")

    return "\n".join(l for l in linhas if l is not None)
