"""
Cliente HTTP da API-Football v3.

Regras importantes:
1. O plano gratuito tem 100 requisições/dia. Toda chamada aqui custa cota,
   então este cliente só deve ser usado pelo ETL (scripts/), nunca por uma
   rota que o frontend chama a cada acesso.
2. A API costuma responder HTTP 200 mesmo quando há erro de negócio
   (chave inválida, temporada não liberada no plano...). O erro vem dentro
   do campo "errors" do JSON — por isso verificamos esse campo manualmente.
"""

from typing import Any

import httpx

from app.core.config import settings


class ApiFootballError(Exception):
    """Erro retornado pela API-Football (HTTP ou campo 'errors')."""


class ApiFootballClient:
    def __init__(self, api_key: str | None = None, timeout: float = 20.0):
        key = api_key or settings.apisports_key
        if not key:
            raise ApiFootballError(
                "APISPORTS_KEY não definida. Copie backend/.env.example para "
                "backend/.env e preencha a chave."
            )

        # httpx.Client reaproveita a conexão TCP entre chamadas (mais rápido)
        # e já envia o header de autenticação em toda requisição.
        self._client = httpx.Client(
            base_url=settings.apisports_base_url,
            headers={"x-apisports-key": key},
            timeout=timeout,
        )
        # Guardamos a cota restante informada pela API na última resposta.
        self.cota_restante_dia: int | None = None

    # Permite usar `with ApiFootballClient() as api:` (fecha a conexão sozinho)
    def __enter__(self) -> "ApiFootballClient":
        return self

    def __exit__(self, *exc) -> None:
        self._client.close()

    def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict:
        """Faz um GET, valida erros e devolve o JSON completo."""
        resp = self._client.get(endpoint, params=params)

        # Headers de cota (o /status não costuma enviá-los; os demais sim).
        restante = resp.headers.get("x-ratelimit-requests-remaining")
        if restante is not None:
            self.cota_restante_dia = int(restante)

        if resp.status_code == 429:
            raise ApiFootballError("Limite de requisições atingido (HTTP 429).")
        resp.raise_for_status()  # outros erros HTTP (401, 500...)

        data = resp.json()
        # "errors" pode vir como lista vazia [] ou dict {"token": "..."}
        if data.get("errors"):
            raise ApiFootballError(f"Erro da API em {endpoint}: {data['errors']}")
        return data

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------
    def status(self) -> dict:
        """Dados da conta e consumo do dia. Não consome cota."""
        return self._get("/status")["response"]

    def standings(self, league: int | None = None, season: int | None = None) -> list[dict]:
        """
        Tabela de classificação.
        Retorna a lista de times já "desembrulhada" do formato aninhado da API:
        response[0].league.standings[0] -> lista de times.
        """
        data = self._get(
            "/standings",
            params={
                "league": league or settings.league_id,
                "season": season or settings.season,
            },
        )
        if not data["response"]:
            return []  # temporada sem dados (ou não liberada no plano)
        grupos = data["response"][0]["league"]["standings"]
        return grupos[0]  # Série A é pontos corridos: um único grupo
