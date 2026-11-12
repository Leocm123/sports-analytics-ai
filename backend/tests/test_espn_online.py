"""
Testes ONLINE: chamam a ESPN de verdade.

Rodar a partir de backend/:  pytest -m online
(o `pytest` normal pula estes testes; veja o pytest.ini)

Por que existem?
Os testes com fixtures usam respostas CONGELADAS, então continuam passando
mesmo que a ESPN mude o formato hoje à noite. Estes baixam respostas atuais
e passam pelos mesmos normalizadores.

Se ESTES quebrarem e os offline passarem, a ESPN mudou o formato: ajuste o
espn.py e atualize as fixtures (veja tests/conftest.py).
"""

from collections.abc import Iterator

import pytest

from app.services.espn import EspnClient, normalizar_boxscore, normalizar_evento

pytestmark = pytest.mark.online  # marca todos os testes deste arquivo


@pytest.fixture(scope="module")
def espn() -> Iterator[EspnClient]:
    with EspnClient() as cliente:
        yield cliente


@pytest.fixture(scope="module")
def eventos_primeiro_mes(espn: EspnClient) -> list[dict]:
    """Jogos do 1º mês da temporada atual (já encerrados, com estatísticas)."""
    temporada = espn.temporada()
    return espn.eventos_do_mes(temporada.inicio.year, temporada.inicio.month)


def test_temporada_tem_inicio_antes_do_fim(espn: EspnClient) -> None:
    temporada = espn.temporada()
    assert temporada.inicio < temporada.fim
    assert temporada.inicio.year == temporada.ano


def test_eventos_atuais_normalizam(eventos_primeiro_mes: list[dict]) -> None:
    assert eventos_primeiro_mes, "a ESPN não devolveu jogos no 1º mês da temporada"
    for evento in eventos_primeiro_mes:
        partida = normalizar_evento(evento)  # não pode levantar EspnError
        assert isinstance(partida["id"], int)
        assert partida["data_hora"].tzinfo is not None


def test_summary_atual_tem_estatisticas(espn: EspnClient, eventos_primeiro_mes: list[dict]) -> None:
    encerrado = next(
        e for e in eventos_primeiro_mes if e["status"]["type"].get("completed")
    )
    stats = normalizar_boxscore(espn.summary(encerrado["id"]))

    assert len(stats) == 2
    for estatisticas in stats.values():
        # Os campos que as views (sql/002_views.sql) leem do JSON
        for campo in ("wonCorners", "yellowCards", "redCards", "foulsCommitted",
                      "totalShots", "shotsOnTarget", "blockedShots", "possessionPct"):
            assert campo in estatisticas, f"a ESPN não mandou '{campo}'"
