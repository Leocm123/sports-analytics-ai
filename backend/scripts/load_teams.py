"""
Carga dos times da Série A (fonte: ESPN) na tabela `times`.

Como rodar (a partir da pasta backend/):
    python -m scripts.init_db      # uma vez, para criar as tabelas
    python -m scripts.load_teams

É idempotente: usa INSERT ... ON CONFLICT (id) DO UPDATE (o famoso "upsert").
- Time novo     -> é inserido.
- Time que já existe -> nome, sigla e escudo são atualizados.
Rodar duas vezes deixa o banco exatamente igual.
"""

import sys

from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import get_engine
from app.services.espn import EspnClient, EspnError

TOTAL_ESPERADO = 20  # a Série A tem 20 clubes

# Os valores entram por PARÂMETROS (:id, :nome...) e nunca montados com
# f-string. Assim o driver cuida das aspas ("Grêmio", "Sport Club d'Ávila"...)
# e fica impossível injetar SQL por um nome mal-intencionado.
# EXCLUDED é a linha que TENTAMOS inserir; usamos os valores dela para
# atualizar a linha que já existia.
SQL_UPSERT_TIME = text(
    """
    INSERT INTO times (id, nome, sigla, escudo_url)
    VALUES (:id, :nome, :sigla, :escudo_url)
    ON CONFLICT (id) DO UPDATE SET
        nome       = EXCLUDED.nome,
        sigla      = EXCLUDED.sigla,
        escudo_url = EXCLUDED.escudo_url
    """
)


def buscar_times() -> list[dict]:
    """Times da ESPN, já no formato das colunas: {id, nome, sigla, escudo_url}."""
    with EspnClient() as espn:
        return espn.times()


def gravar_times(conn: Connection, times: list[dict]) -> None:
    """
    Faz o upsert de todos os times.
    Passar uma LISTA de dicts faz o SQLAlchemy usar executemany: o mesmo
    comando roda uma vez para cada time, dentro da mesma transação.
    """
    conn.execute(SQL_UPSERT_TIME, times)


def contar_times(conn: Connection) -> int:
    return conn.execute(text("SELECT COUNT(*) FROM times")).scalar_one()


def main() -> int:
    try:
        times = buscar_times()
        print(f"ESPN devolveu {len(times)} times.")
        if not times:
            print("[ERRO] Nenhum time retornado. Nada foi gravado.")
            return 1

        with get_engine().begin() as conn:  # COMMIT no fim, ROLLBACK se der erro
            gravar_times(conn, times)
            total = contar_times(conn)
    except EspnError as e:
        print(f"[ERRO ESPN] {e}")
        return 1
    except (SQLAlchemyError, RuntimeError) as e:
        print(f"[ERRO de banco] {type(e).__name__}: {e}")
        return 1

    print(f"SELECT COUNT(*) FROM times = {total}")
    if total != TOTAL_ESPERADO:
        # Não é necessariamente erro: nunca apagamos times (as partidas antigas
        # apontam para eles). Na virada de temporada, os rebaixados continuam
        # na tabela e o total passa de 20.
        print(f"[AVISO] Esperado {TOTAL_ESPERADO}. Confira se houve troca de temporada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
