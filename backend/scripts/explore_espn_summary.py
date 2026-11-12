"""
Exploração do `summary` da ESPN: o que ele TEM e o que NÃO tem.

Como rodar (a partir da pasta backend/):
    python -m scripts.explore_espn_summary

Por que este script existe?
O relatório da IA precisa de H2H, desfalques, árbitro, escalações, odds e
forma recente. Antes de modelar o banco, queremos saber o que a ESPN já
entrega pronto (dado ESTRUTURADO, em um campo do JSON) e o que só aparece em
TEXTO LIVRE (comentários, notícias) e, portanto, vai ter que vir do RAG.

O que ele faz:
1. Lista os jogos encerrados dos últimos 14 dias e escolhe o mais recente.
2. Pega também o próximo jogo agendado, porque escalação, odds e árbitro
   costumam ser publicados ANTES do jogo.
3. Para cada um, lista as chaves de topo do `summary` e roda um checklist.
4. Mostra uma amostra de `estatisticas_partida`, `standings()` e `times()`.
"""

import json
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from app.services.espn import EspnClient, EspnError, normalizar_boxscore

DIAS_PARA_TRAS = 14
LIMITE_AMOSTRA = 220  # caracteres de JSON mostrados por amostra
MAX_CAMINHOS = 6  # caminhos mostrados por item do checklist
MAX_MENCOES = 3  # exemplos de menções em texto livre


@dataclass(frozen=True)
class ItemProcurado:
    """
    Uma informação que queremos achar no JSON.

    - termos: pedaços de NOME DE CHAVE (minúsculos) que indicam o dado.
    - ignorar: pedaços que geram falso positivo (ex.: "form" bate em "format").
    - termos_texto: palavras procuradas nos VALORES de texto, para ver se o
      dado aparece só como texto livre.
    """

    nome: str
    termos: tuple[str, ...]
    ignorar: tuple[str, ...] = ()
    termos_texto: tuple[str, ...] = field(default=())

    def bate_com_chave(self, chave: str) -> bool:
        chave = chave.lower()
        if any(pedaco in chave for pedaco in self.ignorar):
            return False
        return any(pedaco in chave for pedaco in self.termos)


CHECKLIST: list[ItemProcurado] = [
    ItemProcurado("Confronto direto (H2H)", ("seasonseries", "headtohead", "h2h")),
    ItemProcurado(
        "Lesões / desfalques",
        ("injur", "suspen", "absent", "unavailable"),
        termos_texto=("injur", "suspen"),
    ),
    ItemProcurado(
        "Árbitro",
        ("official", "referee"),
        termos_texto=("referee", "official"),
    ),
    ItemProcurado("Escalações", ("rosters", "formation", "lineup")),
    ItemProcurado("Odds", ("odds", "pickcenter")),
    ItemProcurado(
        "Forma recente",
        ("lastfivegames", "form"),
        ignorar=("format", "formation", "platform", "uniform"),
    ),
]


# ----------------------------------------------------------------------
# Navegação genérica no JSON
# ----------------------------------------------------------------------
def percorrer(obj: Any, caminho: str = "$") -> Iterator[tuple[str, str | None, Any]]:
    """
    Visita TODOS os nós do JSON e devolve (caminho, chave, valor).

    Ex.: ("$.rosters[0].formation", "formation", "4-1-4-1").
    Itens de lista não têm chave, então vêm com chave=None.
    """
    if isinstance(obj, dict):
        for chave, valor in obj.items():
            filho = f"{caminho}.{chave}"
            yield filho, chave, valor
            yield from percorrer(valor, filho)
    elif isinstance(obj, list):
        for i, valor in enumerate(obj):
            filho = f"{caminho}[{i}]"
            yield filho, None, valor
            yield from percorrer(valor, filho)


def generalizar(caminho: str) -> str:
    """
    Troca índices de lista por [*] para agrupar caminhos repetidos:
    $.rosters[0].roster[7].formationPlace -> $.rosters[*].roster[*].formationPlace
    """
    return re.sub(r"\[\d+\]", "[*]", caminho)


def buscar_chaves(obj: Any, item: ItemProcurado) -> list[tuple[str, Any]]:
    """
    Caminhos (generalizados, sem repetição) cujas CHAVES batem com o item.
    Para cada caminho guardamos o primeiro valor encontrado, como amostra.
    """
    encontrados: dict[str, Any] = {}
    for caminho, chave, valor in percorrer(obj):
        if chave is not None and item.bate_com_chave(chave):
            encontrados.setdefault(generalizar(caminho), valor)
    return list(encontrados.items())


def buscar_mencoes_texto(obj: Any, termos: tuple[str, ...]) -> list[tuple[str, str]]:
    """Valores de TEXTO que mencionam algum dos termos (dado não estruturado)."""
    return [
        (caminho, valor)
        for caminho, _, valor in percorrer(obj)
        if isinstance(valor, str) and any(t in valor.lower() for t in termos)
    ]


def amostra(valor: Any) -> str:
    """JSON compacto e cortado, para caber numa linha do terminal."""
    texto = json.dumps(valor, ensure_ascii=False)
    return texto if len(texto) <= LIMITE_AMOSTRA else texto[:LIMITE_AMOSTRA] + "..."


def vazio(valor: Any) -> bool:
    """Lista/dict vazios ou None contam como 'chave existe, mas sem dado'."""
    return valor is None or valor == [] or valor == {}


# ----------------------------------------------------------------------
# Relatórios
# ----------------------------------------------------------------------
def listar_chaves_topo(summary: dict) -> None:
    """Mostra cada chave de topo do summary com o tipo e o tamanho."""
    print("Chaves de topo do summary:")
    for chave, valor in summary.items():
        if isinstance(valor, list):
            descricao = f"lista ({len(valor)} itens)"
        elif isinstance(valor, dict):
            descricao = f"objeto ({', '.join(list(valor)[:6])})"
        else:
            descricao = f"{type(valor).__name__} = {valor!r}"
        print(f"  - {chave:<20} {descricao}")


def rodar_checklist(summary: dict) -> None:
    """Para cada item do CHECKLIST: onde está, uma amostra, ou 'NÃO ENCONTRADO'."""
    for item in CHECKLIST:
        caminhos = buscar_chaves(summary, item)
        com_dado = [(c, v) for c, v in caminhos if not vazio(v)]

        if com_dado:
            print(f"\n[{item.nome}] ENCONTRADO (estruturado)")
            for caminho, valor in com_dado[:MAX_CAMINHOS]:
                print(f"    {caminho}\n        amostra: {amostra(valor)}")
            if len(com_dado) > MAX_CAMINHOS:
                print(f"    ... e mais {len(com_dado) - MAX_CAMINHOS} caminho(s)")
        elif caminhos:
            lista = ", ".join(c for c, _ in caminhos)
            print(f"\n[{item.nome}] CHAVE EXISTE, MAS VAZIA: {lista}")
        else:
            print(f"\n[{item.nome}] NÃO ENCONTRADO como campo estruturado")

        if not com_dado and item.termos_texto:
            mostrar_mencoes_texto(summary, item.termos_texto)


def mostrar_mencoes_texto(summary: dict, termos: tuple[str, ...]) -> None:
    """Mostra se o dado aparece só em texto livre (comentários, notícias...)."""
    mencoes = buscar_mencoes_texto(summary, termos)
    print(f"    Menções em texto livre: {len(mencoes)}")
    for caminho, texto in mencoes[:MAX_MENCOES]:
        print(f"      {caminho}: {texto[:120]!r}")


def mostrar_resumo_legivel(summary: dict) -> None:
    """
    Leitura "humana" dos itens mais úteis. Também serve de exemplo de
    como o ETL vai extrair esses dados mais tarde.
    """
    print("\nResumo legível:")
    for serie in summary.get("seasonseries", []):
        print(f"  H2H: {serie.get('summary')} ({serie.get('totalCompetitions')} jogos)")
        for jogo in serie.get("events", []):
            placar = " x ".join(
                f"{c.get('team', {}).get('abbreviation')} {c.get('score')}"
                for c in jogo.get("competitors", [])
            )
            print(f"       {jogo.get('date', '')[:10]}  {placar}")

    # `lastFiveGames` vem do jogo MAIS ANTIGO para o MAIS RECENTE e mistura
    # competições (Copa do Brasil, Libertadores). Já `header...form` vem na
    # ordem inversa e, em jogo encerrado, inclui o próprio jogo. Preferimos
    # `lastFiveGames`, que tem data e competição de cada resultado.
    for bloco in summary.get("lastFiveGames", []):
        resultados = "".join(e.get("gameResult", "?") for e in bloco.get("events", []))
        ligas = {e.get("leagueName") for e in bloco.get("events", [])}
        nome = bloco.get("team", {}).get("displayName")
        print(f"  Forma {nome} (antigo -> recente): {resultados or '-'}  competições: {ligas}")

    for elenco in summary.get("rosters", []):
        jogadores = elenco.get("roster", [])
        titulares = sum(1 for j in jogadores if j.get("starter"))
        nome = elenco.get("team", {}).get("displayName")
        print(
            f"  Escalação {nome}: formação {elenco.get('formation')}, "
            f"{titulares} titulares / {len(jogadores)} relacionados"
        )

    for odd in summary.get("odds", [])[:1]:
        print(
            f"  Odds ({odd.get('provider', {}).get('name')}): {odd.get('details')} | "
            f"over/under gols {odd.get('overUnder')} | hasOdds={summary.get('hasOdds')}"
        )


def analisar_jogo(espn: EspnClient, evento: dict, rotulo: str) -> dict:
    """Baixa o summary de um evento e imprime todos os relatórios."""
    print("\n" + "=" * 78)
    print(f"{rotulo}: {evento['name']}  (id {evento['id']}, {evento['date'][:10]})")
    print("=" * 78)
    summary = espn.summary(evento["id"])
    listar_chaves_topo(summary)
    rodar_checklist(summary)
    mostrar_resumo_legivel(summary)
    return summary


def escolher_jogos(espn: EspnClient) -> tuple[dict | None, dict | None]:
    """Devolve (último jogo encerrado, próximo jogo agendado). Qualquer um pode ser None."""
    encerrados = espn.jogos_encerrados_desde(DIAS_PARA_TRAS)
    print(f"Jogos encerrados nos últimos {DIAS_PARA_TRAS} dias: {len(encerrados)}")
    for e in encerrados[:5]:
        print(f"  {e['id']}  {e['date'][:10]}  {e['name']}")

    proximos = [
        e for e in espn.scoreboard()
        if e.get("status", {}).get("type", {}).get("state") == "pre"
    ]
    return (encerrados[0] if encerrados else None, proximos[0] if proximos else None)


def mostrar_estatisticas(summary: dict) -> None:
    """Amostra das estatísticas normalizadas (int/float em vez de string)."""
    print("\nestatisticas_partida (normalizado):")
    chaves_amostra = ("wonCorners", "yellowCards", "redCards", "foulsCommitted",
                      "totalShots", "shotsOnTarget", "possessionPct")
    for time_id, stats in normalizar_boxscore(summary).items():
        resumo = {k: stats.get(k) for k in chaves_amostra}
        tipos = {type(v).__name__ for v in resumo.values()}
        print(f"  time {time_id}: {resumo}  tipos={tipos}  ({len(stats)} campos)")


def mostrar_tabela_e_times(espn: EspnClient) -> None:
    """Amostra de standings() e times(), com a contagem (esperado: 20 e 20)."""
    print("\n" + "=" * 78)
    tabela = espn.standings()
    print(f"standings(): {len(tabela)} linhas. Top 5:")
    for linha in tabela[:5]:
        print(
            f"  {linha['rank']:>2}  {linha['nome']:<18} {linha['sigla']:<4} "
            f"P={linha['points']:<3} J={linha['gamesPlayed']:<3} "
            f"V-E-D={linha['wins']}-{linha['ties']}-{linha['losses']} "
            f"SG={linha['pointDifferential']}"
        )
    print(f"  campos de cada linha: {list(tabela[0]) if tabela else '-'}")

    times = espn.times()
    print(f"\ntimes(): {len(times)} times. Amostra:")
    for time in times[:5]:
        print(f"  {time}")


def main() -> int:
    try:
        with EspnClient() as espn:
            encerrado, proximo = escolher_jogos(espn)
            if encerrado:
                summary = analisar_jogo(espn, encerrado, "ÚLTIMO JOGO ENCERRADO")
                mostrar_estatisticas(summary)
            else:
                print("Nenhum jogo encerrado no período.")
            if proximo:
                analisar_jogo(espn, proximo, "PRÓXIMO JOGO (pré-jogo)")
            mostrar_tabela_e_times(espn)
    except EspnError as e:
        print(f"[ERRO ESPN] {e}")
        return 1
    except Exception as e:  # erro inesperado: mostra o tipo para facilitar o debug
        print(f"[ERRO inesperado] {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
