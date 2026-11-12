"""
Teste de conexão com a API-Football.

Como rodar (a partir da pasta backend/):
    python -m scripts.test_connection

O que ele faz:
1. GET /status    -> confirma que a chave funciona e mostra a cota do dia
                     (esse endpoint NÃO consome cota).
2. GET /standings -> busca a tabela da Série A (league=71, season=2026)
                     e imprime a classificação. Consome 1 requisição.
"""

import sys

from app.core.config import settings
from app.services.api_football import ApiFootballClient, ApiFootballError


def mostrar_status(api: ApiFootballClient) -> None:
    info = api.status()
    conta = info.get("account", {})
    plano = info.get("subscription", {})
    uso = info.get("requests", {})

    print("=== /status ===")
    print(f"Conta : {conta.get('firstname', '')} {conta.get('lastname', '')}".rstrip())
    print(f"Plano : {plano.get('plan')} (ativo: {plano.get('active')}, até {plano.get('end')})")
    print(f"Cota  : {uso.get('current')}/{uso.get('limit_day')} requisições usadas hoje")
    print()


def mostrar_tabela(api: ApiFootballClient) -> None:
    print(f"=== /standings?league={settings.league_id}&season={settings.season} ===")
    tabela = api.standings()

    if not tabela:
        print(
            "Nenhum dado retornado. Possíveis causas: temporada ainda sem jogos "
            "ou temporada não liberada no plano gratuito."
        )
        return

    print(f"{'#':>2}  {'Time':<24}{'P':>4}{'J':>4}{'SG':>5}  Forma")
    for linha in tabela:
        time = linha["team"]
        jogos = linha["all"]
        print(
            f"{linha['rank']:>2}  {time['name']:<24}"
            f"{linha['points']:>4}{jogos['played']:>4}{linha['goalsDiff']:>5}"
            f"  {linha.get('form') or '-'}"
        )

    print(f"\n{len(tabela)} times encontrados.")
    if api.cota_restante_dia is not None:
        print(f"Cota restante hoje: {api.cota_restante_dia}")


def main() -> int:
    try:
        with ApiFootballClient() as api:
            mostrar_status(api)
            mostrar_tabela(api)
    except ApiFootballError as e:
        print(f"[ERRO] {e}")
        return 1
    except Exception as e:  # erro de rede, timeout etc.
        print(f"[ERRO inesperado] {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
