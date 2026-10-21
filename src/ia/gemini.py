"""Camada de IA: cliente Gemini (Google AI Studio, camada gratuita).

Recursos:
  * Cadeia de fallback entre modelos (o 3.8-flash e o 3-flash-latest vivem retornando
    503 por alta demanda; cair para o 3.5-flash-lite mantém o sistema de pé).
  * Retry com backoff exponencial em 429/503.
  * Saída estruturada (JSON Schema) para o relatório renderizar na UI sem parsing frágil.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import CONFIG

log = logging.getLogger(__name__)

# Erros que valem a pena repetir no MESMO modelo.
_ERROS_RETENTAVEIS = {
    "RESOURCE_EXHAUSTED",  # 429 - cota, pode liberar em segundos
    "DEADLINE_EXCEEDED",
    "INTERNAL",
}

# Erros que indicam que este modelo não vai responder agora: vale pular direto
# para o próximo da cadeia, em vez de gastar o tempo do usuário com retentativas.
_ERROS_PULAR_MODELO = {
    "UNAVAILABLE",  # 503 - alta demanda
    "NOT_FOUND",    # 404 - modelo indisponível para esta conta
}


class ErroIA(RuntimeError):
    """Falha na camada de IA."""


@dataclass
class ResultadoIA:
    """Resposta da IA já estruturada."""

    texto: str
    dados: dict[str, Any] = field(default_factory=dict)
    modelo: str = ""
    tokens_entrada: int = 0
    tokens_saida: int = 0
    segundos: float = 0.0

    @property
    def tokens_total(self) -> int:
        return self.tokens_entrada + self.tokens_saida


class ClienteGemini:
    def __init__(
        self,
        api_key: str | None = None,
        modelo: str | None = None,
        temperatura: float | None = None,
        fallbacks: list[str] | None = None,
    ):
        self.api_key = (api_key if api_key is not None else CONFIG.gemini_api_key).strip()
        self.modelo = modelo or CONFIG.gemini_model
        self.temperatura = CONFIG.gemini_temperatura if temperatura is None else temperatura
        self.fallbacks = fallbacks if fallbacks is not None else list(CONFIG.gemini_fallbacks)
        self._cliente = None

        if not self.api_key:
            raise ErroIA(
                "GEMINI_API_KEY não configurada. Copie .env.example para .env e preencha a chave "
                "(https://aistudio.google.com/apikey)."
            )

    # ------------------------------------------------------------------ interno
    @property
    def cliente(self):
        if self._cliente is None:
            from google import genai
            from google.genai import types

            self._cliente = genai.Client(
                api_key=self.api_key,
                # Sem teto de tempo, uma chamada travada segura a requisição inteira.
                http_options=types.HttpOptions(timeout=60000),
            )
        return self._cliente

    @staticmethod
    def _codigo_erro(exc: Exception) -> str:
        """Extrai o status canônico do erro (RESOURCE_EXHAUSTED, UNAVAILABLE, ...)."""
        for atributo in ("code", "status"):
            valor = getattr(exc, atributo, None)
            if valor is None:
                continue
            texto = str(valor)
            if "." in texto:  # google.rpc.Code -> 'RESOURCE_EXHAUSTED'
                texto = texto.rsplit(".", 1)[-1]
            if texto.isupper():
                return texto
        return type(exc).__name__.upper()

    # ------------------------------------------------------------------ público
    def gerar(
        self,
        prompt: str,
        instrucao_sistema: str | None = None,
        schema: Any | None = None,
        max_tentativas: int = 2,
    ) -> ResultadoIA:
        """Envia o prompt ao Gemini e devolve a resposta estruturada.

        schema: objeto types.Schema do google-genai. Quando informado, o modelo é
        obrigado a devolver JSON válido seguindo o schema.
        """
        from google.genai import types

        candidatos = [self.modelo] + [m for m in self.fallbacks if m != self.modelo]
        ultimo_erro: Exception | None = None

        for modelo in candidatos:
            config = types.GenerateContentConfig(
                temperature=self.temperatura,
                system_instruction=instrucao_sistema,
                response_mime_type="application/json" if schema is not None else "text/plain",
                response_schema=schema,
                max_output_tokens=8192,
            )

            for tentativa in range(max_tentativas):
                inicio = time.time()
                try:
                    resposta = self.cliente.models.generate_content(
                        model=modelo, contents=prompt, config=config
                    )
                    texto = (resposta.text or "").strip()
                    if not texto:
                        raise ErroIA("O modelo devolveu resposta vazia.")

                    uso = getattr(resposta, "usage_metadata", None)
                    dados: dict[str, Any] = {}
                    if schema is not None:
                        try:
                            dados = json.loads(texto)
                        except json.JSONDecodeError:
                            dados = {}

                    return ResultadoIA(
                        texto=texto,
                        dados=dados,
                        modelo=modelo,
                        tokens_entrada=getattr(uso, "prompt_token_count", 0) or 0,
                        tokens_saida=getattr(uso, "candidates_token_count", 0) or 0,
                        segundos=time.time() - inicio,
                    )
                except Exception as exc:  # noqa: BLE001 - a lib usa hierarquia própria
                    ultimo_erro = exc
                    codigo = self._codigo_erro(exc)

                    log.warning(
                        "Gemini falhou (modelo=%s tentativa=%d codigo=%s): %s",
                        modelo, tentativa + 1, codigo, str(exc)[:160],
                    )

                    if codigo in _ERROS_PULAR_MODELO:
                        # Congestionado ou inexistente: vai direto ao próximo modelo.
                        break
                    if codigo not in _ERROS_RETENTAVEIS:
                        break
                    if tentativa < max_tentativas - 1:
                        espera = 2 ** tentativa + 1
                        log.info("Aguardando %ds antes de repetir...", espera)
                        time.sleep(espera)

            # esgotou este modelo -> tenta o próximo da cadeia

        raise ErroIA(
            f"Todas as tentativas com o Gemini falharam ({len(candidatos)} modelos). "
            f"Último erro: {ultimo_erro}"
        ) from ultimo_erro

    def testar(self) -> str:
        """Chamada mínima para validar chave/modelo. Retorna o modelo que respondeu."""
        from google.genai import types

        candidatos = [self.modelo] + [m for m in self.fallbacks if m != self.modelo]
        erros: list[str] = []
        for modelo in candidatos:
            try:
                resposta = self.cliente.models.generate_content(
                    model=modelo,
                    contents="Responda apenas com a palavra OK.",
                    config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=16),
                )
                if (resposta.text or "").strip():
                    return modelo
            except Exception as exc:  # noqa: BLE001
                erros.append(f"{modelo}: {self._codigo_erro(exc)}")
        raise ErroIA("Nenhum modelo respondeu. " + " | ".join(erros))
