"""
Cria o esquema do banco no Neon executando sql/001_init.sql.

Como rodar (a partir da pasta backend/):
    python -m scripts.init_db

É seguro rodar mais de uma vez: o SQL usa IF NOT EXISTS em tudo, então a
segunda execução não muda nada (é isso que chamamos de idempotente).
"""

import sys
from pathlib import Path

from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import BACKEND_DIR
from app.core.db import get_engine

ARQUIVO_SQL = BACKEND_DIR / "sql" / "001_init.sql"


def ler_sql(caminho: Path) -> str:
    """Lê o arquivo .sql inteiro como texto."""
    return caminho.read_text(encoding="utf-8")


def executar_script_sql(conn: Connection, sql: str) -> None:
    """
    Executa um arquivo com VÁRIOS comandos SQL separados por ';'.

    Por que usar a conexão "crua" do driver (psycopg) e não `conn.execute(text(...))`?
    O psycopg 3 só aceita vários comandos num único execute() quando NÃO há
    parâmetros. Pelo `text()` do SQLAlchemy, o comando passa pelo
    tratamento de parâmetros e o Postgres recusa ("cannot insert multiple
    commands into a prepared statement"). Chamando o driver direto e sem
    parâmetros, o arquivo vai inteiro de uma vez.

    A conexão é a mesma do `engine.begin()`, então tudo continua dentro da
    mesma transação: se um comando falhar, nada do arquivo é aplicado.
    """
    conn.connection.driver_connection.execute(sql)


def listar_tabelas(conn: Connection) -> list[str]:
    """Tabelas do schema public, para conferir o resultado."""
    resultado = conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
    )
    return list(resultado.scalars())


def versao_pgvector(conn: Connection) -> str | None:
    """Versão da extensão vector instalada (None se não estiver instalada)."""
    return conn.execute(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    ).scalar_one_or_none()


def main() -> int:
    print(f"Executando {ARQUIVO_SQL.relative_to(BACKEND_DIR)} ...")
    try:
        sql = ler_sql(ARQUIVO_SQL)
        # begin(): abre uma transação e faz COMMIT no fim do bloco
        # (ou ROLLBACK, se der erro no meio).
        with get_engine().begin() as conn:
            executar_script_sql(conn, sql)
            tabelas = listar_tabelas(conn)
            versao = versao_pgvector(conn)
    except FileNotFoundError:
        print(f"[ERRO] Arquivo não encontrado: {ARQUIVO_SQL}")
        return 1
    except (SQLAlchemyError, RuntimeError) as e:
        print(f"[ERRO de banco] {type(e).__name__}: {e}")
        return 1
    except Exception as e:  # erro do psycopg ou inesperado
        print(f"[ERRO inesperado] {type(e).__name__}: {e}")
        return 1

    print(f"OK. pgvector versão: {versao}")
    print(f"Tabelas no schema public: {', '.join(tabelas) or '(nenhuma)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
