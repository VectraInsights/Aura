"""Motor do monitor: avalia as regras e dispara os alertas.

Duas decisões importantes:

1. **Anti-spam por histerese.** Uma regra só notifica na transição
   "não atendida → atendida". Se o preço continua acima do gatilho, não
   repete o aviso a cada ciclo. A regra se rearma quando a condição deixa de
   valer (ou quando `rearmar` está desligado, o aviso é único).

2. **A regra não executa ordens.** Ela avisa; quem executa é o usuário (ou uma
   automação que consome o webhook).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .notificador import Notificador, formatar_alerta, montar_payload
from .precos import cotacao
from .regras import Armazenamento, Regra, armazenamento
from ..projecao import sugerir_operacao, projetar

log = logging.getLogger(__name__)


@dataclass
class Alerta:
    """Um disparo registrado."""

    regra_id: str
    ativo: str
    acao: str
    preco: float
    variacao_dia: float | None
    descricao: str
    canais: list[str] = field(default_factory=list)
    em: str = ""
    regime: str = ""


@dataclass
class ResultadoAvaliacao:
    """Resumo de um ciclo de verificação."""

    verificado_em: str
    cotacoes: dict[str, Any] = field(default_factory=dict)
    disparadas: list[Alerta] = field(default_factory=list)
    regras_ativas: int = 0
    regras_total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "verificado_em": self.verificado_em,
            "cotacoes": self.cotacoes,
            "disparadas": [a.__dict__ for a in self.disparadas],
            "regras_ativas": self.regras_ativas,
            "regras_total": self.regras_total,
        }


class Motor:
    """Avalia regras contra o mercado e dispara notificações."""

    def __init__(
        self,
        store: Armazenamento | None = None,
        notificador: Notificador | None = None,
        historico: Path | str | None = None,
    ):
        self.store = store or armazenamento()
        self.notificador = notificador or Notificador()
        if historico is None:
            historico = os.getenv("HISTORICO_ALERTAS")
        if not historico:
            from ..config import CONFIG

            historico = CONFIG.dados_dir / "alertas.jsonl"
        self.historico = Path(historico)

    # ------------------------------------------------------------------ regras
    def regras(self) -> list[Regra]:
        return self.store.carregar()

    def salvar_regras(self, regras: list[Regra]) -> None:
        self.store.salvar(regras)

    def adicionar(self, regra: Regra) -> Regra:
        if not regra.criada_em:
            regra.criada_em = datetime.now().isoformat(timespec="seconds")
        regras = self.regras()
        regras.append(regra)
        self.salvar_regras(regras)
        return regra

    def atualizar(self, id_regra: str, dados: dict[str, Any]) -> Regra | None:
        regras = self.regras()
        for regra in regras:
            if regra.id == id_regra:
                for campo, valor in (dados or {}).items():
                    if hasattr(regra, campo):
                        setattr(regra, campo, valor)
                self.salvar_regras(regras)
                return regra
        return None

    def remover(self, id_regra: str) -> bool:
        regras = self.regras()
        restantes = [r for r in regras if r.id != id_regra]
        if len(restantes) == len(regras):
            return False
        self.salvar_regras(restantes)
        return True

    # -------------------------------------------------------------- avaliação
    def avaliar(self, notificar: bool = True) -> ResultadoAvaliacao:
        """Roda um ciclo: busca preços, avalia e dispara o que for novo."""
        agora = datetime.now().isoformat(timespec="seconds")
        resultado = ResultadoAvaliacao(verificado_em=agora)

        regras = self.regras()
        resultado.regras_total = len(regras)
        if not regras:
            return resultado

        # Uma única leitura por ativo.
        precios: dict[str, Any] = {}
        for regra in regras:
            chave = regra.ativo.strip().upper()
            if chave not in precios:
                precios[chave] = cotacao(regra.ativo)
            resultado.cotacoes[chave] = {
                "simbolo": precios[chave].simbolo,
                "preco": precios[chave].preco,
                "variacao_dia": precios[chave].variacao_dia,
                "fonte": precios[chave].fonte,
            }

        disparadas: list[Alerta] = []
        for regra in regras:
            cot = precios[regra.ativo.strip().upper()]
            regra.ultimo_preco = cot.preco
            regra.ultima_verificacao = agora

            atendida = regra.condicao_atendida(cot.preco)

            if atendida:
                if notificar:
                    if not regra.armada:
                        # Já avisou e a condição continua valendo: anti-spam.
                        continue
                    # Disparo real: rearma só quando a condição deixar de valer.
                    regra.armada = False
                    regra.ultimo_trigger = agora
                    canais: list[str] = []
                    if cot.preco is not None:
                        titulo, texto = formatar_alerta(regra, cot.preco, cot.variacao_dia)
                        canais = self.notificador.enviar(
                            titulo, texto, montar_payload(regra, cot.preco, cot.variacao_dia)
                        )
                    disparadas.append(
                        Alerta(
                            regra_id=regra.id,
                            ativo=regra.ativo,
                            acao=regra.acao,
                            preco=cot.preco or 0.0,
                            variacao_dia=cot.variacao_dia,
                            descricao=regra.descrever(),
                            canais=canais,
                            em=agora,
                            regime=regra.nota or "",
                        )
                    )
                else:
                    # Simulação (listagem na tela): mostra o que está em gatilho
                    # agora, sem consumir o gatilho nem alterar o estado.
                    disparadas.append(
                        Alerta(
                            regra_id=regra.id,
                            ativo=regra.ativo,
                            acao=regra.acao,
                            preco=cot.preco or 0.0,
                            variacao_dia=cot.variacao_dia,
                            descricao=regra.descrever(),
                            canais=[],
                            em=agora,
                            regime="já avisado" if not regra.armada else "simulação",
                        )
                    )
            elif not atendida and not regra.armada and notificar:
                # Saiu da condição: rearma para poder avisar de novo.
                regra.armada = True

        resultado.disparadas = disparadas
        resultado.regras_ativas = sum(1 for r in regras if r.ultimo_preco is not None)

        if notificar:
            # Só a execução real grava estado e histórico.
            self.salvar_regras(regras)
            self._registrar_alertas(disparadas)
        return resultado

    # --------------------------------------------------------------- histórico
    def _registrar_alertas(self, alertas: list[Alerta]) -> None:
        if not alertas:
            return
        try:
            self.historico.parent.mkdir(parents=True, exist_ok=True)
            with self.historico.open("a", encoding="utf-8") as arquivo:
                for alerta in alertas:
                    arquivo.write(json.dumps(alerta.__dict__, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("Não foi possível gravar o histórico: %s", exc)

    def historico_alertas(self, limite: int = 50) -> list[dict[str, Any]]:
        if not self.historico.exists():
            return []
        try:
            linhas = self.historico.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        saida: list[dict[str, Any]] = []
        for linha in linhas[-limite:]:
            if linha.strip():
                try:
                    saida.append(json.loads(linha))
                except json.JSONDecodeError:
                    continue
        return list(reversed(saida))

    # -------------------------------------------------- sugestões de operação
    def _caminho_sugestoes(self) -> Path:
        from ..config import CONFIG

        return CONFIG.dados_dir / "sugestoes.json"

    def _carregar_sugestoes(self) -> dict[str, dict[str, Any]]:
        caminho = self._caminho_sugestoes()
        if not caminho.exists():
            return {}
        try:
            return json.loads(caminho.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _salvar_sugestoes(self, sugestoes: dict[str, dict[str, Any]]) -> None:
        caminho = self._caminho_sugestoes()
        try:
            caminho.parent.mkdir(parents=True, exist_ok=True)
            caminho.write_text(
                json.dumps(sugestoes, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Não foi possível salvar sugestões: %s", exc)

    def avaliar_sugestoes(
        self,
        tickers: list[str] | None = None,
        taxa_desconto: float = 0.10,
        notificar: bool = True,
        limiar_upside: float = 15.0,
        limiar_downside: float = -15.0,
        auto: bool = False,
    ) -> dict[str, Any]:
        """Avalia sugestões de operação para uma lista de tickers.

        Se auto=True, descobre os tickers automaticamente a partir da triagem
        (perfil qualidade) e do radar, sem precisar informar um por um.

        Compara a sugestão atual com a última registrada. Envia alerta por email
        (e outros canais configurados) quando:
          * A sugestão muda (ex: MANTER → COMPRA)
          * O upside chega ao limiar (preço bem abaixo do justo)
          * O downside chega ao limiar (preço bem acima do justo)
        """
        from ..analise import get_analista

        if auto:
            tickers = self._descobrir_tickers_auto()

        if not tickers:
            return {
                "verificado_em": datetime.now().isoformat(timespec="seconds"),
                "avaliados": 0,
                "enviadas": [],
                "sugestoes": {},
                "mensagem": "Nenhum ticker para avaliar.",
            }

        agora = datetime.now().isoformat(timespec="seconds")
        anterior = self._carregar_sugestoes()
        novas: dict[str, dict[str, Any]] = {}
        enviadas: list[dict[str, Any]] = []

        for ticker in tickers:
            try:
                ctx = get_analista().contexto(ticker)
                if not ctx.indicadores and not ctx.balanco:
                    continue

                res = sugerir_operacao(
                    ticker, ctx.as_dict(), taxa_desconto=taxa_desconto
                )
                proj = projetar(ticker, ctx.as_dict(), taxa_desconto=taxa_desconto)

                registro_atual = {
                    "sugestao": res.sugestao.value,
                    "score": res.score,
                    "preco_atual": proj.preco_atual,
                    "preco_justo": proj.preco_justo,
                    "upside": proj.upside,
                    "dy_atual": proj.dy_atual,
                    "avaliado_em": agora,
                }
                novas[ticker] = registro_atual

                registro_anterior = anterior.get(ticker)
                sugestao_mudou = (
                    registro_anterior is None
                    or registro_anterior.get("sugestao") != res.sugestao.value
                )

                upside = proj.upside
                preco_cruza_justo = False
                if upside is not None:
                    if upside >= limiar_upside:
                        preco_cruza_justo = True  # preço muito abaixo do justo → compra
                    elif upside <= limiar_downside:
                        preco_cruza_justo = True  # preço muito acima do justo → venda

                deve_notificar = notificar and (sugestao_mudou or preco_cruza_justo)

                if deve_notificar and res.texto_whatsapp:
                    titulo = f"Aura · {res.sugestao.value} — {ticker}"
                    canais = self.notificador.enviar(
                        titulo, res.texto_whatsapp
                    )
                    enviadas.append({
                        "ticker": ticker,
                        "sugestao": res.sugestao.value,
                        "score": res.score,
                        "preco_atual": proj.preco_atual,
                        "preco_justo": proj.preco_justo,
                        "upside": proj.upside,
                        "motivo": res.motivo,
                        "canais": canais,
                        "em": agora,
                    })

            except Exception as exc:  # noqa: BLE001
                log.warning("Falha ao avaliar sugestão para %s: %s", ticker, exc)

        self._salvar_sugestoes(novas)

        return {
            "verificado_em": agora,
            "avaliados": len(novas),
            "enviadas": enviadas,
            "sugestoes": {k: v for k, v in novas.items()},
        }

    def _descobrir_tickers_auto(self) -> list[str]:
        """Descobre tickers automaticamente a partir da triagem e radar.

        União dos tickers que aparecem nas triagens de qualidade, valor e
        dividendário, limitados aos 30 primeiros de cada um para não sobrecarregar.
        """
        from ..analise import get_analista
        from ..screen import PERFIS_TELA

        analista = get_analista()
        descobertos: set[str] = set()

        for chave in ["qualidade", "valor", "dividendario"]:
            if chave not in PERFIS_TELA:
                continue
            try:
                dados = analista.varredura(
                    perfil_chave=chave, limite=30, incluir_fiis=False
                )
                for r in dados.get("resultados", []) or []:
                    t = r.get("ticker")
                    if t:
                        descobertos.add(t)
            except Exception as exc:  # noqa: BLE001
                log.warning("Não foi descobrir tickers do perfil %s: %s", chave, exc)

        descobertos.update(self.carregar_tickers_radar())

        return sorted(descobertos)

    # -------------------------------------------------- tickers do radar
    def _caminho_tickers_radar(self) -> Path:
        from ..config import CONFIG

        return CONFIG.dados_dir / "tickers_radar.json"

    def carregar_tickers_radar(self) -> list[str]:
        """Carrega tickers salvos pelo radar para monitoramento."""
        caminho = self._caminho_tickers_radar()
        if not caminho.exists():
            return []
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
            return dados.get("tickers", [])
        except (json.JSONDecodeError, OSError):
            return []

    def salvar_tickers_radar(self, tickers: list[str]) -> None:
        """Salva tickers recomendados pelo radar para monitoramento."""
        caminho = self._caminho_tickers_radar()
        try:
            caminho.parent.mkdir(parents=True, exist_ok=True)
            caminho.write_text(
                json.dumps({"tickers": sorted(set(tickers))}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Não foi possível salvar tickers do radar: %s", exc)

    def adicionar_tickers_radar(self, tickers: list[str]) -> None:
        """Adiciona tickers do radar à lista de monitoramento (não substitui)."""
        existentes = self.carregar_tickers_radar()
        novos = list(set(existentes + tickers))
        self.salvar_tickers_radar(novos)
