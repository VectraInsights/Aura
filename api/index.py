"""Entrada da função serverless na rota /api.

A rota /api/* é servida por api/[...slug].py (rota dinâmica da Vercel, que
preserva o caminho original). Este arquivo cobre a barra /api exata.
"""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from src.webapp import app  # noqa: E402

__all__ = ["app"]
