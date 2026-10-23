"""Monitor de Day Trade: acompanha variação de preço intraday e avisa
quando surge oportunidade de venda.

Área separada dos investimentos de longo prazo. Não executa ordens em
corretora — apenas avisa quando o preço atinge a meta definida.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..monitor.precos import cotacao
from ..config import CONFIG

log = logging.getLogger(__name__)


@dataclass
class OperacaoDayTrade:
    """Registro de uma operação de day trade (não executa ordem real)."""

    ticker: str
    preco_compra: float
    preco_venda_meta: float | None = None  # preço alvo para vender
    variacao_venda_pct: float | None = None  # % de variação para vender (ex: +2.0)
    data: str = ""
    status: str = "aberta"  # aberta, fechada
    preco_venda_real: float | None = None
    lucro_prejuizo: float | None = None

    def __post_init__(self) -> None:
        if not self.data:
            self.data = datetime.now().isoformat(timespec="seconds")
        if self.preco_venda_meta is None and self.variacao_venda_pct is not None:
            self.preco_venda_meta = self.preco_compra * (1 + self.variacao_venda_pct / 100)


@dataclass
class SinalDayTrade:
    """Sinal gerado pelo monitor de day trade."""

    ticker: str
    preco_atual: float
    variacao_pct: float
    sinal: str  # "VENDA" ou "ALTA"
    motivo: str
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat(timespec="seconds")


class MonitorDayTrade:
    """Acompanha preços de ativos para day trade e avisa quando atingir meta de venda."""

    def __init__(self, store_path: Path | str | None = None):
        if store_path is None:
            from ..config import CONFIG

            store_path = CONFIG.dados_dir / "daytrade_operacoes.json"
        self.store_path = Path(store_path)
        self.operacoes: dict[str, OperacaoDayTrade] = {}
        self._carregar()

    # -------------------------------------------------- persistência
    def _carregar(self) -> None:
        if not self.store_path.exists():
            return
        try:
            dados = json.loads(self.store_path.read_text(encoding="utf-8"))
            for t, d in dados.items():
                self.operacoes[t] = OperacaoDayTrade(**d)
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            log.warning("Não foi carregar operações de day trade: %s", exc)

    def _salvar(self) -> None:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            dados = {t: o.__dict__ for t, o in self.operacoes.items()}
            self.store_path.write_text(
                json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            log.warning("Não foi salvar operações de day trade: %s", exc)

    # -------------------------------------------------- operações
    def adicionar_operacao(
        self,
        ticker: str,
        preco_compra: float,
        variacao_venda_pct: float | None = None,
        preco_venda_meta: float | None = None,
    ) -> OperacaoDayTrade:
        ticker = ticker.strip().upper()
        op = OperacaoDayTrade(
            ticker=ticker,
            preco_compra=preco_compra,
            preco_venda_meta=preco_venda_meta,
            variacao_venda_pct=variacao_venda_pct,
        )
        self.operacoes[ticker] = op
        self._salvar()
        return op

    def remover_operacao(self, ticker: str) -> bool:
        ticker = ticker.strip().upper()
        if ticker in self.operacoes:
            del self.operacoes[ticker]
            self._salvar()
            return True
        return False

    def listar_operacoes(self) -> list[dict[str, Any]]:
        return [o.__dict__ for o in self.operacoes.values()]

    # -------------------------------------------------- monitoramento
    def avaliar(self, notificar: bool = True) -> dict[str, Any]:
        """Verifica preços atuais e gera sinais de venda para operações abertas."""
        from ..monitor import Notificador

        agora = datetime.now().isoformat(timespec="seconds")
        sinais: list[SinalDayTrade] = []

        for ticker, op in self.operacoes.items():
            if op.status != "aberta":
                continue

            try:
                cot = cotacao(ticker)
                if cot.preco is None or cot.preco <= 0:
                    continue

                preco_atual = cot.preco
                variacao_pct = ((preco_atual / op.preco_compra) - 1) * 100

                # Sinal de venda se atingiu a meta
                if op.preco_venda_meta is not None and preco_atual >= op.preco_venda_meta:
                    sinal = SinalDayTrade(
                        ticker=ticker,
                        preco_atual=preco_atual,
                        variacao_pct=variacao_pct,
                        sinal="VENDA",
                        motivo=(
                            f"Preço R$ {preco_atual:,.2f} atingiu a meta de venda "
                            f"R$ {op.preco_venda_meta:,.2f} "
                            f"(+{variacao_pct:.2f}% vs compra)"
                        ),
                    )
                    sinais.append(sinal)
                    op.status = "fechada"
                    op.preco_venda_real = preco_atual
                    op.lucro_prejuizo = (preco_atual - op.preco_compra) * 1  # por ação
                    self._salvar()

                # Sinal de alta significativa (ex: +1% no dia) — apenas aviso
                elif variacao_pct >= 1.0 and variacao_pct < 5.0:
                    sinal = SinalDayTrade(
                        ticker=ticker,
                        preco_atual=preco_atual,
                        variacao_pct=variacao_pct,
                        sinal="ALTA",
                        motivo=(
                            f"Preço R$ {preco_atual:,.2f} subiu {variacao_pct:.2f}% "
                            f"na compra (R$ {op.preco_compra:,.2f})"
                        ),
                    )
                    sinais.append(sinal)

            except Exception as exc:  # noqa: BLE001
                log.warning("Falha ao avaliar day trade %s: %s", ticker, exc)

        # Enviar notificações
        canais_enviados: list[str] = []
        if notificar and sinais:
            notificador = Notificador()
            for sinal in sinais:
                canais = notificador.enviar(
                    titulo=f"Day Trade · {sinal.sinal} — {sinal.ticker}",
                    texto=sinal.motivo,
                )
                canais_enviados.extend(canais)

        self._salvar()

        return {
            "verificado_em": agora,
            "sinais": [
                {
                    "ticker": s.ticker,
                    "sinal": s.sinal,
                    "preco_atual": s.preco_atual,
                    "variacao_pct": s.variacao_pct,
                    "motivo": s.motivo,
                    "timestamp": s.timestamp,
                }
                for s in sinais
            ],
            "operacoes_abertas": sum(
                1 for o in self.operacoes.values() if o.status == "aberta"
            ),
            "canais_enviados": list(set(canais_enviados)),
        }
