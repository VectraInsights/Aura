"""Aura — API de análise de investimentos na B3.

Deploy: Vercel (Python runtime) ou localmente com `uvicorn api.index:app --reload`.

Rotas expostas em /api/... (padrão do Vercel com rewrite para api/index.py)
e também na raiz, para funcionar atrás de proxies que removem o prefixo.
"""

from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

# Permite executar tanto pelo repo (uvicorn api.index:app) quanto pela raiz.
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# Em serverless o sistema de arquivos é somente-leitura fora de /tmp.
if os.getenv("VERCEL") or not os.access(RAIZ, os.W_OK):
    os.environ.setdefault("CACHE_DIR", "/tmp/aura-cache")

from fastapi import APIRouter, FastAPI, HTTPException, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from src.analise import Analista  # noqa: E402
from src.config import CONFIG  # noqa: E402
from src.ia import PERFIS_ANALISE  # noqa: E402
from src.ia.gemini import ErroIA  # noqa: E402
from src.screen import PERFIS_TELA  # noqa: E402

app = FastAPI(
    title="Aura",
    description="IA de análise de investimentos na B3 — triagem fundamentalista e parecer técnico.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter()

#: Instância compartilhada: o cache em disco evita re-scraping entre requisições.
_analista: Analista | None = None


def get_analista() -> Analista:
    global _analista
    if _analista is None:
        _analista = Analista()
    return _analista


def json_seguro(payload: Any) -> Any:
    """Substitui NaN/Infinity por None.

    Sem isso o FastAPI emite `NaN` no corpo, que não é JSON válido: o
    response.json() do navegador quebra e a interface fica sem resposta.
    """
    if isinstance(payload, dict):
        return {k: json_seguro(v) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [json_seguro(v) for v in payload]
    if isinstance(payload, float):
        return None if (payload != payload or payload in (float("inf"), float("-inf"))) else payload
    return payload


def _exiguir_ia() -> None:
    if not CONFIG.tem_chave_ia:
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY não configurada no ambiente da aplicação.",
        )


@router.get("/health")
def health() -> dict:
    """Verifica a configuração e a disponibilidade da IA."""
    return {
        "status": "ok",
        "app": "Aura",
        "versao": "1.0.0",
        "ia_configurada": CONFIG.tem_chave_ia,
        "modelo_padrao": CONFIG.gemini_model,
        "modelos_fallback": CONFIG.gemini_fallbacks,
        "cache_dir": str(CONFIG.cache_dir),
    }


@router.get("/perfis")
def perfis() -> dict:
    """Perfis de triagem e perfis de foco da IA disponíveis."""
    tela = [
        {
            "chave": p.chave,
            "rotulo": p.rotulo,
            "descricao": p.descricao,
            "criterios": p.como_texto_criterios(),
            "extras": p.criterios,
        }
        for p in PERFIS_TELA.values()
    ]
    ia = [
        {"chave": k, "rotulo": v["rotulo"], "descricao": v["descricao"]}
        for k, v in PERFIS_ANALISE.items()
    ]
    return {"triagem": tela, "analise": ia}


@router.get("/triagem")
def triagem(
    perfil: str = Query("qualidade", description="Perfil de triagem"),
    limite: int = Query(50, ge=1, le=100),
    incluir_fiis: bool = Query(False, description="Incluir unidades de FII na lista"),
) -> dict:
    """Varre a B3 e devolve as empresas que passam no perfil."""
    if perfil not in PERFIS_TELA:
        raise HTTPException(status_code=400, detail=f"Perfil desconhecido: {perfil}")
    try:
        dados = get_analista().varredura(
            perfil_chave=perfil, limite=limite, incluir_fiis=incluir_fiis
        )
        return json_seguro(dados)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Falha na coleta de dados: {exc}") from exc


@router.get("/analise/{ticker}")
def analise(
    ticker: str,
    perfil: str = Query("qualidade", description="Foco da análise"),
) -> dict:
    """Gera o parecer técnico da IA sobre uma empresa."""
    if perfil not in PERFIS_ANALISE:
        raise HTTPException(status_code=400, detail=f"Perfil de análise desconhecido: {perfil}")
    _exiguir_ia()
    try:
        return json_seguro(get_analista().parecer(ticker, perfil_ia=perfil))
    except ErroIA as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Falha na análise: {exc}") from exc


@router.post("/analise-lote")
def analise_lote(payload: dict) -> dict:
    """Analisa várias empresas em paralelo. Body: {"tickers": [...], "perfil": "..."}.

    O paralelismo existe para caber no limite de 60s da função serverless da Vercel.
    """
    tickers = payload.get("tickers") or []
    perfil = payload.get("perfil", "qualidade")
    if not isinstance(tickers, list) or not tickers:
        raise HTTPException(status_code=400, detail="Informe ao menos um ticker em 'tickers'.")
    if len(tickers) > CONFIG.max_tickers_analise:
        raise HTTPException(
            status_code=400,
            detail=f"Limite de {CONFIG.max_tickers_analise} empresas por lote.",
        )
    _exigir_ia()

    def _um(ticker: str) -> dict:
        try:
            parecer = get_analista().parecer(ticker, perfil_ia=perfil)
            parecer["meta"]["perfil"] = perfil
            return {"ticker": ticker, "ok": True, **parecer}
        except Exception as exc:  # noqa: BLE001
            return {"ticker": ticker, "ok": False, "erro": str(exc)}

    with ThreadPoolExecutor(max_workers=min(4, len(tickers))) as pool:
        resultados = list(pool.map(_um, tickers))

    return json_seguro({"perfil": perfil, "resultados": resultados})


# Monta as rotas em /api/... e também na raiz (proxy sem prefixo).
app.include_router(router, prefix="/api")
app.include_router(router)


class _EstaticosSeguros(StaticFiles):
    """StaticFiles que não expõe arquivos de código ou ocultos (.env, .py)."""

    def lookup_path(self, path: str):  # type: ignore[override]
        if Path(path).name.startswith(".") or path.endswith(".py"):
            raise HTTPException(status_code=404, detail="Not Found")
        return super().lookup_path(path)


# Por último: as rotas da API já foram registradas, então "/" cai no index.html.
app.mount("/", _EstaticosSeguros(directory=str(RAIZ), html=True), name="estatico")
