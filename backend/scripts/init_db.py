"""
Cria ou atualiza o esquema do banco no Neon executando os arquivos sql/*.sql.

Como rodar (a partir da pasta backend/):
    python -m scripts.init_db

Os arquivos rodam em ORDEM ALFABÉTICA (001_init.sql, 002_views.sql, ...).
Por isso eles começam com um número: a ordem importa, porque as views do 002
dependem das tabelas do 001.

É seguro rodar mais de uma vez: os arquivos usam IF NOT EXISTS e
CREATE OR REPLACE, então a segunda execução não muda nada (idempotente).
"""

import sys
from pathlib import Path

from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import BACKEND_DIR
from app.core.db import get_engine

PASTA_SQL = BACKEND_DIR / "sql"


def arquivos_sql(pasta: Path) -> list[Path]:
    """Todos os .sql da pasta, em ordem alfabética (= ordem de execução)."""
    return sorted(pasta.glob("*.sql"))


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


def listar_objetos(conn: Connection, tipo: str) -> list[str]:
    """
    Tabelas (tipo='BASE TABLE') ou views (tipo='VIEW') do schema public,
    para conferir o resultado.
    """
    resultado = conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = :tipo "
            "ORDER BY table_name"
        ),
        {"tipo": tipo},
    )
    return list(resultado.scalars())


def versao_pgvector(conn: Connection) -> str | None:
    """Versão da extensão vector instalada (None se não estiver instalada)."""
    return conn.execute(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    ).scalar_one_or_none()


def aplicar_arquivo(caminho: Path) -> None:
    """
    Executa UM arquivo numa transação própria.

    Uma transação por arquivo: se o 002 tiver um erro, o 001 continua
    aplicado (e, como tudo é idempotente, basta corrigir e rodar de novo).
    """
    print(f"Executando {caminho.relative_to(BACKEND_DIR)} ...")
    # begin(): abre uma transação e faz COMMIT no fim do bloco
    # (ou ROLLBACK, se der erro no meio).
    with get_engine().begin() as conn:
        executar_script_sql(conn, ler_sql(caminho))


def main() -> int:
    arquivos = arquivos_sql(PASTA_SQL)
    if not arquivos:
        print(f"[ERRO] Nenhum arquivo .sql em {PASTA_SQL}")
        return 1

    try:
        for caminho in arquivos:
            aplicar_arquivo(caminho)
        with get_engine().connect() as conn:
            tabelas = listar_objetos(conn, "BASE TABLE")
            views = listar_objetos(conn, "VIEW")
            versao = versao_pgvector(conn)
    except (SQLAlchemyError, RuntimeError) as e:
        print(f"[ERRO de banco] {type(e).__name__}: {e}")
        return 1
    except Exception as e:  # erro do psycopg ou inesperado
        print(f"[ERRO inesperado] {type(e).__name__}: {e}")
        return 1

    print(f"OK. pgvector versão: {versao}")
    print(f"Tabelas: {', '.join(tabelas) or '(nenhuma)'}")
    print(f"Views:   {', '.join(views) or '(nenhuma)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
