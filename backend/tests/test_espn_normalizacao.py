"""
Testes dos normalizadores da ESPN contra respostas reais congeladas.

Rodar a partir de backend/:  pytest

Por que testar os normalizadores?
Eles são o "contrato" entre a ESPN e o resto do projeto: o ETL só enxerga o
que eles devolvem. Se alguém mexer no espn.py e quebrar o formato (ou se a
fixture for atualizada e a ESPN tiver mudado algo), estes testes avisam.
"""

from datetime import datetime, timezone

import pytest

from app.services.espn import (
    EspnError,
    _converter_valor,
    normalizar_boxscore,
    normalizar_evento,
)

# Exatamente as colunas da tabela `partidas` que o ETL grava.
COLUNAS_PARTIDAS = {
    "id", "rodada", "data_hora", "status",
    "time_casa_id", "time_fora_id", "gols_casa", "gols_fora",
}

# IDs da ESPN usados nas fixtures
GREMIO_X_ATLETICO_MG = "401841005"  # encerrado: Grêmio 2 x 1 Atlético-MG
BAHIA_X_CHAPECOENSE = "401840998"  # ADIADO
VITORIA, FLUMINENSE = 3457, 3445  # times do summary_401841302.json


def buscar_evento(scoreboard: dict, event_id: str) -> dict:
    return next(e for e in scoreboard["events"] if e["id"] == event_id)


# ----------------------------------------------------------------------
# normalizar_evento
# ----------------------------------------------------------------------
class TestNormalizarEvento:
    def test_devolve_exatamente_as_colunas_de_partidas(self, scoreboard: dict) -> None:
        for evento in scoreboard["events"]:
            assert set(normalizar_evento(evento)) == COLUNAS_PARTIDAS

    def test_jogo_encerrado(self, scoreboard: dict) -> None:
        partida = normalizar_evento(buscar_evento(scoreboard, GREMIO_X_ATLETICO_MG))

        assert partida == {
            "id": 401841005,
            "rodada": None,  # a ESPN não informa; a view v_partidas deriva
            # 25/02 21:30 em Brasília = 26/02 00:30 em UTC
            "data_hora": datetime(2026, 2, 26, 0, 30, tzinfo=timezone.utc),
            "status": "STATUS_FULL_TIME",
            "time_casa_id": 6273,  # Grêmio
            "time_fora_id": 7632,  # Atlético-MG
            "gols_casa": 2,
            "gols_fora": 1,
        }

    def test_ids_e_gols_sao_int(self, scoreboard: dict) -> None:
        """A ESPN manda tudo como texto ("6273", "2"); o banco espera inteiros."""
        partida = normalizar_evento(buscar_evento(scoreboard, GREMIO_X_ATLETICO_MG))
        for coluna in ("id", "time_casa_id", "time_fora_id", "gols_casa", "gols_fora"):
            assert type(partida[coluna]) is int, coluna

    def test_jogo_adiado_nao_tem_placar(self, scoreboard: dict) -> None:
        """O adiado vem com score "0" e state "post", mas o placar é FALSO."""
        evento = buscar_evento(scoreboard, BAHIA_X_CHAPECOENSE)
        assert evento["competitions"][0]["competitors"][0]["score"] == "0"  # o "0" falso

        partida = normalizar_evento(evento)

        assert partida["status"] == "STATUS_POSTPONED"
        assert partida["gols_casa"] is None
        assert partida["gols_fora"] is None

    def test_jogo_agendado_nao_tem_placar(self, scoreboard: dict) -> None:
        """Evento real com o status trocado para "não começou"."""
        evento = buscar_evento(scoreboard, GREMIO_X_ATLETICO_MG)
        evento["status"]["type"] = {"name": "STATUS_SCHEDULED", "state": "pre", "completed": False}

        partida = normalizar_evento(evento)

        assert partida["status"] == "STATUS_SCHEDULED"
        assert partida["gols_casa"] is None
        assert partida["gols_fora"] is None

    def test_jogo_em_andamento_tem_placar_parcial(self, scoreboard: dict) -> None:
        """Evento real com o status trocado para "rolando" (state "in")."""
        evento = buscar_evento(scoreboard, GREMIO_X_ATLETICO_MG)
        evento["status"]["type"] = {"name": "STATUS_SECOND_HALF", "state": "in", "completed": False}

        partida = normalizar_evento(evento)

        assert (partida["gols_casa"], partida["gols_fora"]) == (2, 1)

    def test_formato_inesperado_vira_espn_error(self, scoreboard: dict) -> None:
        evento = buscar_evento(scoreboard, GREMIO_X_ATLETICO_MG)
        del evento["competitions"]

        with pytest.raises(EspnError, match=GREMIO_X_ATLETICO_MG):
            normalizar_evento(evento)

    def test_sem_time_da_casa_vira_espn_error(self, scoreboard: dict) -> None:
        evento = buscar_evento(scoreboard, GREMIO_X_ATLETICO_MG)
        for competidor in evento["competitions"][0]["competitors"]:
            competidor["homeAway"] = "away"

        with pytest.raises(EspnError):
            normalizar_evento(evento)


# ----------------------------------------------------------------------
# normalizar_boxscore
# ----------------------------------------------------------------------
class TestNormalizarBoxscore:
    def test_estatisticas_dos_dois_times(self, summary: dict) -> None:
        stats = normalizar_boxscore(summary)

        assert set(stats) == {VITORIA, FLUMINENSE}  # chaves int, não texto
        assert stats[VITORIA]["wonCorners"] == 9
        assert stats[FLUMINENSE]["wonCorners"] == 6
        assert stats[VITORIA]["yellowCards"] == 2
        assert stats[FLUMINENSE]["shotsOnTarget"] == 7

    def test_posse_e_float_de_0_a_100(self, summary: dict) -> None:
        stats = normalizar_boxscore(summary)

        assert stats[VITORIA]["possessionPct"] == pytest.approx(44.4)
        assert type(stats[VITORIA]["possessionPct"]) is float

    def test_todos_os_valores_sao_numero_ou_none(self, summary: dict) -> None:
        for estatisticas in normalizar_boxscore(summary).values():
            assert len(estatisticas) > 20  # a ESPN manda ~28 campos
            for nome, valor in estatisticas.items():
                assert valor is None or isinstance(valor, (int, float)), nome

    def test_pre_jogo_devolve_vazio(self, summary: dict) -> None:
        """Antes do jogo o boxscore traz totais da TEMPORADA: não podem virar 'da partida'."""
        summary["header"]["competitions"][0]["status"]["type"]["state"] = "pre"
        assert normalizar_boxscore(summary) == {}

    def test_sem_boxscore_devolve_vazio(self, summary: dict) -> None:
        del summary["boxscore"]
        assert normalizar_boxscore(summary) == {}


# ----------------------------------------------------------------------
# _converter_valor (displayValue -> número)
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("9", 9),
        (" 7 ", 7),
        ("+42", 42),
        ("44.4", 44.4),
        ("0.5", 0.5),
        ("", None),
        ("-", None),
        (None, None),
        ("23-7-4", None),
    ],
)
def test_converter_valor(texto: str | None, esperado: int | float | None) -> None:
    resultado = _converter_valor(texto)
    assert resultado == esperado
    assert type(resultado) is type(esperado)  # 9 tem que ser int, não 9.0
