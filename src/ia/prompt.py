"""Construção do prompt analítico e do schema de saída estruturada.

Filosofia: a IA recebe números reais e formatados (nada de "descrição" vaga).
Tudo que ela afirmar precisa ser rastreável a um número fornecido; o schema de
saída força o modelo a declarar a confiança e a listar lacunas de dados.
"""

from __future__ import annotations

from typing import Any

from ..util import formatar_num, formatar_pct, formatar_num  # noqa: F401  (formatar_num reexportado)
from ..util import parse_br
from ..perfil import resumo_para_ia

INSTRUCAO_SISTEMA = """\
Você é um analista sênior de ações listadas na B3, especializado em leitura de
balanço patrimonial e demonstrações financeiras (CNPI/CVM) e em técnicas de valor
(Décio Bazin, Benjamin Graham, Greenblatt).

REGRAS INEGOCIÁVEIS:
1. Use EXCLUSIVAMENTE os números fornecidos no prompt. Nunca invente, estime ou
   complete de memória valores ausentes. Dado faltante = "n/d".
2. Sempre cite a origem e o período do número que estiver usando ("balanço de
   30/06/2026", "DRE últimos 12 meses", "cotação de 21/10/2026").
3. Distinga o que é FATO (número do documento) do que é INTERPRETAÇÃO (sua leitura).
   Use os campos "endividamento", "geracao_caixa" e "qualidade_lucros" para a leitura.
4. Se os dados forem insuficientes para concluir algo, diga isso no campo "alertas"
   e reduza a "nota" e a "confianca". Não preencha lacuna com suposição.
5. Escreva em português do Brasil, com vocabulário técnico de mercado.
6. Não emita recomendação de compra/venda. O veredito é uma leitura técnica
   (o usuário decide). Nunca prometa retorno.
7. Fale da empresa pelo nome e ticker. Quando o ticker tiver sufixo de classe
   (ex.: PETR4 = PN, PETR3 = ON), respeite a classe: dividendendos e Covenant do
   passivo podem diferir entre elas.
8. Tamanho: o campo "resumo" deve ter 2 a 3 parágrafos densos (sem enrolação).
   "riscos" e "catalisadores": 3 a 5 itens curtos e específicos, com número quando
   fizer sentido.
"""

PERFIS_ANALISE: dict[str, dict[str, str]] = {
    "dividendario": {
        "rotulo": "Foco em dividendo",
        "descricao": (
            "Foco em dividendo e sua sustentabilidade: analise a cobertura de dividendos "
            "pelo lucro e pelo FCF, a política de payout, a consistência do histórico "
            "de pagamentos e o risco de corte (endividamento, ciclo do setor, caixa)."
        ),
        "foco": (
            "Priorize: (a) se o dividend yield informado é sustentável no médio prazo, "
            "usando lucro e geração de caixa dos números fornecidos; (b) risco de "
            "redução ou suspensão do dividendo; (c) se o preço paga um yield razoável "
            "para o risco da dívida."
        ),
    },
    "valor": {
        "rotulo": "Foco em valor",
        "descricao": (
            "Foco em margem de segurança: múltiplos (P/L, P/VP, EV/EBITDA), distância "
            "para o valor intrínseco estimado e qualidade do passivo."
        ),
        "foco": (
            "Priorize: (a) leitura dos múltiplos contra os números de balanço; "
            "(b) qualidade eQui_abordagem do lucro (recorrente x. não recorrente, "
            "ciclicidade); (c) margem de segurança; (d) o que precisaria mudar para "
            "o preço atual ser justo."
        ),
    },
    "qualidade": {
        "rotulo": "Foco em qualidade",
        "descricao": (
            "Foco na saúde financeira: estrutura de capital, cobertura de juros, "
            "geração de caixa vs. lucro e consistência ao longo do tempo."
        ),
        "foco": (
            "Priorize: (a) endividamento e prazo de vencimento; (b) conversão de lucro "
            "em caixa; (c) retorno sobre capital (ROE/ROIC) frente ao custo do passivo; "
            "(d) consistência e volatilidade dos resultados."
        ),
    },
    "crescimento": {
        "rotulo": "Foco em crescimento",
        "descricao": (
            "Foco em crescimento com sustainable: crescimento de receita, expansão de "
            "margem e reinvestimento do capital gerado."
        ),
        "foco": (
            "Priorize: (a) crescimento da receita informado; (b) evolução das margens "
            "(bruta, EBIT e líquida); (c) se o caixa gerado sustenta o crescimento "
            "sem aumento proporcional de dívida; (d) riscos de saturação do setor."
        ),
    },
    "conservador": {
        "rotulo": "Foco em solidez",
        "descricao": (
            "Foco em conservador: o que pode dar errado. Prioriza balanço sólido, "
            "geração de caixa previsível e ausência de gatilhos de crise."
        ),
        "foco": (
            "Priorize: (a) pior cenário plausível a partir dos números; (b) gatilhos "
            "de deterioração; (c) folga de balanço; (d) o que o investidor deve "
            "monitorar trimestralmente."
        ),
    },
}


def instrucao_sistema_para(perfil_investidor: dict[str, Any] | None = None) -> str:
    """Instrução de sistema acrescida dos ajustes do perfil do investidor."""
    from ..perfil import instrucoes_estilo

    estilo = instrucoes_estilo(perfil_investidor)
    if not estilo:
        return INSTRUCAO_SISTEMA
    return (
        INSTRUCAO_SISTEMA
        + "\n\nAJUSTES PARA O PERFIL DESTE INVESTIDOR (obedecer):\n"
        + estilo
    )


def _linha(rotulo: str, valor: Any, formatter=None) -> str:
    if valor is None:
        return f"- {rotulo}: n/d"
    if formatter is not None:
        return f"- {rotulo}: {formatter(valor)}"
    return f"- {rotulo}: {valor}"


def _secao(titulo: str, linhas: list[str]) -> str:
    if not linhas:
        return ""
    return f"\n## {titulo}\n" + "\n".join(linhas) + "\n"


def construir_prompt(
    ticker: str,
    nome: str | None,
    indicadores: dict[str, Any],
    balanco: dict[str, Any],
    dre_anual: dict[str, Any],
    dre_trimestral: dict[str, Any],
    perfil: str = "qualidade",
    cotacao: float | None = None,
    valor_mercado: float | None = None,
    ultimo_balanco: str | None = None,
    proventos: dict[str, Any] | None = None,
    performance: dict[str, Any] | None = None,
    criterios_tela: list[str] | None = None,
    tipo: str = "Ação",
    setor: str | None = None,
    subsetor: str | None = None,
    nro_acoes: float | None = None,
    valor_firma: float | None = None,
    perfil_investidor: dict[str, Any] | None = None,
) -> str:
    """Monta o prompt com todos os números disponíveis da empresa."""
    perfil = perfil if perfil in PERFIS_ANALISE else "qualidade"
    cfg_perfil = PERFIS_ANALISE[perfil]
    eh_fii = (tipo or "").upper() == "FII"

    partes: list[str] = []
    partes.append(f"# Empresa: {nome or ticker} ({ticker})")
    partes.append(f"Tipo de papel: {tipo}" + (f" — {subsetor or setor}" if (setor or subsetor) else ""))
    partes.append(f"Perfil de análise: {cfg_perfil['rotulo']}")

    if eh_fii:
        partes.append(
            "\n> **ATENÇÃO — este papel é um FUNDO IMOBILIÁRIO (FII), não uma companhia aberta.** "
            "As métricas de corporate governance, EV/EBITDA e ROE de um fundo não são "
            "comparáveis às de uma ação: o fundo é uma condominium, o dividend yield é "
            "mensal e distribuído com base na CAIXA (renda distribuível), não no lucro contábil. "
            "Priorize: P/VP (desconto ou prêmio sobre o valor patrimonial), dividend yield, "
            "qualidade e localização dos imóveis, vacância, e nível de alavancagem do fundo. "
            "Se algum indicador não fizer sentido para um FII, ignore-o em vez de interpretá-lo."
        )

    if ultimo_balanco:
        partes.append(f"Data de referência do último balanço processado: {ultimo_balanco}")

    # ---------------------------------------------------------- cotação
    linhas: list[str] = []
    if cotacao is not None:
        linhas.append(_linha("Cotação atual (R$)", cotacao, lambda v: formatar_num(v)))
    if valor_mercado is not None:
        from ..util import parse_numero_curto

        linhas.append(_linha("Valor de mercado", valor_mercado, parse_numero_curto))
    if valor_firma is not None:
        from ..util import parse_numero_curto

        linhas.append(_linha("Valor da firma (enterprise value)", valor_firma, parse_numero_curto))
    if nro_acoes is not None:
        linhas.append(_linha("Número de ações em circulação", nro_acoes, lambda v: f"{v:,.0f}".replace(",", ".")))
    if performance:
        for chave, rotulo in [
            ("dia", "Variação no dia"),
            ("mes", "Variação no mês"),
            ("12 meses", "Variação em 12 meses"),
            ("2026", "Variação em 2026"),
        ]:
            if performance.get(chave):
                linhas.append(f"- {rotulo}: {performance[chave]}")
    partes.append(_secao("Cotação e mercado", linhas))

    # ---------------------------------------------------------- indicadores
    ROTULOS = {
        "pl": "P/L (preço/lucro)",
        "pvp": "P/VP (preço/valor patrimonial)",
        "psr": "PSR (preço/receita)",
        "pebit": "P/EBIT",
        "ev_ebit": "EV/EBIT",
        "ev_ebitda": "EV/EBITDA",
        "divyield": "Dividend yield",
        "margem_bruta": "Margem bruta",
        "margem_ebit": "Margem EBIT",
        "margem_liquida": "Margem líquida",
        "roe": "ROE (retorno sobre patrimônio)",
        "roic": "ROIC (retorno sobre capital investido)",
        "liquidez_corrente": "Liquidez corrente",
        "div_liq_patrimonio": "Dívida líquida / patrimônio",
        "crescimento_receita_5a": "Crescimento da receita (5 anos)",
        "lpa": "LPA (lucro por ação)",
        "vpa": "VPA (valor patrimonial por ação)",
        "pativos": "P/Ativos",
        "ebit_ativo": "EBIT / Ativo",
        "giro_ativos": "Giro de ativos",
    }
    pct_set = {
        "divyield", "margem_bruta", "margem_ebit", "margem_liquida",
        "roe", "roic", "crescimento_receita_5a",
    }
    linhas = []
    for chave, rotulo in ROTULOS.items():
        if chave not in indicadores:
            continue
        valor = indicadores[chave]
        if valor is None:
            continue
        if chave in pct_set:
            linhas.append(_linha(rotulo, valor, lambda v: formatar_pct(v)))
        else:
            linhas.append(_linha(rotulo, valor, lambda v: formatar_num(v)))
    partes.append(_secao("Indicadores fundamentalistas", linhas))

    # ---------------------------------------------------------- balanço
    ROTULOS_BALANCO = {
        "ativo": "Ativo total",
        "disponibilidades": "Disponibilidades (caixa e equivalentes)",
        "ativo_circulante": "Ativo circulante",
        "patrimonio_liquido": "Patrimônio líquido",
        "div_bruta": "Dívida bruta",
        "div_liquida": "Dívida líquida",
    }
    from ..util import parse_numero_curto

    linhas = []
    for chave, rotulo in ROTULOS_BALANCO.items():
        if chave in balanco and balanco[chave] is not None:
            linhas.append(_linha(rotulo, balanco[chave], parse_numero_curto))
    if linhas:
        linhas.append("")
        linhas.append("Razões calculadas a partir do balanço acima:")
        ev = _razoes_balanco(balanco)
        for rotulo, valor in ev:
            linhas.append(f"- {rotulo}: {valor}")
    partes.append(_secao("Balanço patrimonial", linhas))

    # ---------------------------------------------------------- DRE
    ROTULOS_DRE = {
        "receita_liquida": "Receita líquida",
        "ebit": "EBIT",
        "lucro_liquido": "Lucro líquido",
    }
    linhas = []
    for chave, rotulo in ROTULOS_DRE.items():
        if chave in dre_anual and dre_anual[chave] is not None:
            linhas.append(_linha(f"{rotulo} (últimos 12 meses)", dre_anual[chave], parse_numero_curto))
    partes.append(_secao("Demonstração de resultados (últimos 12 meses)", linhas))

    linhas = []
    for chave, rotulo in ROTULOS_DRE.items():
        if chave in dre_trimestral and dre_trimestral[chave] is not None:
            linhas.append(_linha(f"{rotulo} (últimos 3 meses)", dre_trimestral[chave], parse_numero_curto))
    if linhas:
        partes.append(_secao("Demonstração de resultados (últimos 3 meses)", linhas))

    # ---------------------------------------------------------- proventos
    if proventos:
        linhas = []
        if proventos.get("dividendos_12m_total") is not None:
            linhas.append(_linha("Total de dividendos pagos (12 meses)", proventos["dividendos_12m_total"], parse_numero_curto))
        if proventos.get("dividendos_12m_por_acao") is not None:
            linhas.append(_linha("Dividendos por ação (12 meses, R$)", proventos["dividendos_12m_por_acao"], lambda v: formatar_num(v)))
        if proventos.get("payout") is not None:
            linhas.append(_linha("Payout (dividendo por ação / LPA)", proventos["payout"], lambda v: formatar_pct(v)))
        if proventos.get("n_pagamentos") is not None:
            linhas.append(f"- Número de pagamentos no período: {proventos['n_pagamentos']}")
        if proventos.get("ultimo"):
            linhas.append(f"- Último pagamento: {proventos['ultimo']}")
        partes.append(_secao("Proventos", linhas))

    # ---------------------------------------------------------- tela
    if criterios_tela:
        partes.append(
            "\n## Critérios de triagem que a empresa superou\n"
            + "\n".join(f"- {c}" for c in criterios_tela)
            + "\n"
        )

    # ---------------------------------------------------------- investidor
    if perfil_investidor:
        resumo = resumo_para_ia(perfil_investidor)
        if resumo:
            partes.append(
                "\n## Quem é o investidor (contexto do usuário)\n"
                "Use este contexto para calibrar o parecer — o que ele sabe, o que "
                "ele busca e o que ele aceita.\n"
                + resumo
                + "\n"
            )

    # ---------------------------------------------------------- pedido
    partes.append(
        f"""
## Tarefa

{cfg_perfil['descricao']}

{cfg_perfil['foco']}

Produza o parecer técnico no formato JSON exigido pelo schema.

Checklist antes de responder:
- Todo número citado no texto aparece nos dados acima?
- Identifiquei a data/período de cada número usado?
- Declarei explicitamente o que NÃO está nos dados e o que isso impede de concluir?
- A nota reflete a leitura dos números, e não uma preferência pessoal?
"""
    )

    return "\n".join(partes)


def _razoes_balanco(balanco: dict[str, Any]) -> list[tuple[str, str]]:
    """Deriva razões simples do balanço (dívida/patrimônio, caixa/passivo, etc.)."""
    saida: list[tuple[str, str]] = []
    patrimonio = parse_br(balanco.get("patrimonio_liquido"))
    div_liquida = parse_br(balanco.get("div_liquida"))
    div_bruta = parse_br(balanco.get("div_bruta"))
    caixa = parse_br(balanco.get("disponibilidades"))
    ativo_circ = parse_br(balanco.get("ativo_circulante"))
    ativo = parse_br(balanco.get("ativo"))

    if div_liquida is not None and patrimonio:
        saida.append(("Dívida líquida / patrimônio", formatar_pct(div_liquida / patrimonio)))
    if div_bruta is not None and patrimonio:
        saida.append(("Dívida bruta / patrimônio", formatar_pct(div_bruta / patrimonio)))
    if div_liquida is not None and ativo:
        saida.append(("Dívida líquida / ativo total", formatar_pct(div_liquida / ativo)))
    if caixa is not None and ativo_circ:
        saida.append(("Caixa / ativo circulante", formatar_pct(caixa / ativo_circ)))
    if patrimonio is not None and ativo:
        saida.append(("Patrimônio líquido / ativo total", formatar_pct(patrimonio / ativo)))
    return saida


def schema_relatorio():
    """Schema JSON (google-genai) que o modelo deve respeitar."""
    from google.genai import types

    return types.Schema(
        type=types.Type.OBJECT,
        properties={
            "ticker": types.Schema(type=types.Type.STRING, description="Ticker analisado"),
            "resumo": types.Schema(
                type=types.Type.STRING,
                description="2 a 3 parágrafos densos com a leitura técnica dos números",
            ),
            "nota": types.Schema(
                type=types.Type.INTEGER,
                description="Nota de qualidade/sustentabilidade de 0 a 10 (inteiro)",
            ),
            "confianca": types.Schema(
                type=types.Type.STRING,
                enum=["Alta", "Média", "Baixa"],
                description="Confiança na análise, conforme a qualidade dos dados disponíveis",
            ),
            "veredito": types.Schema(
                type=types.Type.STRING,
                description="1 ou 2 frases de conclusão técnica (sem recomendação de compra/venda)",
            ),
            "endividamento": types.Schema(
                type=types.Type.STRING,
                description="Leitura do endividamento e da estrutura de capital, citando números",
            ),
            "geracao_caixa": types.Schema(
                type=types.Type.STRING,
                description="Leitura da geração de caixa e sua relação com o lucro, citando números",
            ),
            "qualidade_lucros": types.Schema(
                type=types.Type.STRING,
                description="Leitura da qualidade, recorrência e ciclicidade dos resultados",
            ),
            "riscos": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="3 a 5 riscos específicos, com número quando aplicável",
            ),
            "catalisadores": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="3 a 5 catalisadores potenciais, com número quando aplicável",
            ),
            "alertas": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="Lacunas de dados e ressalvas metodológicas",
            ),
            "o_que_monitorar": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="3 a 5 indicadores a acompanhar nos próximos trimestres",
            ),
        },
        required=[
            "ticker", "resumo", "nota", "confianca", "veredito",
            "endividamento", "geracao_caixa", "qualidade_lucros",
            "riscos", "catalisadores", "alertas", "o_que_monitorar",
        ],
    )
