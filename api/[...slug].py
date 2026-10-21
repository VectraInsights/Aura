"""Entrada da função serverless para tudo sob /api/*.

A Vercel resolve api/[...slug].py como a rota /api/* e ENTREGA O CAMINHO
ORIGINAL ao app ASGI. É isso que permite ao Aura ter rotas reais
(/api/perfis, /api/analise/{ticker}, ...) em vez de um caminho colapsado.

Sem este arquivo, um rewrite seria necessário — e a Vercel entrega o caminho
de DESTINO, perdendo o resto (o que quebrava os POST com 405).
"""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from src.webapp import app  # noqa: E402

__all__ = ["app"]
