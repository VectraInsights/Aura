"""Configuração central. Lê o .env da raiz do projeto."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent

# .env do projeto tem precedência; sem overwrite para respeitar variáveis já setadas.
load_dotenv(RAIZ / ".env", override=False)


def _float_env(nome: str, padrao: float) -> float:
    bruto = os.getenv(nome)
    if bruto is None or bruto.strip() == "":
        return padrao
    try:
        return float(bruto)
    except ValueError:
        return padrao


def _int_env(nome: str, padrao: int) -> int:
    bruto = os.getenv(nome)
    if bruto is None or bruto.strip() == "":
        return padrao
    try:
        return int(bruto)
    except ValueError:
        return padrao


@dataclass
class Config:
    """Configuração da aplicação."""

    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", "").strip())
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip())
    gemini_temperatura: float = field(default_factory=lambda: _float_env("GEMINI_TEMPERATURE", 0.2))
    gemini_fallbacks: list[str] = field(
        default_factory=lambda: [
            m.strip()
            for m in os.getenv("GEMINI_MODELOS_FALLBACK", "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3-flash-preview").split(",")
            if m.strip()
        ]
    )
    cache_dir: Path = field(default_factory=lambda: RAIZ / os.getenv("CACHE_DIR", ".cache"))
    cache_ttl_horas: int = field(default_factory=lambda: _int_env("CACHE_TTL_HORAS", 12))
    max_tickers_analise: int = field(default_factory=lambda: _int_env("MAX_TICKERS_ANALISE", 8))
    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "HTTP_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )
    )

    @property
    def tem_chave_ia(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }


CONFIG = Config()
