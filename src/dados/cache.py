"""Cache em disco (JSON) com TTL, para não re-scrapar nem estourar rate limit."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from ..config import CONFIG


class Cache:
    def __init__(self, diretorio: Path | None = None, ttl_horas: int | None = None):
        self.diretorio = Path(diretorio or CONFIG.cache_dir)
        self.ttl_segundos = (ttl_horas if ttl_horas is not None else CONFIG.cache_ttl_horas) * 3600
        self.diretorio.mkdir(parents=True, exist_ok=True)

    def _caminho(self, namespace: str, chave: str) -> Path:
        digest = hashlib.sha256(f"{namespace}|{chave}".encode("utf-8")).hexdigest()[:24]
        return self.diretorio / f"{namespace}_{digest}.json"

    def get(self, namespace: str, chave: str) -> Any | None:
        caminho = self._caminho(namespace, chave)
        if not caminho.exists():
            return None
        try:
            payload = json.loads(caminho.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if time.time() - payload.get("ts", 0) > self.ttl_segundos:
            return None
        return payload.get("data")

    def set(self, namespace: str, chave: str, dados: Any) -> None:
        caminho = self._caminho(namespace, chave)
        try:
            caminho.write_text(
                json.dumps({"ts": time.time(), "data": dados}, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except OSError:
            pass  # cache é otimização, nunca deve derrubar o fluxo

    def limpar(self) -> int:
        removidos = 0
        for arquivo in self.diretorio.glob("*.json"):
            try:
                arquivo.unlink()
                removidos += 1
            except OSError:
                pass
        return removidos
