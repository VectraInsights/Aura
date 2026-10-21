"""Cliente do Fundamentus (statusinvest/fundamentus) — fonte primária de fundamentalistas da B3.

Duas operações:
  * buscar_mercado(): POST em resultado.php -> varre a B3 inteira (395+ papéis) numa requisição.
  * detalhes():       GET em detalhes.php?papel=X -> indicadores, balanço e DRE de uma empresa.

O site é codificado em ISO-8859-1 e não documenta API pública, então usamos o mesmo
formulário que o site usa. Tudo é cacheado em disco (ver src/dados/cache.py).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import CONFIG
from ..util import limpar_label, parse_br
from .cache import Cache

log = logging.getLogger(__name__)

BASE = "https://fundamentus.com.br"

#: Sobe quando o formato de parsing muda, para invalidar o cache antigo.
VERSAO_CACHE = "v5"

# Cabeçalho da tabela de resultados -> nome interno da coluna.
COLUNAS_MERCADO: dict[str, str] = {
    "Papel": "ticker",
    "Cotação": "cotacao",
    "P/L": "pl",
    "P/VP": "pvp",
    "PSR": "psr",
    "Div.Yield": "divyield",
    "P/Ativo": "pativos",
    "P/Cap.Giro": "pcapgiro",
    "P/EBIT": "pebit",
    "P/Ativ Circ.Liq": "p_circ_liq",
    "EV/EBIT": "ev_ebit",
    "EV/EBITDA": "ev_ebitda",
    "Mrg Bruta": "margem_bruta",
    "Mrg Ebit": "margem_ebit",
    "Mrg. Líq.": "margem_liquida",
    "Liq. Corr.": "liquidez_corrente",
    "ROIC": "roic",
    "ROE": "roe",
    "Liq.2meses": "liquidez_diaria",
    "Patrim. Líq": "patrimonio_liquido",
    "Dív.Líq/ Patrim.": "div_liq_patrimonio",
    "Cresc. Rec.5a": "crescimento_receita_5a",
}

# Critérios de tela -> (campo min, campo max) do formulário.
CAMPOS_CRITERIO: dict[str, tuple[str, str]] = {
    "pl": ("pl_min", "pl_max"),
    "pvp": ("pvp_min", "pvp_max"),
    "psr": ("psr_min", "psr_max"),
    "divyield": ("divy_min", "divy_max"),
    "pativos": ("pativos_min", "pativos_max"),
    "pcapgiro": ("pcapgiro_min", "pcapgiro_max"),
    "pebit": ("pebit_min", "pebit_max"),
    "p_circ_liq": ("fgrah_min", "fgrah_max"),
    "ev_ebit": ("firma_ebit_min", "firma_ebit_max"),
    "ev_ebitda": ("firma_ebitda_min", "firma_ebitda_max"),
    "margem_ebit": ("margemebit_min", "margemebit_max"),
    "margem_liquida": ("margemliq_min", "margemliq_max"),
    "liquidez_corrente": ("liqcorr_min", "liqcorr_max"),
    "roic": ("roic_min", "roic_max"),
    "roe": ("roe_min", "roe_max"),
    "liquidez_diaria": ("liq_min", "liq_max"),
    "patrimonio_liquido": ("patrim_min", "patrim_max"),
    "div_liq_patrimonio": ("divliquida_min", "divliquida_max"),
    "crescimento_receita_5a": ("tx_cresc_rec_min", "tx_cresc_rec_max"),
}

# Índice de ordenação aceito pelo formulário (valor do <select name="ordem">).
ORDEM_POR_COLUNA: dict[str, int] = {
    "pl": 1, "pvp": 2, "psr": 3, "divyield": 4, "pativos": 5, "pcapgiro": 6,
    "p_circ_liq": 7, "pebit": 8, "ev_ebit": 9, "ev_ebitda": 10, "margem_ebit": 11,
    "margem_liquida": 12, "liquidez_corrente": 13, "roic": 14, "roe": 15,
    "liquidez_diaria": 16, "patrimonio_liquido": 17, "div_liq_patrimonio": 18,
    "crescimento_receita_5a": 19,
}

SETORES: dict[str, int] = {
    "Agropecuária": 1, "Água e Saneamento": 2, "Alimentos Processados": 3,
    "Serv.Méd.Hospit.": 4, "Automóveis e Motocicletas": 5, "Bebidas": 6, "Comércio": 7,
    "Comércio e Distribuição": 8, "Computadores e Equipamentos": 9, "Construção Civil": 10,
    "Construção e Engenharia": 11, "Diversos": 12, "Embalagens": 13, "Energia Elétrica": 14,
    "Equipamentos": 15, "Exploração de Imóveis": 16, "Gás": 17, "Holdings Diversificadas": 18,
    "Hotéis e Restaurantes": 19, "Intermediários Financeiros": 20, "Madeira e Papel": 21,
    "Máquinas e Equipamentos": 22, "Materiais Diversos": 23, "Material de Transporte": 24,
    "Medicamentos e Outros Produtos": 25, "Mídia": 26, "Mineração": 27, "Outros": 28,
    "Outros Títulos": 29, "Petróleo, Gás e Biocombustíveis": 30, "Previdência e Seguros": 31,
    "Produtos de Uso Pessoal e Limpeza": 32, "Programas e Serviços": 33, "Químicos": 34,
    "Securitizadoras de Recebíveis": 35, "Serviços Diversos": 36,
    "Serviços Financeiros Diversos": 37, "Siderurgia e Metalurgia": 38,
    "Tecidos, Vestuário e Calçados": 39, "Telecomunicações": 40, "Transporte": 41,
    "Utilidades Domésticas": 42, "Viagens e Lazer": 43,
}


class ErroFundamentus(RuntimeError):
    """Falha ao falar com o Fundamentus."""


@dataclass
class DetalheEmpresa:
    """Indicadores, balanço e DRE de uma empresa."""

    ticker: str
    cotacao: float | None = None
    valor_mercado: float | None = None
    ultimo_balanco: str | None = None
    tipo_papel: str | None = None
    empresa: str | None = None
    setor: str | None = None
    subsetor: str | None = None
    data_ult_cot: str | None = None
    valor_firma: float | None = None
    nro_acoes: float | None = None
    vol_medio_2m: float | None = None
    min_52sem: float | None = None
    max_52sem: float | None = None
    indicadores: dict[str, float | None] = field(default_factory=dict)
    indicadores_rotulos: dict[str, str] = field(default_factory=dict)
    balanco: dict[str, float | None] = field(default_factory=dict)
    balanco_rotulos: dict[str, str] = field(default_factory=dict)
    dre_anual: dict[str, float | None] = field(default_factory=dict)
    dre_trimestral: dict[str, float | None] = field(default_factory=dict)
    oscilacoes: dict[str, str] = field(default_factory=dict)

    @property
    def eh_fii(self) -> bool:
        """FIIs listados pelo Fundamentus aparecem com o tipo 'ON NM'.

        Verificado contra unidades conhecidas (ALLD3, RIAA3, LOGG3, LPSB3, CURY3
        -> ON NM) versus ações e unidades societárias (POMO3 -> ON, POMO4 -> PN N2,
        CMIN3 -> ON N2, VULC3 -> ON).
        """
        return "NM" in (self.tipo_papel or "").upper()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "cotacao": self.cotacao,
            "valor_mercado": self.valor_mercado,
            "ultimo_balanco": self.ultimo_balanco,
            "tipo_papel": self.tipo_papel,
            "empresa": self.empresa,
            "setor": self.setor,
            "subsetor": self.subsetor,
            "data_ult_cot": self.data_ult_cot,
            "valor_firma": self.valor_firma,
            "nro_acoes": self.nro_acoes,
            "vol_medio_2m": self.vol_medio_2m,
            "min_52sem": self.min_52sem,
            "max_52sem": self.max_52sem,
            "indicadores": self.indicadores,
            "balanco": self.balanco,
            "dre_anual": self.dre_anual,
            "dre_trimestral": self.dre_trimestral,
            "oscilacoes": self.oscilacoes,
        }


class FundamentusClient:
    def __init__(self, cache: Cache | None = None, usar_cache: bool = True):
        self.cache = cache or Cache()
        self.usar_cache = usar_cache
        self.sessao = requests.Session()
        self.sessao.headers.update(CONFIG.headers)
        retry = Retry(
            total=3, backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        self.sessao.mount("https://", adapter)
        self.sessao.mount("http://", adapter)

    # ------------------------------------------------------------------ interno
    @staticmethod
    def _decodificar(resposta: requests.Response) -> str:
        """O site serve ISO-8859-1; confiamos no header e no meta como fallback."""
        encoding = (resposta.encoding or "").lower()
        if encoding and encoding not in {"iso-8859-1", "latin-1"}:
            return resposta.text
        return resposta.content.decode("iso-8859-1", errors="replace")

    def _get(self, url: str, params: dict | None = None) -> str:
        try:
            resposta = self.sessao.get(url, params=params, timeout=30)
            resposta.raise_for_status()
        except requests.RequestException as exc:
            raise ErroFundamentus(f"Falha ao acessar {url}: {exc}") from exc
        return self._decodificar(resposta)

    def _post(self, url: str, data: dict) -> str:
        try:
            resposta = self.sessao.post(url, data=data, timeout=45)
            resposta.raise_for_status()
        except requests.RequestException as exc:
            raise ErroFundamentus(f"Falha ao acessar {url}: {exc}") from exc
        return self._decodificar(resposta)

    # --------------------------------------------------------------- varredura
    def mercado_completo(self) -> pd.DataFrame:
        """Baixa a B3 inteira (395+ papéis) com os 22 indicadores, numa requisição.

        Esta é a forma recomendada de triar: os filtros do servidor do Fundamentus
        misturam escalas (fração vs. absoluto) e passam por rate limit. Como a
        tabela completa vem numa única resposta, filtrar localmente em pandas é
        mais rápido, exato e sem risco de bloqueio.
        """
        if self.usar_cache:
            guardado = self.cache.get("fundamentus", f"{VERSAO_CACHE}:mercado_completo")
            if guardado is not None:
                return pd.DataFrame(guardado)

        html = self._post(f"{BASE}/resultado.php", {"negociada": "ON"})
        df = self._parse_tabela_mercado(html)
        if self.usar_cache and not df.empty:
            self.cache.set("fundamentus", f"{VERSAO_CACHE}:mercado_completo", df.to_dict(orient="records"))
        return df

    def buscar_mercado(
        self,
        criterios: dict[str, tuple[float | None, float | None]] | None = None,
        setor: str | None = None,
        apenas_negociadas: bool = True,
        ult_balanco_entregue: bool = False,
        ordenar_por: str | None = None,
    ) -> pd.DataFrame:
        """Varre a B3 aplicando filtros no servidor do Fundamentus.

        criterios: {"pl": (0, 10), "divyield": (6, None), ...} usando os nomes
        de COLUNAS_MERCADO. None em min/max significa 'sem limite'.

        Atenção: para campos exibidos em porcentagem (divyield, roe, roic,
        margens) o formulário espera FRACTION (0.06 = 6%), enquanto a tabela
        exibe 6,26. Para o resto, o valor é absoluto. Use mercado_completo() +
        triagem local para evitar essa armadilha.
        """
        criterios = criterios or {}
        payload: dict[str, str] = {}
        for chave, (minimo, maximo) in criterios.items():
            if chave not in CAMPOS_CRITERIO:
                log.warning("Critério ignorado (desconhecido): %s", chave)
                continue
            campo_min, campo_max = CAMPOS_CRITERIO[chave]
            payload[campo_min] = "" if minimo is None else str(minimo)
            payload[campo_max] = "" if maximo is None else str(maximo)

        if setor:
            codigo = SETORES.get(setor)
            if codigo is None:
                raise ErroFundamentus(f"Setor desconhecido: {setor}")
            payload["setor"] = str(codigo)

        if apenas_negociadas:
            payload["negociada"] = "ON"
        if ult_balanco_entregue:
            payload["ult_balanco_entregue"] = "ON"
        if ordenar_por and ordenar_por in ORDEM_POR_COLUNA:
            payload["ordem"] = str(ORDEM_POR_COLUNA[ordenar_por])

        chave_cache = "mercado::" + "|".join(f"{k}={v}" for k, v in sorted(payload.items()))
        if self.usar_cache:
            guardado = self.cache.get("fundamentus", chave_cache)
            if guardado is not None:
                return pd.DataFrame(guardado)

        html = self._post(f"{BASE}/resultado.php", payload)
        df = self._parse_tabela_mercado(html)
        if self.usar_cache and not df.empty:
            self.cache.set("fundamentus", chave_cache, df.to_dict(orient="records"))
        return df

    @staticmethod
    def _parse_tabela_mercado(html: str) -> pd.DataFrame:
        soup = BeautifulSoup(html, "lxml")
        tabelas = soup.find_all("table")
        if not tabelas:
            raise ErroFundamentus("Nenhuma tabela retornada pelo Fundamentus.")

        cabecalhos: list[str] = []
        linhas: list[list[str]] = []
        for tabela in tabelas:
            trs = tabela.find_all("tr")
            if not trs:
                continue
            cabecalho = [limpar_label(c.get_text(" ", strip=True)) for c in trs[0].find_all(["td", "th"])]
            if "Papel" not in cabecalho:
                continue
            cabecalhos = cabecalho
            for tr in trs[1:]:
                celulas = [limpar_label(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
                if celulas and celulas[0]:
                    linhas.append(celulas[: len(cabecalhos)])
            break

        if not cabecalhos:
            raise ErroFundamentus("Cabeçalho da tabela de resultados não reconhecido.")

        df = pd.DataFrame(linhas, columns=cabecalhos)
        df = df.rename(columns=COLUNAS_MERCADO)
        df = df.loc[:, ~df.columns.duplicated()]

        # Normaliza ticker e converte colunas numéricas.
        if "ticker" in df.columns:
            df["ticker"] = df["ticker"].str.strip().str.upper()
        for coluna in COLUNAS_MERCADO.values():
            if coluna in df.columns and coluna != "ticker":
                df[coluna] = df[coluna].map(parse_br)

        if "cotacao" in df.columns:
            df = df[df["cotacao"].notna() & (df["cotacao"] > 0)].copy()

        return df.reset_index(drop=True)

    # ---------------------------------------------------------------- detalhes
    def lista_fiis(self) -> set[str]:
        """Tickers de todos os FIIs da B3 (milhares).

        O Fundamentus lista FIIs junto com ações na tabela de mercado, mas as
        métricas não são comparáveis: P/L e EV/EBITDA de um fundo não têm o mesmo
        significado de uma companhia aberta. Esta lista separa os dois universos.
        """
        if self.usar_cache:
            guardado = self.cache.get("fundamentus", f"{VERSAO_CACHE}:lista_fiis")
            if guardado is not None:
                return set(guardado)

        html = self._post(f"{BASE}/fii_resultado.php", {"negociada": "ON"})
        soup = BeautifulSoup(html, "lxml")
        fiis: set[str] = set()
        for tabela in soup.find_all("table"):
            trs = tabela.find_all("tr")
            if not trs:
                continue
            cabecalho = [limpar_label(c.get_text(" ", strip=True)) for c in trs[0].find_all(["td", "th"])]
            if "Papel" not in cabecalho:
                continue
            for tr in trs[1:]:
                celulas = [limpar_label(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
                if celulas:
                    ticker = celulas[0].strip().upper()
                    if ticker and ticker != "PAPEL" and len(ticker) <= 10:
                        fiis.add(ticker)
            break

        if self.usar_cache and fiis:
            self.cache.set("fundamentus", f"{VERSAO_CACHE}:lista_fiis", sorted(fiis))
        return fiis

    def detalhes(self, ticker: str) -> DetalheEmpresa:
        """Dados completos de uma empresa (indicadores + balanço + DRE)."""
        ticker = ticker.strip().upper().replace(".SA", "")
        if self.usar_cache:
            guardado = self.cache.get("detalhes", f"{VERSAO_CACHE}:{ticker}")
            if guardado is not None:
                return DetalheEmpresa(**guardado)

        html = self._get(f"{BASE}/detalhes.php", params={"papel": ticker})
        detalhe = self._parse_detalhes(html, ticker)
        if self.usar_cache:
            self.cache.set("detalhes", f"{VERSAO_CACHE}:{ticker}", detalhe.to_dict())
        return detalhe

    @staticmethod
    def _parse_detalhes(html: str, ticker: str) -> DetalheEmpresa:
        soup = BeautifulSoup(html, "lxml")
        tabelas = soup.find_all("table")
        if not tabelas:
            raise ErroFundamentus(f"Sem dados para {ticker} no Fundamentus.")

        detalhe = DetalheEmpresa(ticker=ticker)

        def _linhas(tabela) -> list[list[str]]:
            """Linhas da tabela. PRESERVA o '?' dos rótulos: ele distingue
            indicador fundamentalista de linha de oscilação."""
            saida = []
            for tr in tabela.find_all("tr"):
                celulas = [
                    re.sub(r"\s+", " ", c.get_text(" ", strip=True)).replace("\xa0", " ").strip()
                    for c in tr.find_all(["td", "th"])
                ]
                if any(celulas):
                    saida.append(celulas)
            return saida

        # Tabela 0: papel, cotação, tipo, empresa, setor, subsetor e faixa 52 sem.
        # Tabela 1: valor de mercado, último balanço, valor da firma e nº de ações.
        _CAMPOS_T0 = {
            "papel": "ticker", "cotacao": "cotacao", "tipo": "tipo_papel",
            "empresa": "empresa", "setor": "setor", "subsetor": "subsetor",
            "data últ cot": "data_ult_cot", "min 52 sem": "min_52sem",
            "max 52 sem": "max_52sem", "vol $ méd (2m)": "vol_medio_2m",
        }
        if tabelas:
            for linha in _linhas(tabelas[0]):
                for i in range(0, len(linha) - 1, 2):
                    rotulo = linha[i].lstrip("? ").strip().lower()
                    destino = _CAMPOS_T0.get(rotulo)
                    if not destino:
                        continue
                    bruto = linha[i + 1]
                    if destino in {"cotacao", "min_52sem", "max_52sem", "vol_medio_2m"}:
                        setattr(detalhe, destino, parse_br(bruto))
                    else:
                        setattr(detalhe, destino, bruto)

        if len(tabelas) > 1:
            for linha in _linhas(tabelas[1]):
                for i in range(0, len(linha) - 1, 2):
                    rotulo = linha[i].lstrip("? ").strip().lower()
                    if "valor de mercado" in rotulo:
                        detalhe.valor_mercado = parse_br(linha[i + 1])
                    elif "ltimo balan" in rotulo or "ltimo balan" in rotulo:
                        detalhe.ultimo_balanco = linha[i + 1]
                    elif "valor da firma" in rotulo:
                        detalhe.valor_firma = parse_br(linha[i + 1])
                    elif rotulo.startswith("nro"):
                        detalhe.nro_acoes = parse_br(linha[i + 1])

        # Tabela 2: oscilações + indicadores fundamentalistas (pares rótulo/valor).
        # O Fundamentus prefixa com "?" os rótulos de indicador; os de oscilação
        # (Dia, Mês, 30 dias, 12 meses, anos) não têm prefixo.
        if len(tabelas) > 2:
            for linha in _linhas(tabelas[2]):
                for i in range(0, len(linha) - 1, 2):
                    rotulo_bruto = linha[i].strip()
                    eh_indicador = rotulo_bruto.startswith("?")
                    rotulo = rotulo_bruto.lstrip("? ").strip()
                    valor = linha[i + 1]
                    if not rotulo:
                        continue
                    if eh_indicador:
                        chave = _chave_indicador(rotulo)
                        detalhe.indicadores[chave] = parse_br(valor)
                        detalhe.indicadores_rotulos[rotulo] = chave
                    else:
                        detalhe.oscilacoes[rotulo] = valor

        # Tabela 3: balanço patrimonial (pares rótulo/valor).
        if len(tabelas) > 3:
            for linha in _linhas(tabelas[3]):
                for i in range(0, len(linha) - 1, 2):
                    rotulo = linha[i].lstrip("? ").strip()
                    if rotulo:
                        chave = _chave_balanco(rotulo)
                        detalhe.balanco[chave] = parse_br(linha[i + 1])
                        detalhe.balanco_rotulos[rotulo] = chave

        # Tabela 4: DRE (últimos 12 meses | últimos 3 meses).
        if len(tabelas) > 4:
            linhas = _linhas(tabelas[4])
            for linha in linhas:
                if len(linha) >= 4:
                    rotulo = linha[0].lstrip("? ").strip()
                    if rotulo:
                        detalhe.dre_anual[_chave_texto(rotulo)] = parse_br(linha[1])
                        detalhe.dre_trimestral[_chave_texto(rotulo)] = parse_br(linha[3])

        return detalhe


_ROTULOS_INDICADOR = {
    "p/l": "pl", "p/vp": "pvp", "psr": "psr", "p/ebit": "pebit",
    "marg. bruta": "margem_bruta", "marg. ebit": "margem_ebit",
    "marg. lquida": "margem_liquida", "marg. líquida": "margem_liquida",
    "roe": "roe", "roic": "roic",
    "ev / ebitda": "ev_ebitda", "ev / ebit": "ev_ebit",
    "liquidez corr": "liquidez_corrente",
    "dv. lq / patrim": "div_liq_patrimonio",
    "dív lq / patrim": "div_liq_patrimonio",
    "dív. líq / patrim": "div_liq_patrimonio",
    "cres. rec (5a)": "crescimento_receita_5a", "div. yield": "divyield",
    "p/ativos": "pativos", "p/cap. giro": "pcapgiro", "p/ativ circ liq": "p_circ_liq",
    "p/ativ circ liq": "p_circ_liq",
    "lpa": "lpa", "vpa": "vpa", "ebit / ativo": "ebit_ativo", "giro ativos": "giro_ativos",
}

# Rótulos do balanço patrimonial -> chave canônica usada no prompt.
_ROTULOS_BALANCO = {
    "ativo": "ativo",
    "disponibilidades": "disponibilidades",
    "ativo circulante": "ativo_circulante",
    "patrim. líq": "patrimonio_liquido",
    "patrimônio líquido": "patrimonio_liquido",
    "dív. bruta": "div_bruta",
    "dív. líquida": "div_liquida",
    "div. bruta": "div_bruta",
    "div. líquida": "div_liquida",
}


def _chave_indicador(rotulo: str) -> str:
    chave = _ROTULOS_INDICADOR.get(rotulo.lower().strip())
    if chave:
        return chave
    return _chave_texto(rotulo)


def _chave_balanco(rotulo: str) -> str:
    base = rotulo.lower().strip()
    chave = _ROTULOS_BALANCO.get(base)
    if chave:
        return chave
    return _chave_texto(rotulo)


def _chave_texto(rotulo: str) -> str:
    """Normaliza um rótulo em snake_case ascii."""
    limpo = (
        rotulo.lower()
        .replace("í", "i").replace("á", "a").replace("ã", "a").replace("é", "e")
        .replace("ê", "e").replace("ó", "o").replace("ô", "o").replace("ç", "c")
        .replace("ú", "u").replace("û", "u")
    )
    saida = "".join(c if c.isalnum() else "_" for c in limpo)
    while "__" in saida:
        saida = saida.replace("__", "_")
    return saida.strip("_")

