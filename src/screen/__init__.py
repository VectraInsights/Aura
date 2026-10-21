"""Motor de triagem de ações."""

from .criterios import (
    PERFIS_TELA,
    Perfil,
    aplicar_perfil,
    graham_number,
    montar_ranking,
    obter_perfil,
    ordenar,
    resumo_colunas,
)

__all__ = [
    "PERFIS_TELA", "Perfil", "aplicar_perfil", "graham_number",
    "montar_ranking", "obter_perfil", "ordenar", "resumo_colunas",
]
