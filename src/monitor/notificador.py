"""Notificações do monitor: Telegram, webhook genérico e log local.

Sem configuração, tudo é registrado no histórico. Com TELEGRAM_BOT_TOKEN e
TELEGRAM_CHAT_ID, o alerta também chega no Telegram. Com WEBHOOK_URL, ele
vai para qualquer endpoint (n8n, Make, Discord, Slack...).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Notificador:
    """Envia alertas para os canais configurados.

    Canais (todos opcionais e independentes):
      * Telegram    — TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
      * WhatsApp   — três modos, ver _whatsapp()
      * Webhook     — WEBHOOK_URL (n8n, Make, Discord, Slack, qualquer HTTP)
    """

    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", "").strip())
    webhook_url: str = field(default_factory=lambda: os.getenv("WEBHOOK_URL", "").strip())

    # WhatsApp — modo 1: CallMeBot (grátis, sem cadastro de empresa)
    callmebot_phone: str = field(default_factory=lambda: os.getenv("CALLMEBOT_PHONE", "").strip())
    callmebot_apikey: str = field(default_factory=lambda: os.getenv("CALLMEBOT_APIKEY", "").strip())
    # WhatsApp — modo 2: Meta Cloud API (profissional)
    whatsapp_token: str = field(default_factory=lambda: os.getenv("WHATSAPP_TOKEN", "").strip())
    whatsapp_phone_id: str = field(default_factory=lambda: os.getenv("WHATSAPP_PHONE_ID", "").strip())
    whatsapp_template: str = field(default_factory=lambda: os.getenv("WHATSAPP_TEMPLATE", "aura_alerta").strip())
    whatsapp_destino: str = field(default_factory=lambda: os.getenv("WHATSAPP_DESTINO", "").strip())

    def enviar(self, titulo: str, texto: str, dados: dict[str, Any] | None = None) -> list[str]:
        """Dispara o alerta. Devolve a lista de canais que responderam."""
        canais: list[str] = []

        if self.telegram_token and self.telegram_chat_id:
            if self._telegram(titulo, texto):
                canais.append("telegram")

        if self._whatsapp_disponivel():
            if self._whatsapp(titulo, texto):
                canais.append("whatsapp")

        if self.webhook_url:
            if self._webhook(titulo, texto, dados or {}):
                canais.append("webhook")

        return canais

    # ------------------------------------------------------------------ interno
    def _whatsapp_disponivel(self) -> bool:
        return bool(
            (self.callmebot_phone and self.callmebot_apikey)
            or (self.whatsapp_token and self.whatsapp_phone_id)
        )

    def _whatsapp(self, titulo: str, texto: str) -> bool:
        """Modo 1: CallMeBot. Modo 2: Meta Cloud API."""
        if self.callmebot_phone and self.callmebot_apikey:
            return self._whatsapp_callmebot(texto)
        if self.whatsapp_token and self.whatsapp_phone_id:
            return self._whatsapp_cloud(titulo, texto)
        return False

    def _whatsapp_callmebot(self, texto: str) -> bool:
        """CallMeBot: gratuito. Exige enviar /start para o bot uma única vez.

        O número deve estar no formato internacional, só dígitos (ex.: 5511999999999).
        """
        try:
            import requests

            resposta = requests.get(
                "https://api.callmebot.com/whatsapp.php",
                params={"phone": self.callmebot_phone, "text": texto, "apikey": self.callmebot_apikey},
                timeout=20,
            )
            if resposta.status_code != 200:
                log.warning("CallMeBot respondeu %s: %s", resposta.status_code, resposta.text[:200])
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha no CallMeBot: %s", exc)
            return False

    def _whatsapp_cloud(self, titulo: str, texto: str) -> bool:
        """Meta Cloud API (WhatsApp Business).

        A Meta exige template aprovado para notificação fora da janela de 24h.
        O template deve conter a variável {{1}}.
        """
        try:
            import requests

            url = f"https://graph.facebook.com/v21.0/{self.whatsapp_phone_id}/messages"
            corpo = {
                "messaging_product": "whatsapp",
                "to": self.whatsapp_destino,
                "type": "template",
                "template": {
                    "name": self.whatsapp_template,
                    "language": {"code": "pt_BR"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": f"{titulo}\n{texto}"}],
                        }
                    ],
                },
            }
            resposta = requests.post(
                url,
                json=corpo,
                headers={"Authorization": f"Bearer {self.whatsapp_token}"},
                timeout=20,
            )
            if resposta.status_code >= 400:
                log.warning("WhatsApp Cloud respondeu %s: %s", resposta.status_code, resposta.text[:250])
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha no WhatsApp Cloud API: %s", exc)
            return False

    def _telegram(self, titulo: str, texto: str) -> bool:
        try:
            import requests

            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            resposta = requests.post(
                url,
                json={
                    "chat_id": self.telegram_chat_id,
                    "text": f"*{titulo}*\n{texto}",
                    "parse_mode": "Markdown",
                },
                timeout=15,
            )
            if resposta.status_code != 200:
                log.warning("Telegram respondeu %s: %s", resposta.status_code, resposta.text[:200])
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha ao enviar para o Telegram: %s", exc)
            return False

    def _webhook(self, titulo: str, texto: str, dados: dict[str, Any]) -> bool:
        try:
            import requests

            resposta = requests.post(
                self.webhook_url,
                json={"titulo": titulo, "texto": texto, "dados": dados},
                timeout=15,
            )
            if resposta.status_code >= 400:
                log.warning("Webhook respondeu %s", resposta.status_code)
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha no webhook: %s", exc)
            return False


def link_whatsapp(texto: str, numero: str | None = None) -> str:
    """Link wa.me: abre o WhatsApp com a mensagem já escrita.

    Funciona sem nenhuma credencial — é o caminho universal de "compartilhar
    no WhatsApp" a partir do Telegram ou do webhook.
    """
    from urllib.parse import quote

    digitos = "".join(c for c in (numero or "") if c.isdigit())
    base = f"https://wa.me/{digitos}" if digitos else "https://wa.me/"
    return f"{base}?text={quote(texto[:900])}"


def formatar_alerta(regra, preco: float, variacao: float | None) -> tuple[str, str]:
    """(título, texto) do alerta de uma regra disparada."""
    from .regras import ACOES, OPERADORES

    acao = ACOES.get(regra.acao, regra.acao)
    condicao = OPERADORES.get(regra.operador, regra.operador)

    titulo = f"Aura · {acao}: {regra.ativo}"
    linhas = [
        f"Preço atual: **{preco:,.4f}**".replace(",", "@").replace(".", ",").replace("@", "."),
    ]
    if variacao is not None:
        linhas.append(f"Variação no dia: {variacao:+.2f}%".replace(".", ","))
    if regra.operador == "entre" and regra.valor2 is not None:
        linhas.append(f"Gatilho: entre {regra.valor:g} e {regra.valor2:g}".replace(".", ","))
    else:
        linhas.append(f"Gatilho: {condicao} {regra.valor:g}".replace(".", ","))
    if regra.ativo_alvo:
        linhas.append(f"Ação sugerida: **{acao} {regra.ativo_alvo}**")
    else:
        linhas.append(f"Ação sugerida: **{acao}**")
    if regra.nota:
        linhas.append(f"Observação: {regra.nota}")

    destino = os.getenv("WHATSAPP_DESTINO", "").strip()
    if destino:
        linhas.append("")
        linhas.append(f"Compartilhar no WhatsApp: {link_whatsapp(chr(10).join(linhas), destino)}")

    return titulo, "\n".join(linhas)


def montar_payload(regra, preco: float, variacao: float | None) -> dict[str, Any]:
    """Payload bruto do webhook, com os números sem formatação."""
    return {
        "regra": regra.to_dict(),
        "preco_atual": preco,
        "variacao_dia": variacao,
        "descricao": regra.descrever(),
        "acionado_em": regra.ultimo_trigger,
        "json": json.dumps({"tipo": "aura_alerta"}, ensure_ascii=False),
    }
