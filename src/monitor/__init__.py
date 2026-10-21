"""Monitor de regras: avalia o mercado continuamente e avisa quando um gatilho dispara.

Importante: a regra NÃO executa ordens em corretora. Ela avisa — por Telegram,
WhatsApp ou webhook — e quem decide agir é o usuário (ou uma automação que
consuma o webhook).

Rodando:
    python run.py monitor --intervalo 60
"""

from .motor import Alerta, Motor, ResultadoAvaliacao
from .notificador import Notificador, formatar_alerta, link_whatsapp
from .precos import Cotacao, cotacao, resolver
from .regras import ACOES, OPERADORES, Regra, armazenamento

__all__ = [
    "Alerta", "Motor", "ResultadoAvaliacao", "Notificador", "formatar_alerta",
    "link_whatsapp", "Cotacao", "cotacao", "resolver", "ACOES", "OPERADORES",
    "Regra", "armazenamento",
]
