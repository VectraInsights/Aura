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

    args = parser.parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrompido.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
