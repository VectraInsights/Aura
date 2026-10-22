"""Radar de compra: a IA ordena os candidatos pelo que recomenda PARA ESSE
investidor, separado por classe de ativo.

Diferente da triagem (mecânica, ordenada por um indicador), o radar pede à IA
uma leitura comparada: ela recebe os candidatos com os números, conhece o
perfil de quem pergunta, e devolve o que consideraria comprar agora — e o que
evitaria — dentro de cada classe.

Aviso: a ordem do radar é a leitura da IA sobre números públicos. Não é
recomendação de investimento nem ordem para execução.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .perfil import resumo_para_ia

log = logging.getLogger(__name__)

#: Ativos de outras classes que entram no radar (cripto, moeda, commodity).
#: Vem do ambiente; configurável sem mexer no código.
ATIVOS_EXTRA_PADRAO = [
    "BTC-USD", "ETH-USD", "SOL-USD",
    "USDBRL=X", "EURBRL=X",
    "GC=F",
]


#: Critérios de julgamento por classe. Vão no prompt E são mostrados na
#: interface, para que o investidor saiba exatamente no que a IA se baseia.
CRITERIOS_POR_CLASSE: dict[str, str] = {
    "Ações": (
        "Sustentabilidade do dividendo (cobertura por lucro e por caixa, payout, "
        "histórico de distribuição); qualidade do negócio (ROE e ROIC contra o "
        "preço pago, margens e consistência); nível de dívida (Dív.Líq/PL, "
        "Liquidez Corrente, EV/EBITDA); e crescimento que não dependa de "
        "endividamento. Compare os candidatos ENTRE SI: o que tem o melhor "
        "conjunto de ROE/ROIC para o P/L e o P/VP que custa."
    ),
    "FIIs": (
        "Yield E sua qualidade: dividend yield e FFO yield contra vacância, cap "
        "rate e alavancagem do fundo; preço contra o valor patrimonial (P/VP); "
        "liquidez diária para quem precisa sair; e concentração/segmento do "
        "portfólio de imóveis. Um DY alto com vacância alta e alavancagem alta é "
        "armadilha, não renda."
    ),
    "Cripto": (
        "Aqui não há balanço: julgue pelo comportamento do preço. Use a variação "
        "de 7 dias, 30 dias e 12 meses e a posição na faixa de 52 semanas "
        "(perto do topo indica risco de correção; muito abaixo do topo, em alta "
        "de 12 meses, indica força com folga). Não invente fundamental de cripto."
    ),
    "Moeda": (
        "Julgue o par pela variação recente e pela faixa de 52 semanas, "
        "cruzando com o que faz sentido para o objetivo e o horizonte do "
        "investidor (moeda forte demais reduz o poder de compra; fraca demais "
        "afeta quem importa). Trate com prudência: câmbio não é investimento, é "
        "proteção."
    ),
    "Outros": (
        "Julgue pelo comportamento do preco (variacoes e faixa de 52 semanas) e "
        "pelo encaixe no perfil do investidor."
    ),
}

#: Aviso do que a IA nao tem para julgar (transparencia).
LIMITES_DO_RADAR = (
    "Para cripto, moeda e commodity nao existem indicadores fundamentalistas: a "
    "IA julga apenas variacao de preco e faixa de 52 semanas. Para acoes e FIIs ha "
    "balanco, DRE e 22 indicadores."
)


@dataclass
class Candidato:
    """Um ativo candidato, com os números que a IA vai considerar."""

    classe: str
    ticker: str
    nome: str = ""
    preco: float | None = None
    metricas: dict[str, Any] = field(default_factory=dict)

    def linha(self) -> str:
        partes = [f"{self.ticker}"]
        if self.nome and self.nome != self.ticker:
            partes.append(self.nome)
        if self.preco is not None:
            partes.append(f"preço {_fmt_preco(self.preco)}")
        if self.metricas:
            partes.append(" | ".join(f"{k}: {v}" for k, v in self.metricas.items()))
        return " · ".join(partes)


def _fmt(valor: Any, casas: int = 2, sufixo: str = "") -> str:
    if valor is None or valor != valor:
        return "n/d"
    try:
        return f"{float(valor):,.{casas}f}{sufixo}".replace(",", "@").replace(".", ",").replace("@", ".")
    except (TypeError, ValueError):
        return str(valor)


def _fmt_preco(valor: Any) -> str:
    """Preço com precisão conforme a ordem de grandeza.

    Sem isso, 83.644,4531 fica ambíguo (milhar com vírgula de milhar).
    """
    if valor is None or valor != valor:
        return "n/d"
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    if abs(v) >= 1000:
        return f"{v:,.0f}".replace(",", ".")
    if abs(v) >= 1:
        return f"{v:,.4f}".replace(",", "@").replace(".", ",").replace("@", ".")
    return f"{v:,.6f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _ou_nd(valor: Any, casas: int = 1, sufixo: str = "%") -> str:
    """Zero pode significar 'não informado' — nesse caso vira n/d.

    Crítico nos FIIs: o Fundamentus deixa vacância e cap rate em 0 quando não
    tem o dado, e 0% de vacância lido como número faria a IA ranquear o fundo
    no topo por um motivo que não existe.
    """
    if valor is None or valor != valor:
        return "n/d"
    try:
        if float(valor) == 0:
            return "n/d"
    except (TypeError, ValueError):
        return "n/d"
    return _fmt(valor, casas, sufixo)


def _candidatos_acoes(analista, perfil_triagem: str, limite: int) -> list[Candidato]:
    from .screen import obter_perfil

    perfil = obter_perfil(perfil_triagem)
    df = analista.fundamentus.mercado_completo()
    df["tipo"] = "Ação"
    from .screen.criterios import montar_ranking

    ranking = montar_ranking(df, perfil)
    topo = ranking.head(limite)

    saida: list[Candidato] = []
    for _, linha in topo.iterrows():
        saida.append(
            Candidato(
                classe="Ações",
                ticker=str(linha.get("ticker", "")),
                nome=str(linha.get("nome") or ""),
                preco=linha.get("cotacao"),
                metricas={
                    "DY": _fmt(linha.get("divyield"), 1, "%"),
                    "P/L": _fmt(linha.get("pl"), 1),
                    "P/VP": _fmt(linha.get("pvp"), 2),
                    "EV/EBITDA": _fmt(linha.get("ev_ebitda"), 1),
                    "ROE": _fmt(linha.get("roe"), 1, "%"),
                    "ROIC": _fmt(linha.get("roic"), 1, "%"),
                    "Liq.Corr": _fmt(linha.get("liquidez_corrente"), 2),
                    "Dív.Líq/PL": _fmt(linha.get("div_liq_patrimonio"), 2),
                    "Cresc.5a": _fmt(linha.get("crescimento_receita_5a"), 1, "%"),
                },
            )
        )
    return saida


def _candidatos_fiis(analista, limite: int) -> list[Candidato]:
    """Candidatos a FII, já sem os desvirtuamentos da tabela do Fundamentus.

    Filtros aplicados (e por quê):
      * FFO yield negativo — o fundo pagou mais do que gerou no período;
      * DY acima de 30% — quase sempre distribuição extraordinária ou erro de
        dados (o GCOI11 aparece com 80% e FFO de -1,9%);
      * liquidez diária muito baixa — quem precisa sair não consegue.
    """
    df = analista.fundamentus.mercado_fiis()
    if df.empty:
        return []

    def _coluna(nome: str) -> Any:
        return df[nome] if nome in df.columns else None

    df = df.copy()
    ffo = pd.to_numeric(df["ffo_yield"], errors="coerce") if "ffo_yield" in df.columns else None
    dy = pd.to_numeric(df["divyield"], errors="coerce") if "divyield" in df.columns else None
    liq = pd.to_numeric(df["liquidez_diaria"], errors="coerce") if "liquidez_diaria" in df.columns else None

    mascara = pd.Series(True, index=df.index)
    if ffo is not None:
        mascara &= ffo > 0
    if dy is not None:
        mascara &= (dy >= 2) & (dy <= 30)
    if liq is not None:
        mascara &= liq >= 1000
    df = df[mascara]

    if df.empty:
        return []
    df = df.assign(_ordem=pd.to_numeric(df["ffo_yield"], errors="coerce")).sort_values(
        "_ordem", ascending=False
    ).head(limite)

    saida: list[Candidato] = []
    for _, linha in df.iterrows():
        liquidez = linha.get("liquidez_diaria")
        saida.append(
            Candidato(
                classe="FIIs",
                ticker=str(linha.get("ticker", "")),
                nome=str(linha.get("segmento") or ""),
                preco=linha.get("cotacao"),
                metricas={
                    "DY": _fmt(linha.get("divyield"), 1, "%"),
                    "FFO Yield": _fmt(linha.get("ffo_yield"), 1, "%"),
                    "P/VP": _fmt(linha.get("pvp"), 2),
                    "Vacância": _ou_nd(linha.get("vacancia"), 1, "%"),
                    "Cap Rate": _ou_nd(linha.get("cap_rate"), 1, "%"),
                    "Liquidez/dia": f"R$ {_fmt(liquidez, 0)}" if liquidez else "n/d",
                    "Segmento": str(linha.get("segmento") or "n/d"),
                },
            )
        )
    return saida


def _candidatos_extras(ativos: list[str]) -> list[Candidato]:
    from .monitor.precos import cotacao, resolver, variacoes

    saida: list[Candidato] = []
    for ativo in ativos:
        cot = cotacao(ativo)
        if not cot.valido:
            continue
        simbolo = cot.simbolo
        if simbolo.endswith("-USD") or simbolo.endswith("-EUR"):
            classe = "Cripto"
        elif "BRL=X" in simbolo:
            classe = "Moeda"
        else:
            classe = "Outros"

        var = variacoes(ativo)
        metricas = {
            "Var 7d": _fmt(var.get("var_7d"), 1, "%"),
            "Var 30d": _fmt(var.get("var_30d"), 1, "%"),
            "Var 12m": _fmt(var.get("var_1a"), 1, "%"),
            "Pos. 52s": _fmt(var.get("pos_52"), 1, "%"),
            "Mín 52s": _fmt_preco(var.get("min_52")),
            "Máx 52s": _fmt_preco(var.get("max_52")),
        }
        saida.append(
            Candidato(
                classe=classe,
                ticker=ativo,
                nome=resolver(ativo),
                preco=cot.preco,
                metricas=metricas,
            )
        )
    return saida


def montar_candidatos(
    analista,
    perfil_triagem: str = "qualidade",
    limite: int = 15,
    incluir_fiis: bool = True,
    ativos_extra: list[str] | None = None,
) -> list[Candidato]:
    """Reúne candidatos de todas as classes."""
    candidatos = _candidatos_acoes(analista, perfil_triagem, limite)
    if incluir_fiis:
        candidatos += _candidatos_fiis(analista, limite)
    candidatos += _candidatos_extras(ativos_extra if ativos_extra is not None else ATIVOS_EXTRA_PADRAO)
    return candidatos


def classificar_por_classe(candidatos: list[Candidato]) -> dict[str, list[Candidato]]:
    grupos: dict[str, list[Candidato]] = {}
    for c in candidatos:
        grupos.setdefault(c.classe, []).append(c)
    return grupos


# --------------------------------------------------------------------- prompt
INSTRUCAO_RADAR = """\
Você é um analista sênior de ativos e monta um RADAR DE COMPRA para um investidor
brasileiro específico, usando APENAS os números fornecidos.

Regras:
1. Ordene os candidatos DENTRO de cada classe pela atratividade PARA ESSE
   investidor, considerando o perfil informado. Não repita a ordem original da
   lista: ela é mecânica (ordenada por um indicador) e você deve reordenar.
2. Explique em UMA FRASE por ativo por que ele está ali. Cite o número que
   justifica a posição.
3. Marque como "cuidados" o que pode dar errado com aquele ativo específico.
4. Se um candidato for claramente inadequado ao perfil, coloque-o em "evitar" e
   diga por quê. Prefira poucos candidatos a encher a lista.
5. Se o perfil pedir renda, o critério dominante é a sustentabilidade do
   provento. Se pedir crescimento, é a expansão com disciplina de capital.
6. Não invente números. Dado ausente é "n/d".
7. Formato: JSON conforme o schema. Sem texto fora do JSON.
"""


def construir_prompt_radar(
    candidatos: list[Candidato],
    perfil_investidor: dict[str, Any] | None,
    perfil_triagem: str,
) -> str:
    """Prompt do radar, com os candidatos agrupados por classe."""
    grupos = classificar_por_classe(candidatos)

    partes: list[str] = []
    partes.append("# Radar de compra — candidatos por classe de ativo")
    partes.append(f"Filtro de triagem que originou a lista: {perfil_triagem}")

    if perfil_investidor:
        resumo = resumo_para_ia(perfil_investidor)
        if resumo:
            partes.append("\n## Perfil de quem pergunta\n" + resumo)

    partes.append("\n## Critérios de julgamento por classe\n")
    for classe, itens in grupos.items():
        if not itens:
            continue
        partes.append(f"\n### {classe}")
        partes.append(CRITERIOS_POR_CLASSE.get(classe, CRITERIOS_POR_CLASSE["Outros"]))

    partes.append("\n## Candidatos\n")
    for classe, itens in grupos.items():
        if not itens:
            continue
        partes.append(f"\n### {classe}")
        for i, c in enumerate(itens, 1):
            partes.append(f"{i}. {c.linha()}")

    partes.append(f"\n## Limites desta análise\n{LIMITES_DO_RADAR}")
    partes.append(
        "\n## Tarefa\n\n"
        "Ordene cada classe pela atratividade para este investidor e devolva o JSON "
        "com: resumo, por_classe (destaques com ranking/veredito/motivo/cuidados/nota, "
        "e evitar) e alertas.\n"
        "Se alguma classe não tiver candidato que valha a pena, devolva a lista vazia "
        "e explique no resumo."
    )
    return "\n".join(partes)


def schema_radar():
    """Schema JSON da resposta do radar."""
    from google.genai import types

    destaque = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "ticker": types.Schema(type=types.Type.STRING),
            "ranking": types.Schema(type=types.Type.INTEGER),
            "nota": types.Schema(type=types.Type.INTEGER, description="0 a 10"),
            "veredito": types.Schema(type=types.Type.STRING, description="Uma frase"),
            "motivo": types.Schema(type=types.Type.STRING, description="O número que justifica"),
            "cuidados": types.Schema(type=types.Type.STRING),
        },
        required=["ticker", "ranking", "nota", "veredito", "motivo", "cuidados"],
    )

    por_classe = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "classe": types.Schema(type=types.Type.STRING),
            "destaques": types.Schema(type=types.Type.ARRAY, items=destaque),
            "evitar": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "ticker": types.Schema(type=types.Type.STRING),
                        "motivo": types.Schema(type=types.Type.STRING),
                    },
                    required=["ticker", "motivo"],
                ),
            ),
        },
        required=["classe", "destaques", "evitar"],
    )

    return types.Schema(
        type=types.Type.OBJECT,
        properties={
            "resumo": types.Schema(type=types.Type.STRING),
            "por_classe": types.Schema(type=types.Type.ARRAY, items=por_classe),
            "alertas": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
        },
        required=["resumo", "por_classe", "alertas"],
    )
