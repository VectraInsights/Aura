#!/usr/bin/env python
"""Aura — linha de comando local.

    python run.py triagem --perfil dividendario --limite 15
    python run.py analisar PETR4 --perfil qualidade
    python run.py analisar PETR4 VALE3 --perfil dividendario
    python run.py contexto PETR4
    python run.py perfis
    python run.py saude
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from src.analise import Analista  # noqa: E402
from src.config import CONFIG  # noqa: E402
from src.ia import PERFIS_ANALISE  # noqa: E402
from src.screen import PERFIS_TELA  # noqa: E402


def _linha(titulo: str) -> None:
    print(f"\n\033[1m{titulo}\033[0m")
    print("─" * 72)


def cmd_triagem(args: argparse.Namespace) -> int:
    analista = Analista()
    dados = analista.varredura(args.perfil, limite=args.limite, incluir_fiis=args.fiis)

    _linha(f"Triagem · {dados['perfil']['rotulo']}")
    print(dados["perfil"]["descricao"])
    print("Filtros: " + " · ".join(dados["perfil"]["criterios"]))
    print(f"\n{dados['total_aprovado']} aprovados de {dados['total_mercado']} papéis da B3\n")

    cabecalho = f"{'#':>3}  {'Ticker':8s} {'Empresa':26s} {'R$':>9} {'DY':>7} {'P/L':>7} {'ROE':>7} {'EV/EBITDA':>10}"
    print(cabecalho)
    print("─" * len(cabecalho))
    for r in dados["resultados"]:
        nome = str(r.get("nome") or "")[:24]
        print(
            f"{r.get('posicao', ''):>3}  {r['ticker']:8s} {nome:26s} "
            f"{r.get('cotacao') or 0:9.2f} "
            f"{r.get('divyield') if r.get('divyield') is not None else 0:6.2f}% "
            f"{r.get('pl') if r.get('pl') is not None else 0:7.2f} "
            f"{r.get('roe') if r.get('roe') is not None else 0:6.2f}% "
            f"{r.get('ev_ebitda') if r.get('ev_ebitda') is not None else 0:10.2f}"
        )

    if args.json:
        print("\n" + json.dumps(dados, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_analisar(args: argparse.Namespace) -> int:
    analista = Analista()
    for ticker in args.tickers:
        _linha(f"Parecer da IA · {ticker.upper()}")
        try:
            parecer = analista.parecer(ticker, perfil_ia=args.perfil)
        except Exception as exc:  # noqa: BLE001
            print(f"  Falha: {exc}\n")
            continue

        rel = parecer["relatorio"]
        ctx = parecer["contexto"]
        meta = parecer["meta"]

        print(f"Empresa    : {ctx.get('nome')} ({ctx.get('tipo')})")
        print(f"Setor      : {ctx.get('setor') or 'n/d'}")
        print(f"Cotação    : R$ {ctx.get('cotacao')}")
        print(f"Nota       : {rel.get('nota')}/10  (confiança: {rel.get('confianca')})")
        print(f"Modelo     : {meta.get('modelo')} · {meta.get('tokens_entrada')}→{meta.get('tokens_saida')} tokens")

        _linha("Veredito")
        print(rel.get("veredito", "—"))

        _linha("Resumo")
        print(rel.get("resumo", "—"))

        _linha("Endividamento")
        print(rel.get("endividamento", "—"))

        _linha("Geração de caixa")
        print(rel.get("geracao_caixa", "—"))

        _linha("Qualidade dos lucros")
        print(rel.get("qualidade_lucros", "—"))

        for titulo, chave in [("Riscos", "riscos"), ("Catalisadores", "catalisadores"),
                              ("Alertas", "alertas"), ("O que monitorar", "o_que_monitorar")]:
            itens = rel.get(chave) or []
            if itens:
                _linha(titulo)
                for item in itens:
                    print(f"  • {item}")

        if args.json:
            print("\n" + json.dumps(parecer, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_contexto(args: argparse.Namespace) -> int:
    ctx = Analista().contexto(args.ticker)
    _linha(f"Contexto · {ctx.ticker}")
    print(json.dumps(ctx.as_dict(), ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_perfis(args: argparse.Namespace) -> int:
    _linha("Perfis de triagem")
    for chave, p in PERFIS_TELA.items():
        print(f"\n  {chave} — {p.rotulo}")
        print(f"    {p.descricao}")
        for c in p.como_texto_criterios():
            print(f"      · {c}")
        for c in p.criterios:
            print(f"      · {c} (extra)")
    _linha("Perfis de análise (IA)")
    for chave, p in PERFIS_ANALISE.items():
        print(f"  {chave:14s} {p['rotulo']}")
    return 0


def cmd_saude(args: argparse.Namespace) -> int:
    info = {
        "chave_ia_configurada": CONFIG.tem_chave_ia,
        "modelo": CONFIG.gemini_model,
        "fallbacks": CONFIG.gemini_fallbacks,
        "cache": str(CONFIG.cache_dir),
    }
    print(json.dumps(info, ensure_ascii=False, indent=2))
    if CONFIG.tem_chave_ia:
        from src.ia import ClienteGemini

        try:
            modelo = ClienteGemini().testar()
            print(f"\nIA respondendo: {modelo}")
        except Exception as exc:  # noqa: BLE001
            print(f"\nFalha ao testar a IA: {exc}")
    return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    """Roda o monitor de regras em laço contínuo."""
    import time

    from src.monitor import Motor

    motor = Motor()
    print(f"\n\033[1mMonitor Aura\033[0m — verificando a cada {args.intervalo}s (Ctrl+C para sair)")
    canais = []
    if motor.notificador.telegram_token:
        canais.append("telegram")
    if motor.notificador._whatsapp_disponivel():
        canais.append("whatsapp")
    if motor.notificador.webhook_url:
        canais.append("webhook")
    print(f"Canais de aviso: {', '.join(canais) if canais else 'somente histórico local'}")
    print(f"Regras: {len(motor.regras())}\n")

    while True:
        try:
            resultado = motor.avaliar()
            for alerta in resultado.disparadas:
                print(
                    f"\033[33m⚡ {alerta.descricao}\033[0m — {alerta.preco:,.4f} "
                    f"[{', '.join(alerta.canais) if alerta.canais else 'histórico'}]"
                )
            if not resultado.disparadas:
                cotacoes = ", ".join(
                    f"{k}={v['preco']:.4f}" for k, v in resultado.cotacoes.items() if v.get("preco")
                )
                print(f"  {resultado.verificado_em}  {cotacoes}")
        except KeyboardInterrupt:
            print("\nInterrompido.")
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"  erro no ciclo: {exc}")
        time.sleep(args.intervalo)


def cmd_regras(args: argparse.Namespace) -> int:
    """Lista as regras de monitoramento."""
    from src.monitor import Motor

    motor = Motor()
    regras = motor.regras()
    if not regras:
        print("Nenhuma regra cadastrada.")
        return 0

    _linha(f"Regras de monitoramento ({len(regras)})")
    for r in regras:
        estado = "ARMADA" if r.armed else "DISPARADA"
        preco = f"{r.ultimo_preco:.4f}" if r.ultimo_preco is not None else "n/d"
        print(f"\n  [{r.id}] {r.descrever()}")
        print(f"      estado: {estado} | último preço: {preco} | criada em {r.criada_em}")
        if r.ultimo_trigger:
            print(f"      último disparo: {r.ultimo_trigger}")
    return 0


def cmd_alertas(args: argparse.Namespace) -> int:
    """Mostra o histórico de alertas."""
    from src.monitor import Motor

    motor = Motor()
    alertas = motor.historico_alertas(limite=args.limite)
    if not alertas:
        print("Nenhum alerta registrado.")
        return 0

    _linha(f"Alertas ({len(alertas)})")
    for a in alertas:
        print(f"  {a.get('em')}  {a.get('descricao')}  → R$ {a.get('preco')}  [{', '.join(a.get('canais') or []) or 'histórico'}]")
    return 0


def cmd_verificar(args: argparse.Namespace) -> int:
    """Auto-teste: confere dependências declaradas e sobe a aplicação.

    Existe porque uma dependência faltando no requirements.txt só aparece
    no build da Vercel, como 500 FUNCTION_INVOCATION_FAILED. Aqui isso vira
    um erro claro, em segundos, antes do deploy.
    """
    import importlib

    print("\n\033[1mVerificação do Aura\033[0m\n")

    # 1) Toda dependência usada está declarada no requirements.txt?
    requisitos = RAIZ / "requirements.txt"
    declarados: set[str] = set()
    if requisitos.exists():
        for linha in requisitos.read_text(encoding="utf-8").splitlines():
            linha = linha.split("#")[0].strip()
            if linha:
                declarados.add(linha.split("[")[0].split("=")[0].split(">")[0].split("<")[0].strip().lower())

    # módulo importado -> nome do pacote no PyPI
    modulos = {
        "fastapi": "fastapi",
        "starlette": "fastapi",
        "pandas": "pandas",
        "requests": "requests",
        "bs4": "beautifulsoup4",
        "lxml": "lxml",
        "yfinance": "yfinance",
        "google.genai": "google-genai",
        "dotenv": "python-dotenv",
        "itsdangerous": "itsdangerous",
    }

    faltando: list[str] = []
    print("Dependências:")
    for modulo, pacote in modulos.items():
        try:
            importlib.import_module(modulo)
            instalado = True
        except ImportError:
            instalado = False
            faltando.append(pacote)
        declarado = pacote in declarados
        marca = "OK " if instalado and declarado else "!! "
        print(f"  {marca}{modulo:16s} instalado={'sim' if instalado else 'NÃO':4s} "
              f"declarado={'sim' if declarado else 'NÃO'}")
        if instalado and not declarado:
            faltando.append(f"{pacote} (instalado mas fora do requirements.txt)")

    if faltando:
        print(f"\n\033[31mProblemas:\033[0m {', '.join(sorted(set(faltando)))}")
        print("Rode: pip install -r requirements.txt")
        return 1

    # 2) A aplicação sobe e responde?
    print("\nAplicação:")
    try:
        from fastapi.testclient import TestClient

        from api.index import app

        cliente = TestClient(app, base_url="https://testserver")
        for rota in ["/api/health", "/api/sessao", "/", "/assets/app.js"]:
            resposta = cliente.get(rota)
            ok = resposta.status_code == 200
            print(f"  {'OK ' if ok else '!! '} {rota} -> {resposta.status_code}")
            if not ok:
                return 1
    except Exception as exc:  # noqa: BLE001
        print(f"  !! a aplicação não sobe: {type(exc).__name__}: {exc}")
        return 1

    # 3) As formas de caminho que a Vercel entrega funcionam?
    #    A Vercel reescreve /api/x para /api/index.py e entrega o caminho já
    #    reescrito. Se a normalização falhar, vem 404 ou 405.
    print("\nFormas de caminho (como a Vercel entrega):")
    formas = [
        ("GET", "/api/health"),
        ("GET", "/api/perfis"),
        ("GET", "/api/index.py/health"),
        ("GET", "/api/index.py/perfis"),
        ("GET", "/api/api/perfis"),
        ("POST", "/api/login"),
        ("POST", "/api/index.py/login"),
        ("POST", "/api/monitor/verificar"),
        ("POST", "/api/index.py/monitor/verificar"),
    ]
    try:
        from fastapi.testclient import TestClient

        from api.index import app

        cliente = TestClient(app, base_url="https://testserver")
        from src.config import CONFIG

        if CONFIG.app_password:
            cliente.post("/api/login", json={"senha": CONFIG.app_password})
        for metodo, caminho in formas:
            if metodo == "POST":
                resposta = cliente.post(caminho, json={})
            else:
                resposta = cliente.get(caminho)
            # 401 é esperado (sem sessão): o que não pode é 404 ou 405.
            ok = resposta.status_code not in (404, 405)
            print(f"  {'OK ' if ok else '!! '} {metodo:4s} {caminho:34s} -> {resposta.status_code}")
            if not ok:
                return 1
    except Exception as exc:  # noqa: BLE001
        print(f"  !! não foi possível testar as formas de caminho: {exc}")
        return 1

    print("\n\033[32mTudo certo.\033[0m")
    return 0


def cmd_senha(args: argparse.Namespace) -> int:
    """Gera a senha de acesso e o segredo de assinatura do cookie.

    Escreve no .env local (e imprime, para você copiar para a Vercel).
    """
    import secrets
    import string

    alfabeto = string.ascii_letters + string.digits
    # Sem caracteres que pareçam outros (0/O, 1/l/I) e sem símbolo, para
    # digitar no celular sem susto.
    alfabeto = "".join(c for c in alfabeto if c not in "O0oIl1")
    senha = "".join(secrets.choice(alfabeto) for _ in range(16))
    segredo = secrets.token_urlsafe(48)

    arquivo = RAIZ / ".env"
    if arquivo.exists():
        linhas = arquivo.read_text(encoding="utf-8").splitlines()
        novos: list[str] = []
        trocou = {"APP_PASSWORD": False, "SESSION_SECRET": False}
        for linha in linhas:
            chave = linha.split("=", 1)[0].strip()
            if chave == "APP_PASSWORD":
                novos.append(f"APP_PASSWORD={senha}")
                trocou["APP_PASSWORD"] = True
            elif chave == "SESSION_SECRET":
                novos.append(f"SESSION_SECRET={segredo}")
                trocou["SESSION_SECRET"] = True
            else:
                novos.append(linha)
        if not trocou["APP_PASSWORD"]:
            novos.append(f"APP_PASSWORD={senha}")
        if not trocou["SESSION_SECRET"]:
            novos.append(f"SESSION_SECRET={segredo}")
        arquivo.write_text("\n".join(novos) + "\n", encoding="utf-8")
        print("Atualizado no .env local.\n")
    else:
        print("Nao ha .env local. Crie-o com .env.example e preencha.\n")

    print("\033[1mCopie estes valores para a Vercel\033[0m")
    print("(Settings -> Environment Variables, nos 3 ambientes)\n")
    print(f"  APP_PASSWORD     = {senha}")
    print(f"  SESSION_SECRET  = {segredo}")
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="aura",
        description="Aura — IA de análise de investimentos na B3.",
    )
    parser.add_argument("--json", action="store_true", help="Saída também em JSON")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("triagem", help="Varre a B3 aplicando um perfil")
    p.add_argument("--perfil", default="qualidade", choices=sorted(PERFIS_TELA))
    p.add_argument("--limite", type=int, default=20)
    p.add_argument("--fiis", action="store_true", help="Incluir unidades de FII")
    p.set_defaults(func=cmd_triagem)

    p = sub.add_parser("analisar", help="Gera o parecer da IA sobre um ou mais tickers")
    p.add_argument("tickers", nargs="+")
    p.add_argument("--perfil", default="qualidade", choices=sorted(PERFIS_ANALISE))
    p.set_defaults(func=cmd_analisar)

    p = sub.add_parser("contexto", help="Mostra todos os números coletados de um ticker")
    p.add_argument("ticker")
    p.set_defaults(func=cmd_contexto)

    p = sub.add_parser("perfis", help="Lista os perfis disponíveis")
    p.set_defaults(func=cmd_perfis)

    p = sub.add_parser("saude", help="Verifica a configuração e a IA")
    p.set_defaults(func=cmd_saude)

    p = sub.add_parser("monitor", help="Monitora as regras de preço em laço contínuo")
    p.add_argument("--intervalo", type=int, default=60, help="Segundos entre verificações")
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("regras", help="Lista as regras de monitoramento")
    p.set_defaults(func=cmd_regras)

    p = sub.add_parser("alertas", help="Histórico de alertas disparados")
    p.add_argument("--limite", type=int, default=20)
    p.set_defaults(func=cmd_alertas)

    p = sub.add_parser("verificar", help="Confere dependências e sobe a aplicação")
    p.set_defaults(func=cmd_verificar)

    p = sub.add_parser("senha", help="Gera a senha de acesso e o segredo do cookie")
    p.set_defaults(func=cmd_senha)

    args = parser.parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrompido.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
