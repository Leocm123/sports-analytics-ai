"""
Fixtures compartilhadas pelos testes (o pytest carrega este arquivo sozinho).

Os JSONs em tests/fixtures/ são respostas REAIS da ESPN, capturadas em
12/11/2026 e salvas com json.dumps(..., ensure_ascii=False, indent=1):

- scoreboard_20260225.json  <- GET {base}/site/v2/sports/soccer/bra.1/scoreboard?dates=20260225
    6 jogos encerrados + 3 ADIADOS. Repare que a consulta do dia 25/02 traz
    jogos de 26/02 00:30 em UTC: a ESPN define o "dia" pelo horário local.
- summary_401841302.json    <- GET {base}/site/v2/sports/soccer/bra.1/summary?event=401841302
    Vitória 0 x 3 Fluminense (06/11/2026). Escanteios 9 x 6, posse 44.4 x 55.6.

Por que respostas congeladas?
Os testes ficam rápidos, rodam sem internet e dão sempre o mesmo resultado.
A contrapartida: eles NÃO percebem se a ESPN mudar o formato amanhã. Quem
percebe é tests/test_espn_online.py (pytest -m online).

Para atualizar: baixe de novo a mesma URL, salve por cima no mesmo formato e,
se trocar de jogo, ajuste os valores esperados nos testes.
"""

import json
from pathlib import Path

import pytest

PASTA_FIXTURES = Path(__file__).parent / "fixtures"


def carregar_json(nome: str) -> dict:
    return json.loads((PASTA_FIXTURES / nome).read_text(encoding="utf-8"))


# As fixtures do pytest abaixo leem o arquivo a cada teste. Assim, um teste
# que altera o dict (ex.: troca o status para simular um jogo ao vivo) não
# contamina os outros.
@pytest.fixture
def scoreboard() -> dict:
    return carregar_json("scoreboard_20260225.json")


@pytest.fixture
def summary() -> dict:
    return carregar_json("summary_401841302.json")
