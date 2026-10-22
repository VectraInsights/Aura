"""Aura — API de análise de investimentos na B3.

Deploy: Vercel (Python runtime) ou localmente com `uvicorn api.index:app --reload`.

Rotas expostas em /api/... (padrão do Vercel com rewrite para api/index.py)
e também na raiz, para funcionar atrás de proxies que removem o prefixo.
"""

from __future__ import annotations

import os
import secrets
import sys
import time
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

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

try:
    from starlette.middleware.sessions import SessionMiddleware

    SESSAO_DISPONIVEL = True
except ImportError:  # pragma: no cover - só sem o itsdangerous instalado
    # Sem itsdangerous não há cookie assinado. Melhor abrir a aplicação
    # (sem login) do que derrubar a função inteira.
    SessionMiddleware = None  # type: ignore[assignment]
    SESSAO_DISPONIVEL = False
    print("[aviso] itsdangerous ausente: o login por senha fica desativado.")

from src.analise import Analista  # noqa: E402
from src.config import CONFIG, VERSAO_APP  # noqa: E402
from src.ia import PERFIS_ANALISE  # noqa: E402
from src.ia.gemini import ErroIA  # noqa: E402
from src.perfil import QUESTOES, perfil_completo, resumo_para_ia  # noqa: E402
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

#: Rotas liberadas mesmo sem sessão iniciada.
ROTAS_PUBLICAS = {"/health", "/sessao", "/login", "/logout", "/diagnostico"}


def _segredo_sessao() -> str:
    """Chave de assinatura do cookie. Gerada na hora se não vier do ambiente."""
    if CONFIG.session_secret:
        return CONFIG.session_secret
    if CONFIG.login_ativo:
        print(
            "[aviso] SESSION_SECRET nao configurada: as sessoes serao invalidadas "
            "a cada reinicio da aplicacao. Defina SESSION_SECRET no ambiente."
        )
    return secrets.token_urlsafe(48)


if SESSAO_DISPONIVEL:
    app.add_middleware(
        SessionMiddleware,
        secret_key=_segredo_sessao(),
        max_age=CONFIG.session_max_age,
        same_site="lax",
        https_only=CONFIG.cookie_https_only,
    )


@app.middleware("http")
async def normaliza_caminho(request: Request, call_next):
    """Normaliza o caminho recebido pela função.

    A Vercel, em `rewrites`, entrega ao app o caminho de DESTINO e descarta o
    original: `/api/perfis` chega como `/api/index.py`. Isso colapsaria todas as
    rotas numa só (e os POST em 405).

    Resgate em duas camadas:
      1. o cliente manda a rota pretendida em `?rota=` — a query string
         sobrevive ao rewrite, então é o canal confiável;
      2. se não houver `?rota=`, normaliza as formas reescritas removendo
         segmentos .py e usando o ÚLTIMO "/api" do caminho.
    """
    caminho = request.scope.get("path", "")

    # Camada 1: rota enviada pelo cliente na query string.
    consulta = request.scope.get("query_string", b"")
    if consulta:
        from urllib.parse import parse_qs

        params = parse_qs(consulta.decode("utf-8", errors="replace"))
        rota = (params.get("rota") or [""])[0]
        if rota:
            # A rota vem na query string; se o cliente mandou algo como
            # "/triagem?x=1", o que vier depois do '?' nao faz parte do caminho.
            rota = rota.split("?", 1)[0]
            rota = rota if rota.startswith("/") else "/" + rota
            novo = "/api" + rota if not rota.startswith("/api") else rota
            if novo != caminho:
                request.scope["path"] = novo
                request.scope["raw_path"] = caminho.encode()
            return await call_next(request)

    if caminho in ("", "/"):
        return await call_next(request)  # "/" é o frontend, servido pelo StaticFiles

    segmentos = [s for s in caminho.split("/") if s]
    if not segmentos:
        request.scope["path"] = "/api/health"
        return await call_next(request)

    # Requisição de arquivo estático (app.js, styles.css, logo.png...).
    ultimo = segmentos[-1].lower()
    if "." in ultimo and not ultimo.endswith(".py"):
        if ultimo.rsplit(".", 1)[-1] in {
            "js", "css", "html", "htm", "png", "jpg", "jpeg", "gif", "svg",
            "ico", "json", "txt", "md", "woff", "woff2", "ttf", "map", "webp",
        }:
            return await call_next(request)

    segmentos = [s for s in segmentos if not s.endswith(".py")]
    if not segmentos:
        request.scope["path"] = "/api/health"
        return await call_next(request)

    if "api" in segmentos:
        # Vale o último "/api": /api/api/x -> /api/x
        indice = len(segmentos) - 1 - segmentos[::-1].index("api")
        novo = "/" + "/".join(segmentos[indice:])
        if novo == "/api":
            novo = "/api/health"
    else:
        novo = "/api/" + "/".join(segmentos)

    if novo != caminho:
        request.scope["path"] = novo
        request.scope["raw_path"] = caminho.encode()
    return await call_next(request)


def _sessao(request: Request) -> dict:
    """Sessão do request, ou dicionário vazio se o login não estiver disponível."""
    if not SESSAO_DISPONIVEL:
        return {}
    try:
        return request.session
    except AssertionError:
        return {}


def exigir_sessao(request: Request) -> None:
    """Dependência que bloqueia as rotas de dados sem sessão iniciada."""
    if not CONFIG.login_ativo or not SESSAO_DISPONIVEL:
        return
    if not request.session.get("autenticado"):
        raise HTTPException(
            status_code=401,
            detail="Não autenticado. Informe a senha para continuar.",
        )

router = APIRouter()
#: Rotas que exigem sessão (quando há APP_PASSWORD configurada).
router_protegido = APIRouter(dependencies=[Depends(exigir_sessao)])

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


@router.get("/sessao")
def sessao(request: Request) -> dict:
    """Estado da sessão — o frontend usa isto para decidir se mostra o login."""
    dados = _sessao(request)
    autenticado = bool(dados.get("autenticado")) or not CONFIG.login_ativo
    return {
        "autenticado": autenticado,
        "login_ativo": CONFIG.login_ativo and SESSAO_DISPONIVEL,
        "perfil_preenchido": perfil_completo(dados.get("perfil")),
        "usuario": dados.get("usuario"),
    }


@router.post("/login")
async def login(request: Request) -> dict:
    """Autentica com a senha de acesso (APP_PASSWORD).

    Aceita JSON {"senha": "..."} ou formulário. A comparação é em tempo
    constante, para não vazar informação pelo tempo de resposta.
    """
    if not CONFIG.login_ativo:
        return {"autenticado": True, "mensagem": "Login desativado."}
    if not SESSAO_DISPONIVEL:
        raise HTTPException(
            status_code=503,
            detail="Login indisponível: o pacote itsdangerous não está instalado.",
        )

    senha = ""
    tipo = request.headers.get("content-type", "")
    if "application/json" in tipo:
        try:
            corpo = await request.json()
            senha = str(corpo.get("senha", ""))
        except Exception:  # noqa: BLE001
            senha = ""
    else:
        from urllib.parse import parse_qs

        try:
            bruto = (await request.body()).decode("utf-8", errors="replace")
            senha = parse_qs(bruto).get("senha", [""])[0]
        except Exception:  # noqa: BLE001
            senha = ""

    if not secrets.compare_digest(senha.encode("utf-8"), CONFIG.app_password.encode("utf-8")):
        time.sleep(0.4)  # desacelera tentativas em massa
        raise HTTPException(status_code=401, detail="Senha incorreta.")

    _sessao(request).clear()
    _sessao(request)["autenticado"] = True
    _sessao(request)["usuario"] = "voce"
    return {"autenticado": True, "mensagem": "Acesso liberado."}


@router.post("/logout")
def logout(request: Request) -> dict:
    """Encerra a sessão."""
    _sessao(request).clear()
    return {"autenticado": False}


@router_protegido.get("/regras")
def listar_regras() -> dict:
    """Regras de monitoramento cadastradas, com o preço atual de cada ativo."""
    from src.monitor import Motor

    motor = Motor()
    regras = motor.regras()
    resultado = motor.avaliar(notificar=False)

    for regra in regras:
        cot = resultado.cotacoes.get(regra.ativo.strip().upper(), {})
        regra.ultimo_preco = cot.get("preco")
        regra.ultima_verificacao = resultado.verificado_em

    return {
        "regras": [r.to_dict() for r in regras],
        "cotacoes": resultado.cotacoes,
        "verificado_em": resultado.verificado_em,
        "disparadas": [a.__dict__ for a in resultado.disparadas],
    }


@router_protegido.post("/regras")
async def criar_regra(payload: dict) -> dict:
    """Cria uma regra de monitoramento.

    Corpo: {"ativo": "PETR4", "operador": ">=", "valor": 60,
             "acao": "vender", "ativo_alvo": "PETR4", "nota": "opcional"}
    """
    from src.monitor import ACOES, OPERADORES, Motor, Regra, resolver

    ativo = str(payload.get("ativo", "")).strip()
    if not ativo:
        raise HTTPException(status_code=400, detail="Informe o ativo a monitorar.")

    operador = str(payload.get("operador", ">="))
    if operador not in OPERADORES:
        raise HTTPException(status_code=400, detail=f"Condição inválida: {operador}")

    acao = str(payload.get("acao", "alertar"))
    if acao not in ACOES:
        raise HTTPException(status_code=400, detail=f"Ação inválida: {acao}")

    try:
        valor = float(payload.get("valor"))
        valor2 = payload.get("valor2")
        valor2 = float(valor2) if valor2 not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Valores inválidos.") from exc

    if operador == "entre" and valor2 is None:
        raise HTTPException(status_code=400, detail="'entre' precisa de dois valores.")

    regra = Regra(
        ativo=ativo,
        operador=operador,
        valor=valor,
        valor2=valor2,
        acao=acao,
        ativo_alvo=(str(payload["ativo_alvo"]).strip() if payload.get("ativo_alvo") else None),
        nota=str(payload.get("nota", ""))[:300],
    )
    motor = Motor()
    salva = motor.adicionar(regra)
    return {"regra": salva.to_dict()}


@router_protegido.delete("/regras/{id_regra}")
def remover_regra(id_regra: str) -> dict:
    """Remove uma regra."""
    from src.monitor import Motor

    removida = Motor().remover(id_regra)
    if not removida:
        raise HTTPException(status_code=404, detail="Regra não encontrada.")
    return {"removida": id_regra}


@router_protegido.get("/alertas")
def listar_alertas(limite: int = Query(30, ge=1, le=200)) -> dict:
    """Histórico de alertas disparados."""
    from src.monitor import Motor

    return {"alertas": Motor().historico_alertas(limite=limite)}


@router_protegido.post("/monitor/verificar")
def verificar_monitor() -> dict:
    """Roda uma verificação agora e dispara os alertas pendentes."""
    from src.monitor import Motor

    resultado = Motor().avaliar(notificar=True)
    return resultado.to_dict()


@router_protegido.get("/monitor/canais")
def canais_monitor() -> dict:
    """Quais canais de aviso estão configurados."""
    from src.monitor import Motor

    motor = Motor()
    n = motor.notificador
    return {
        "telegram": bool(n.telegram_token and n.telegram_chat_id),
        "whatsapp_callmebot": bool(n.callmebot_phone and n.callmebot_apikey),
        "whatsapp_cloud": bool(n.whatsapp_token and n.whatsapp_phone_id),
        "webhook": bool(n.webhook_url),
        "historico_local": True,
    }


@router_protegido.get("/perfil")
def perfil_atual(request: Request) -> dict:
    """Questionário do onboarding + as respostas já dadas."""
    return {
        "questoes": QUESTOES,
        "respostas": _sessao(request).get("perfil") or {},
        "preenchido": perfil_completo(_sessao(request).get("perfil")),
    }


@router_protegido.post("/perfil")
async def salvar_perfil(request: Request) -> dict:
    """Guarda as respostas do onboarding na sessão."""
    try:
        corpo = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Corpo inválido.") from exc

    respostas: dict[str, Any] = {}
    for pergunta in QUESTOES:
        campo = pergunta["campo"]
        if campo not in (corpo or {}):
            continue
        valor = corpo[campo]
        if pergunta.get("tipo") == "numero":
            try:
                valor = float(valor)
            except (TypeError, ValueError):
                continue
        elif pergunta.get("tipo") == "texto":
            valor = str(valor or "").strip()[:600]
        else:
            permitidos = {o["valor"] for o in pergunta.get("opcoes", [])}
            if valor not in permitidos:
                continue
        respostas[campo] = valor

    _sessao(request)["perfil"] = respostas
    return {
        "perfil_preenchido": perfil_completo(respostas),
        "respostas": respostas,
        "resumo": resumo_para_ia(respostas),
    }


@router_protegido.post("/radar")
async def radar(
    request: Request,
    perfil: str = Query("qualidade", description="Perfil de triagem que gera os candidatos"),
    limite: int = Query(12, ge=3, le=30),
    incluir_fiis: bool = Query(True),
) -> dict:
    """Radar de compra: a IA ordena os candidatos para o perfil de quem pergunta.

    Diferente da triagem (mecânica, ordenada por um indicador), aqui a ordem
    é da IA — e vem com o motivo de cada posição.
    """
    from .radar import (
        CRITERIOS_POR_CLASSE,
        INSTRUCAO_RADAR,
        LIMITES_DO_RADAR,
        classificar_por_classe,
        construir_prompt_radar,
        montar_candidatos,
        schema_radar,
    )

    try:
        candidatos = montar_candidatos(
            get_analista(),
            perfil_triagem=perfil,
            limite=limite,
            incluir_fiis=incluir_fiis,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Falha ao montar os candidatos: {exc}") from exc

    if not candidatos:
        raise HTTPException(status_code=404, detail="Nenhum candidato encontrado.")

    prompt = construir_prompt_radar(candidatos, _sessao(request).get("perfil") or None, perfil)

    try:
        resultado = get_analista().cliente_ia.gerar(
            prompt=prompt,
            instrucao_sistema=INSTRUCAO_RADAR,
            schema=schema_radar(),
        )
    except ErroIA as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Falha ao gerar o radar: {exc}") from exc

    grupos = classificar_por_classe(candidatos)
    return {
        "radar": json_seguro(resultado.dados or {}),
        "candidatos": {classe: [c.ticker for c in itens] for classe, itens in grupos.items()},
        "criterios": CRITERIOS_POR_CLASSE,
        "limites": LIMITES_DO_RADAR,
        "meta": {
            "modelo": resultado.modelo,
            "tokens_entrada": resultado.tokens_entrada,
            "tokens_saida": resultado.tokens_saida,
            "segundos": round(resultado.segundos, 1),
            "perfil_triagem": perfil,
            "total_candidatos": len(candidatos),
        },
    }


@router.get("/diagnostico")
def diagnostico(request: Request) -> dict:
    """Diagnóstico do ambiente — útil para conferir deploy e variáveis.

    Revela qual caminho bruto chegou à função (o Vercel pode reescrever),
    sem jamais expor o valor das chaves.
    """
    import platform
    import sys

    sha = os.getenv("VERCEL_GIT_COMMIT_SHA", "")
    return {
        "app": "Aura",
        "versao": VERSAO_APP,
        "commit": sha[:7] if sha else "local",
        "commit_completo": sha,
        "commit_mensagem": os.getenv("VERCEL_GIT_COMMIT_MESSAGE", "")[:120],
        "branch": os.getenv("VERCEL_GIT_COMMIT_REF", ""),
        "ambiente": os.getenv("VERCEL_ENV", ""),
        "projeto": os.getenv("VERCEL_PROJECT_NAME", ""),
        "url_producao": os.getenv("VERCEL_PROJECT_PRODUCTION_URL", ""),
        "deploy_id": os.getenv("VERCEL_DEPLOYMENT_ID", ""),
        "python": sys.version.split()[0],
        "plataforma": platform.system(),
        "em_venv": bool(os.getenv("VERCEL")),
        "caminho_recebido": request.scope.get("path"),
        "caminho_bruto": request.scope.get("raw_path", b"").decode(errors="replace"),
        "ia_configurada": CONFIG.tem_chave_ia,
        "modelo_padrao": CONFIG.gemini_model,
        "variaveis_presentes": sorted(
            k for k in os.environ
            if k.startswith(("GEMINI", "GOOGLE", "VERCEL"))
        ),
        "cache_dir": str(CONFIG.cache_dir),
        "cache_escrevivel": os.access(CONFIG.cache_dir, os.W_OK) if CONFIG.cache_dir.exists() else False,
    }


@router.get("/health")
def health() -> dict:
    """Verifica a configuração e a disponibilidade da IA."""
    sha = os.getenv("VERCEL_GIT_COMMIT_SHA", "")
    return {
        "status": "ok",
        "app": "Aura",
        "versao": VERSAO_APP,
        "commit": sha[:7] if sha else "local",
        "ia_configurada": CONFIG.tem_chave_ia,
        "modelo_padrao": CONFIG.gemini_model,
        "modelos_fallback": CONFIG.gemini_fallbacks,
        "cache_dir": str(CONFIG.cache_dir),
    }


@router_protegido.get("/perfis")
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


@router_protegido.get("/triagem")
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


@router_protegido.get("/analise/{ticker}")
def analise(
    ticker: str,
    request: Request,
    perfil: str = Query("qualidade", description="Foco da análise"),
) -> dict:
    """Gera o parecer técnico da IA sobre uma empresa."""
    if perfil not in PERFIS_ANALISE:
        raise HTTPException(status_code=400, detail=f"Perfil de análise desconhecido: {perfil}")
    _exiguir_ia()
    try:
        return json_seguro(
            get_analista().parecer(
                ticker,
                perfil_ia=perfil,
                perfil_investidor=_sessao(request).get("perfil") or None,
            )
        )
    except ErroIA as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Falha na análise: {exc}") from exc


@router_protegido.post("/analise-lote")
def analise_lote(payload: dict, request: Request) -> dict:
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
            parecer = get_analista().parecer(
                ticker,
                perfil_ia=perfil,
                perfil_investidor=_sessao(request).get("perfil") or None,
            )
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
app.include_router(router_protegido, prefix="/api")
app.include_router(router_protegido)


class _EstaticosSeguros(StaticFiles):
    """StaticFiles que não expõe arquivos de código ou ocultos (.env, .py)."""

    def lookup_path(self, path: str):  # type: ignore[override]
        if Path(path).name.startswith(".") or path.endswith(".py"):
            raise HTTPException(status_code=404, detail="Not Found")
        return super().lookup_path(path)


# Por último: as rotas da API já foram registradas, então "/" cai no index.html.
app.mount("/", _EstaticosSeguros(directory=str(RAIZ), html=True), name="estatico")
