"""Camada de IA: construção de prompts e schema de saída."""

from .gemini import ClienteGemini, ErroIA, ResultadoIA
from .prompt import (
    INSTRUCAO_SISTEMA,
    PERFIS_ANALISE,
    construir_prompt,
    schema_relatorio,
)

__all__ = [
    "ClienteGemini",
    "ErroIA",
    "ResultadoIA",
    "INSTRUCAO_SISTEMA",
    "PERFIS_ANALISE",
    "construir_prompt",
    "schema_relatorio",
]
