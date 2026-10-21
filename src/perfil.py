"""Perfil do investidor: questionário de onboarding e como a IA deve usá-lo.

O questionário é respondido na primeira entrada. As respostas viajam no cookie de
sessão assinado (sem banco de dados) e são injetadas em cada parecer, para
que a escrita da IA se ajuste ao nível e ao estilo do investidor.
"""

from __future__ import annotations

from typing import Any

#: Questionário do onboarding. "campo" é a chave guardada na sessão.
QUESTOES: list[dict[str, Any]] = [
    {
        "campo": "experiencia",
        "titulo": "Quanto você já operou ações?",
        "ajuda": "Define o quanto a IA pode explicar termos de mercado.",
        "opcoes": [
            {"valor": "nenhuma", "rotulo": "Nunca operei"},
            {"valor": "pouca", "rotulo": "Poucas vezes (menos de 1 ano)"},
            {"valor": "media", "rotulo": "Anos de experiência, estudo por conta própria"},
            {"valor": "profissional", "rotulo": "Atuo no mercado"},
        ],
    },
    {
        "campo": "tolerancia",
        "titulo": "Qual sua tolerância a queda da carteira?",
        "ajuda": "Orienta o peso do risco no parecer.",
        "opcoes": [
            {"valor": "baixa", "rotulo": "Não tolero perdas"},
            {"valor": "media", "rotulo": "Aceito oscilações de até -20%"},
            {"valor": "alta", "rotulo": "Confortável com -35%"},
            {"valor": "maxima", "rotulo": "Busco volatilidade de propósito"},
        ],
    },
    {
        "campo": "horizonte",
        "titulo": "Em quanto tempo pretende usar esse dinheiro?",
        "ajuda": "Curto prazo exige liquididade e menor risco de evento.",
        "opcoes": [
            {"valor": "curto", "rotulo": "Até 1 ano"},
            {"valor": "medio", "rotulo": "1 a 3 anos"},
            {"valor": "longo", "rotulo": "3 a 5 anos"},
            {"valor": "muito_longo", "rotulo": "Mais de 5 anos"},
        ],
    },
    {
        "campo": "objetivo",
        "titulo": "Qual é o objetivo principal?",
        "ajuda": "Define o que a IA prioriza em cada parecer.",
        "opcoes": [
            {"valor": "renda", "rotulo": "Renda mensal"},
            {"valor": "valorizacao", "rotulo": "Valorização de capital"},
            {"valor": "crescimento", "rotulo": "Crescimento acelerado"},
            {"valor": "diversificacao", "rotulo": "Diversificação da carteira"},
        ],
    },
    {
        "campo": "estrategia",
        "titulo": "Qual estratégia te atrai mais?",
        "ajuda": "Sugere o perfil de triagem mais usado.",
        "opcoes": [
            {"valor": "valor", "rotulo": "Comprar barato (Graham, P/VP, EV/EBITDA)"},
            {"valor": "dividendos", "rotulo": "Dividendos consistentes (Bazin)"},
            {"valor": "qualidade", "rotulo": "Empresas de qualidade (ROE, ROIC, margens)"},
            {"valor": "crescimento", "rotulo": "Crescimento (receita, margens)"},
            {"valor": "indeciso", "rotulo": "Não sei, me ajuda a decidir"},
        ],
    },
    {
        "campo": "universo",
        "titulo": "Qual universo prefere?",
        "ajuda": "FIIs têm métricas diferentes de ações — a IA trata cada caso à parte.",
        "opcoes": [
            {"valor": "acoes", "rotulo": "Só ações"},
            {"valor": "misto", "rotulo": "Ações e FIIs"},
            {"valor": "fiis", "rotulo": "Só FIIs"},
            {"valor": "indeciso", "rotulo": "Quero entender antes"},
        ],
    },
    {
        "campo": "tamanho",
        "titulo": "Qual o tamanho aproximado da carteira de renda variável?",
        "ajuda": "Influencia a atenção a liquidez e a custos.",
        "opcoes": [
            {"valor": "pequena", "rotulo": "Até R$ 10 mil"},
            {"valor": "media", "rotulo": "R$ 10 mil a R$ 50 mil"},
            {"valor": "grande", "rotulo": "R$ 50 mil a R$ 200 mil"},
            {"valor": "muito_grande", "rotulo": "Acima de R$ 200 mil"},
        ],
    },
    {
        "campo": "dy_minimo",
        "titulo": "Qual dividend yield mínimo te interessa?",
        "ajuda": "Usado como referência quando o assunto for proventos.",
        "tipo": "numero",
        "padrao": 6,
        "sufixo": "% ao ano",
    },
    {
        "campo": "observacoes",
        "titulo": "Mais alguma coisa que a IA devia saber?",
        "ajuda": "Setores que quer evitar, nomes que já possui, dúvidas recorrentes…",
        "tipo": "texto",
        "placeholder": "Ex.: não quero nada de bank's; já tenho VALE3 e PETR4; me explain Bézier…",
    },
]

#: Rótulos legíveis das opções, para montar o resumo.
ROTULOS: dict[str, dict[str, str]] = {
    q["campo"]: {o["valor"]: o["rotulo"] for o in q.get("opcoes", [])} for q in QUESTOES
}

#: Descrição do nível de conhecimento.
_NIVEIS = {
    "nenhuma": "iniciante absoluto, sem experiência prática em renda variável",
    "pouca": "iniciante, com pouca ou nenhuma experiência prática",
    "media": "investidor intermediário, que estuda o mercado por conta própria",
    "profissional": "investidor avançado, que atua ou atua profissionalmente com renda variável",
}

_TOLERANCIAS = {
    "baixa": "baixa tolerância a queda — a preservação do capital vem primeiro",
    "media": "tolerância moderada a oscilações",
    "alta": "tolerância alta a oscilações",
    "maxima": "busca volatilidade de propósito, focado em assimetria",
}

_HORIZONTES = {
    "curto": "prazo muito curto (até 1 ano) — risco de liquidez e de evento pesa muito",
    "medio": "prazo curto a médio (1 a 3 anos)",
    "longo": "prazo médio a longo (3 a 5 anos)",
    "muito_longo": "prazo longo (mais de 5 anos) — privilegia perenidade",
}

_OBJETIVOS = {
    "renda": "renda mensal — a sustentabilidade dos proventos é o critério central",
    "valorizacao": "valorização de capital — entrada e preço importam mais que o dividendo",
    "crescimento": "crescimento acelerado — potencial de expansão sobre a base atual",
    "diversificacao": "diversificação de carteira — exposição a setores diferentes importa",
}

_ESTRATEGIAS = {
    "valor": "comprar barato, olhando múltiplos contra o balanço",
    "dividendos": "dividendos consistentes, no estilo Bazin",
    "qualidade": "empresas de qualidade, com retorno sobre capital e margens",
    "crescimento": "crescimento de receita e expansão de margens",
    "indeciso": "ainda sem estratégia definida — apresente as alternativas com clareza",
}

_UNIVERSOS = {
    "acoes": "foco apenas em ações",
    "misto": "ações e FIIs",
    "fiis": "foco apenas em FIIs",
    "indeciso": "ainda não definiu entre ações e FIIs",
}

_TAMANHOS = {
    "pequena": "carteira pequena",
    "media": "carteira de tamanho médio",
    "grande": "carteira grande",
    "muito_grande": "carteira muito grande",
}


def perfil_completo(perfil: dict[str, Any] | None) -> bool:
    """O onboarding foi respondido?"""
    if not perfil:
        return False
    obrigatorias = {"experiencia", "tolerancia", "horizonte", "objetivo"}
    return obrigatorias.issubset({k for k, v in perfil.items() if v not in (None, "")})


def rotulo(campo: str, valor: Any) -> str:
    """Rótulo legível de uma resposta."""
    if valor in (None, ""):
        return "n/d"
    return ROTULOS.get(campo, {}).get(str(valor), str(valor))


def resumo_para_ia(perfil: dict[str, Any] | None) -> str:
    """Texto com o perfil, para ir dentro do prompt.

    Formato: fatos (rotulos) + um parágrafo de instruções de estilo.
    """
    if not perfil:
        return ""

    linhas: list[str] = []
    if perfil.get("experiencia"):
        linhas.append(f"- Nível de conhecimento: {_NIVEIS.get(perfil['experiencia'], 'n/d')}.")
    if perfil.get("tolerancia"):
        linhas.append(f"- Tolerância a risco: {_TOLERANCIAS.get(perfil['tolerancia'], 'n/d')}.")
    if perfil.get("horizonte"):
        linhas.append(f"- Horizonte de investimento: {_HORIZONTES.get(perfil['horizonte'], 'n/d')}.")
    if perfil.get("objetivo"):
        linhas.append(f"- Objetivo principal: {_OBJETIVOS.get(perfil['objetivo'], 'n/d')}.")
    if perfil.get("estrategia"):
        linhas.append(f"- Estratégia de interesse: {_ESTRATEGIAS.get(perfil['estrategia'], 'n/d')}.")
    if perfil.get("universo"):
        linhas.append(f"- Universo preferido: {_UNIVERSOS.get(perfil['universo'], 'n/d')}.")
    if perfil.get("tamanho"):
        linhas.append(f"- Porte da carteira: {_TAMANHOS.get(perfil['tamanho'], 'n/d')}.")
    if perfil.get("dy_minimo") not in (None, ""):
        linhas.append(f"- Dividend yield mínimo de interesse: {perfil['dy_minimo']}% ao ano.")
    if perfil.get("observacoes"):
        linhas.append(f"- Observações do investidor: {perfil['observacoes']}")

    return "\n".join(linhas)


def instrucoes_estilo(perfil: dict[str, Any] | None) -> str:
    """Instruções adicionais de estilo, anexadas à instrução de sistema."""
    if not perfil:
        return ""

    instrucoes: list[str] = []

    nivel = perfil.get("experiencia")
    if nivel in ("nenhuma", "pouca"):
        instrucoes.append(
            "O investidor é INICIANTE: explique cada termo técnico na primeira ocorrência "
            "(por exemplo, o que é ROIC e o que significa payout), use analogias simples "
            "e diga o que ele precisa olhar para acompanhar a empresa."
        )
    elif nivel == "media":
        instrucoes.append(
            "O investidor é intermediário: use o vocabulário técnico do mercado, mas "
            "explique conclusões que dependam de julgamento (ex.: por que o payout "
            "pode ser insustentável)."
        )
    else:
        instrucoes.append(
            "O investidor é avançado: seja direto e técnico. Não explique o básico; "
            "concentre-se no que é não óbvio, nos riscos escondidos e nos números que "
            "importam e não aparecem no texto."
        )

    tolerancia = perfil.get("tolerancia")
    if tolerancia in ("baixa", "media"):
        instrucoes.append(
            "Destaque o QUE PODE DAR ERRADO e a perda potencial de capital; trate "
            "liquidez e solidez do balanço como factores de primeira linha."
        )
    elif tolerancia == "maxima":
        instrucoes.append(
            "Destaque o potencial de assimetria e o que seria um erro de dimensionamento "
            "de posição; não descarte premissas pessimistas por serem exageradas."
        )

    horizonte = perfil.get("horizonte")
    if horizonte == "curto":
        instrucoes.append(
            "O horizonte é curto: avise explicitamente sobre risco de evento, "
            "iliquidez e janelas de saída ruins."
        )
    elif horizonte in ("longo", "muito_longo"):
        instrucoes.append(
            "O horizonte é longo: priorize perenidade do negócio e qualidade da "
            "gestão sobre oscilações de curto prazo."
        )

    objetivo = perfil.get("objetivo")
    if objetivo == "renda":
        instrucoes.append(
            "O objetivo é RENDA: avalie a sustentabilidade dos proventos (cobertura por "
            "lucro e por caixa, política de payout, histórico de distribuição) como "
            "questão central, não como item secundário."
        )
    elif objetivo == "valorizacao":
        instrucoes.append(
            "O objetivo é VALORIZAÇÃO: foque em margem de segurança, entrada e o que "
            "precisa mudar no thesis para o preço se justificar."
        )
    elif objetivo == "crescimento":
        instrucoes.append(
            "O objetivo é CRESCIMENTO: foque em expansão de receita e margens, e no "
            "custo de capital necessário para sustentar esse crescimento."
        )
    elif objetivo == "diversificacao":
        instrucoes.append(
            "O objetivo é DIVERSIFICAÇÃO: comente em que setor/setores a empresa "
            "exposição, para avaliar se ela diversifica ou concentra a carteira."
        )

    estrategia = perfil.get("estrategia")
    if estrategia == "indeciso":
        instrucoes.append(
            "O investidor ainda não definiu estratégia: apresente as alternativas de "
            "maneira comparável e indique qual se encaixa melhor no objetivo declarado."
        )

    universo = perfil.get("universo")
    if universo == "fiis":
        instrucoes.append(
            "O investidor foca em FIIs: use métricas de fundo (P/VP, dividend yield, "
            "vacância, alavancagem) e explique o que P/L e EV/EBITDA significam para "
            "um fundo."
        )
    elif universo == "acoes":
        instrucoes.append(
            "O investidor foca em ações: ignore FUNDAMENTOS IMOBILIÁRIOS ou avise "
            "quando uma empresa da lista for na verdade um FII."
        )

    observacoes = str(perfil.get("observacoes") or "").strip()
    if observacoes:
        instrucoes.append(
            f"Pedido explícito do investidor, a respeitar: {observacoes}"
        )

    return "\n".join(instrucoes)
