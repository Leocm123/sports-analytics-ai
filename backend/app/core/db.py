"""
Conexão com o banco (Neon / PostgreSQL + pgvector) via SQLAlchemy 2.0.

O que é o "engine"?
É o objeto que guarda um POOL de conexões abertas com o banco. Abrir uma
conexão com o Neon (TLS + autenticação, pela internet) é lento, então o pool
reaproveita conexões já abertas. Por isso existe UM engine para o programa
inteiro: todo mundo chama `get_engine()`.

Por que criar sob demanda (e não direto ao importar o módulo)?
Se o DATABASE_URL estiver vazio, `create_engine("")` quebraria só de importar
este arquivo, inclusive em scripts que nem usam o banco. Com `get_engine()`,
o erro aparece só quando alguém realmente precisa do banco, e com uma
mensagem clara.

Por que pool_pre_ping e pool_recycle?
O Neon "dorme" (scale to zero) depois de alguns minutos sem uso e derruba as
conexões abertas. Sem tratamento, a próxima consulta usaria uma conexão morta
e falharia com "server closed the connection unexpectedly".
- pool_pre_ping=True: antes de entregar uma conexão do pool, o SQLAlchemy
  testa se ela ainda está viva e, se não estiver, abre outra.
- pool_recycle=300: conexões com mais de 5 minutos são descartadas e
  reabertas, antes que o Neon as derrube.

Uso:
    from sqlalchemy import text
    from app.core.db import get_engine

    with get_engine().begin() as conn:  # begin() = transação com commit automático
        conn.execute(text("SELECT 1"))
"""

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from app.core.config import settings


@lru_cache  # cria o engine uma única vez e reaproveita (igual ao get_settings)
def get_engine() -> Engine:
    """Devolve o engine do Neon. O driver (psycopg) já vem do config.py."""
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL não definida. Preencha no backend/.env com a "
            "connection string do Neon (Dashboard -> Connect)."
        )
    return create_engine(
        settings.database_url,  # o config.py já trocou para "postgresql+psycopg://"
        pool_pre_ping=True,
        pool_recycle=300,
    )
