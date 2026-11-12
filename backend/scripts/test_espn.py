"""
Teste exploratório da API pública da ESPN (Brasileirão = bra.1).
Rodar a partir de backend/:  python -m scripts.test_espn
"""
import json

import httpx

from datetime import date, timedelta

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1"


def get_json(url: str, params: dict | None = None) -> dict:
    resp = httpx.get(url, params=params, timeout=20, follow_redirects=True)
    print(f"[HTTP {resp.status_code}] {resp.url}")
    try:
        return resp.json()
    except ValueError:
        print("Resposta não é JSON:", resp.text[:300])
        raise


def buscar_scoreboard() -> dict:
    """
    Procura, dia a dia para trás, o primeiro dia com jogos encerrados.
    A ESPN aceita uma data única no formato YYYYMMDD (intervalo deu 400).
    """
    hoje = date.today()
    for dias_atras in range(1, 15):
        dia = (hoje - timedelta(days=dias_atras)).strftime("%Y%m%d")
        sb = get_json(f"{BASE}/scoreboard", {"dates": dia})
        eventos = sb.get("events", [])
        if any(e["status"]["type"].get("completed") for e in eventos):
            return sb
    print("Nenhum jogo encerrado nos últimos 14 dias. Usando scoreboard padrão.")
    return get_json(f"{BASE}/scoreboard")


def main() -> None:
    sb = buscar_scoreboard()

    if "leagues" not in sb:
        print("Resposta inesperada (primeiros 500 caracteres):")
        print(json.dumps(sb, ensure_ascii=False)[:500])
        return

    liga = sb["leagues"][0]
    print("Liga:", liga.get("name"), "| Temporada:", liga.get("season", {}).get("year"))

    eventos = sb.get("events", [])
    print("Jogos no scoreboard:", len(eventos))
    for e in eventos[:5]:
        print(f'  {e["id"]}  {e["date"][:10]}  {e["name"]}  ({e["status"]["type"]["name"]})')

    encerrados = [e for e in eventos if e["status"]["type"].get("completed")]
    if not encerrados:
        print("Nenhum jogo encerrado nesse scoreboard.")
        return

    ev = encerrados[0]
    print("\nEstatísticas de:", ev["name"], ev["date"][:10])
    summ = get_json(f"{BASE}/summary", {"event": ev["id"]})

    times = summ.get("boxscore", {}).get("teams", [])
    if not times:
        print("Sem boxscore. Chaves do summary:", list(summ.keys()))
        return

    for t in times:
        print(f'\n{t["team"]["displayName"]}:')
        for s in t.get("statistics", []):
            print(f'  {s["name"]}: {s["displayValue"]}')


if __name__ == "__main__":
    main()