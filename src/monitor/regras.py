"""Regras de monitoramento: modelo, persistência e consulta.

Uma regra é do tipo "quando <ativo> <condição> <valor>, <ação>". Serve para
qualquer instrumento com cotação: ação da B3, FII, dólar, euro, cripto,
índice, commodity ou qualquer símbolo do Yahoo Finance.

Ação é sempre uma NOTIFICAÇÃO — a aplicação não executa ordens em corretora.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Condições suportadas.
OPERADORES = {
    ">=": "atinge ou ultrapassa",
    "<=": "atinge ou fica abaixo",
    ">": "ultrapassa",
    "<": "fica abaixo",
    "entre": "fica entre dois valores",
}

#: Ações possíveis.
ACOES = {
    "vender": "Vender",
    "comprar": "Comprar",
    "alertar": "Alertar",
    "revisar": "Revisar posição",
}


@dataclass
class Regra:
    """Uma regra de monitoramento."""

    ativo: str
    operador: str = ">="
    valor: float = 0.0
    valor2: float | None = None
    acao: str = "alertar"
    ativo_alvo: str | None = None
    nota: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    criada_em: str = ""
    rearmar: bool = True
    # Estado de execução (preenchido pelo motor)
    armada: bool = True
    ultimo_preco: float | None = None
    ultimo_trigger: str | None = None
    ultima_verificacao: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, dados: dict[str, Any]) -> "Regra":
        conhecidos = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (dados or {}).items() if k in conhecidos})

    def descrever(self) -> str:
        """Texto legível da regra, para notificação e UI."""
        if self.operador == "entre" and self.valor2 is not None:
            condicao = f"ficar entre {self.valor:g} e {self.valor2:g}"
        else:
            condicao = f"{OPERADORES.get(self.operador, self.operador)} {self.valor:g}"
        texto = f"{self.ativo} {condicao}"
        acao = ACOES.get(self.acao, self.acao)
        if self.ativo_alvo:
            texto += f" → {acao} {self.ativo_alvo}"
        else:
            texto += f" → {acao}"
        return texto

    def condicao_atendida(self, preco: float | None) -> bool:
        if preco is None:
            return False
        if self.operador == "entre":
            if self.valor2 is None:
                return False
            baixo, alto = sorted([self.valor, self.valor2])
            return baixo <= preco <= alto
        if self.operador == ">=":
            return preco >= self.valor
        if self.operador == "<=":
            return preco <= self.valor
        if self.operador == ">":
            return preco > self.valor
        if self.operador == "<":
            return preco < self.valor
        return False


# ------------------------------------------------------------------ persistência
class Armazenamento:
    """Interface de persistência das regras."""

    def carregar(self) -> list[Regra]:  # pragma: no cover - interface
        raise NotImplementedError

    def salvar(self, regras: list[Regra]) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ArmazenamentoArquivo(Armazenamento):
    """Regras em arquivo JSON (padrão). Funciona em qualquer disco persistente."""

    def __init__(self, caminho: Path | str | None = None):
        if caminho is None:
            caminho = os.getenv("REGRAS_ARQUIVO")
        if not caminho:
            from ..config import CONFIG

            caminho = CONFIG.dados_dir / "regras.json"
        self.caminho = Path(caminho)

    def carregar(self) -> list[Regra]:
        if not self.caminho.exists():
            return []
        try:
            bruto = json.loads(self.caminho.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Não foi possível ler as regras: %s", exc)
            return []
        return [Regra.from_dict(r) for r in bruto or []]

    def salvar(self, regras: list[Regra]) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.caminho.write_text(
            json.dumps([r.to_dict() for r in regras], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class ArmazenamentoRedis(Armazenamento):
    """Regras no Upstash Redis (REST, sem SDK). Útil na Vercel, onde o disco é efêmero.

    Exige UPSTASH_REDIS_REST_URL e UPSTASH_REDIS_REST_TOKEN no ambiente.
    """

    CHAVE = "aura:regras"

    def __init__(self, url: str | None = None, token: str | None = None):
        self.url = (url or os.getenv("UPSTASH_REDIS_REST_URL", "")).rstrip("/")
        self.token = token or os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

    @property
    def disponivel(self) -> bool:
        return bool(self.url and self.token)

    def _comando(self, comando: list[str]) -> Any:
        import requests

        resposta = requests.post(
            self.url,
            json=comando,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=12,
        )
        resposta.raise_for_status()
        return resposta.json().get("result")

    def carregar(self) -> list[Regra]:
        if not self.disponivel:
            return []
        bruto = self._comando(["GET", self.CHAVE])
        if not bruto:
            return []
        try:
            dados = json.loads(bruto)
        except json.JSONDecodeError:
            return []
        return [Regra.from_dict(r) for r in dados or []]

    def salvar(self, regras: list[Regra]) -> None:
        if not self.disponivel:
            raise RuntimeError(
                "UPSTASH_REDIS_REST_URL e UPSTASH_REDIS_REST_TOKEN não configurados."
            )
        self._comando(["SET", self.CHAVE, json.dumps([r.to_dict() for r in regras], ensure_ascii=False)])


def armazenamento() -> Armazenamento:
    """Escolhe o backend: Upstash se configurado, senão arquivo."""
    redis = ArmazenamentoRedis()
    if redis.disponivel:
        return redis
    return ArmazenamentoArquivo()
