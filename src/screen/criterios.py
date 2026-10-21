"""Motor de triagem: perfis de investimento, filtros e pontuação.

Cada perfil devolve filtros aplicáveis direto no formulário do Fundamentus
(que faz o cálculo no servidor) mais, quando aplicável, um ranking local.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class Perfil:
    """Um perfil de triagem."""

    chave: str
    rotulo: str
    descricao: str
    filtros: dict[str, tuple[float | None, float | None]]
    ordenacao: str
    criterios: list[str] = field(default_factory=list)

    def como_texto_criterios(self) -> list[str]:
        """Descrição legível dos filtros, para o prompt e para a UI."""
        ROTULOS = {
            "pl": "P/L", "pvp": "P/VP", "psr": "PSR", "divyield": "Dividend yield",
            "pativos": "P/Ativos", "pebit": "P/EBIT", "ev_ebit": "EV/EBIT",
            "ev_ebitda": "EV/EBITDA", "margem_ebit": "Margem EBIT",
            "margem_liquida": "Margem líquida", "roe": "ROE", "roic": "ROIC",
            "liquidez_corrente": "Liquidez corrente",
            "div_liq_patrimonio": "Dívida líquida/patrimônio",
            "liquidez_diaria": "Liquidez diária (R$/dia)",
            "crescimento_receita_5a": "Crescimento da receita (5a)",
            "patrimonio_liquido": "Patrimônio líquido (R$)",
        }
        partes: list[str] = []
        for chave, (minimo, maximo) in self.filtros.items():
            rotulo = ROTULOS.get(chave, chave)
            if minimo is not None and maximo is not None:
                partes.append(f"{rotulo} entre {minimo:g} e {maximo:g}")
            elif maximo is not None:
                partes.append(f"{rotulo} ≤ {maximo:g}")
            elif minimo is not None:
                partes.append(f"{rotulo} ≥ {minimo:g}")
        return partes


#: Perfis prontos. Os filtros vão direto no formulário do Fundamentus.
PERFIS_TELA: dict[str, Perfil] = {
    "dividendario": Perfil(
        chave="dividendario",
        rotulo="Dividendário",
        descricao="Renda com dividend yield alto e pagamento sustentável.",
        filtros={
            "divyield": (5.0, None),
            "pl": (0.0, 15.0),
            "roe": (8.0, None),
            "liquidez_corrente": (0.8, None),
            "div_liq_patrimonio": (None, 1.0),
            "liquidez_diaria": (500_000.0, None),
        },
        ordenacao="divyield",
    ),
    "valor": Perfil(
        chave="valor",
        rotulo="Valor / Deep Value",
        descricao="Múltiplos baixos com balanço sólido — margem de segurança.",
        filtros={
            "pl": (0.0, 10.0),
            "pvp": (0.0, 1.5),
            "ev_ebitda": (0.0, 8.0),
            "div_liq_patrimonio": (None, 0.8),
            "liquidez_corrente": (0.8, None),
        },
        ordenacao="pl",
    ),
    "qualidade": Perfil(
        chave="qualidade",
        rotulo="Qualidade",
        descricao="Retorno sobre capital e margens consistentes, endividamento controlado.",
        filtros={
            "roe": (12.0, None),
            "roic": (8.0, None),
            "margem_ebit": (8.0, None),
            "div_liq_patrimonio": (None, 1.0),
            "liquidez_corrente": (0.8, None),
        },
        ordenacao="roic",
    ),
    "graham": Perfil(
        chave="graham",
        rotulo="Benjamin Graham (defensivo)",
        descricao=(
            "Os 7 critérios do investimento defensivo de Graham: P/L ≤ 15, P/VP ≤ 1,5, "
            "dívida líquida/PL ≤ 50%, liquidez corrente ≥ 1, ROE ≥ 0, dividend yield ≥ 4% "
            "e preço abaixo do Graham Number."
        ),
        filtros={
            "pl": (0.0, 15.0),
            "pvp": (0.0, 1.5),
            "div_liq_patrimonio": (None, 0.5),
            "liquidez_corrente": (1.0, None),
            "divyield": (4.0, None),
        },
        ordenacao="pvp",
        criterios=[
            "P/L ≤ 15",
            "P/VP ≤ 1,5",
            "Dívida líquida / patrimônio ≤ 50%",
            "Liquidez corrente ≥ 1,0",
            "ROE positivo",
            "Dividend yield ≥ 4%",
            "Preço ≤ Graham Number (√(22,5 × LPA × VPA))",
        ],
    ),
    "bazin": Perfil(
        chave="bazin",
        rotulo="Décio Bazin",
        descricao=(
            "Critérios de Bazin: dividend yield elevado, preço pago abaixo de 1,5× o lucro, "
            "ROE mínimo e pagamento consistente."
        ),
        filtros={
            "divyield": (6.0, None),
            "pl": (0.0, 15.0),
            "roe": (15.0, None),
            "liquidez_corrente": (1.0, None),
            "div_liq_patrimonio": (None, 0.7),
        },
        ordenacao="divyield",
        criterios=[
            "Dividend yield ≥ 6%",
            "P/L ≤ 15 (preço pago ≤ 1,5× o lucro em leitura de Bazin)",
            "ROE ≥ 15%",
            "Liquidez corrente ≥ 1,0",
            "Dívida líquida / patrimônio ≤ 70%",
        ],
    ),
    "conservador": Perfil(
        chave="conservador",
        rotulo="Conservador / Solidez",
        descricao="Prioriza balanço sólido e previsibilidade acima de rentabilidade.",
        filtros={
            "div_liq_patrimonio": (None, 0.5),
            "liquidez_corrente": (1.2, None),
            "roe": (5.0, None),
            "divyield": (3.0, None),
            "liquidez_diaria": (1_000_000.0, None),
        },
        ordenacao="div_liq_patrimonio",
    ),
    "crescimento": Perfil(
        chave="crescimento",
        rotulo="Crescimento",
        descricao="Receita em alta com margens que acompanham.",
        filtros={
            "crescimento_receita_5a": (5.0, None),
            "margem_ebit": (6.0, None),
            "liquidez_corrente": (0.8, None),
        },
        ordenacao="crescimento_receita_5a",
    ),
}


def obter_perfil(chave: str) -> Perfil:
    return PERFIS_TELA.get(chave, PERFIS_TELA["qualidade"])


def graham_number(lpa: float | None, vpa: float | None) -> float | None:
    """Benjamin Graham Number = √(22,5 × EPS × BVPS)."""
    if not lpa or not vpa or lpa <= 0 or vpa <= 0:
        return None
    return (22.5 * lpa * vpa) ** 0.5


def aplicar_perfil(df: pd.DataFrame, perfil: Perfil) -> pd.DataFrame:
    """Aplica os filtros do perfil localmente (redundância com o Fundamentus).

    Serve para: (a) garantir o filtro mesmo se o Fundamentus mudar o formulário,
    (b) permitir ordenar/rankear os resultados.
    """
    if df.empty:
        return df

    mask = pd.Series(True, index=df.index)
    for coluna, (minimo, maximo) in perfil.filtros.items():
        if coluna not in df.columns:
            continue
        serie = pd.to_numeric(df[coluna], errors="coerce")
        if minimo is not None:
            mask &= serie >= minimo
        if maximo is not None:
            mask &= serie <= maximo

    return df[mask].copy()


def ordenar(df: pd.DataFrame, coluna: str, crescente: bool = False) -> pd.DataFrame:
    """Ordena por uma coluna numérica, ignorando NaN (que vão para o fim)."""
    if df.empty or coluna not in df.columns:
        return df
    serie = pd.to_numeric(df[coluna], errors="coerce")
    ordem = serie.sort_values(ascending=crescente, na_position="last")
    return df.loc[ordem.index]


def montar_ranking(df: pd.DataFrame, perfil: Perfil) -> pd.DataFrame:
    """Aplica o perfil, ordena e adiciona colunas auxiliares de leitura."""
    resultado = aplicar_perfil(df, perfil)
    if resultado.empty:
        return resultado

    resultado = ordenar(resultado, perfil.ordenacao)
    resultado = resultado.reset_index(drop=True)
    resultado.insert(0, "posicao", range(1, len(resultado) + 1))
    return resultado


def resumo_colunas(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Metadados das colunas para a UI (rótulo, formato, se é ordenável)."""
    ROTULOS = {
        "ticker": "Ticker", "cotacao": "Cotação (R$)", "pl": "P/L", "pvp": "P/VP",
        "psr": "PSR", "divyield": "Div. Yield (%)", "pativos": "P/Ativos",
        "nome": "Empresa", "tipo_papel": "Classe", "setor": "Setor",
        "pcapgiro": "P/Cap. Giro", "pebit": "P/EBIT", "p_circ_liq": "P/Ativo Circ. Liq.",
        "ev_ebit": "EV/EBIT", "ev_ebitda": "EV/EBITDA", "margem_bruta": "Margem Bruta (%)",
        "margem_ebit": "Margem EBIT (%)", "margem_liquida": "Margem Líquida (%)",
        "liquidez_corrente": "Liq. Corrente", "roic": "ROIC (%)", "roe": "ROE (%)",
        "liquidez_diaria": "Liq. Diária (mi/dia)", "patrimonio_liquido": "Patrim. Líq.",
        "div_liq_patrimonio": "Dív.Líq/PL", "crescimento_receita_5a": "Cresc. Rec. 5a (%)",
    }
    saida = []
    nao_numericas = {"ticker", "nome", "tipo_papel", "setor", "eh_fii"}
    for coluna in df.columns:
        saida.append({
            "chave": coluna,
            "rotulo": ROTULOS.get(coluna, coluna),
            "numerica": coluna not in nao_numericas,
        })
    return saida
