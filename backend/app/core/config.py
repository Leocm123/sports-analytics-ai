"""
Configurações centrais do backend.

Por que pydantic-settings?
- Lê automaticamente as variáveis do arquivo `.env` (ou do ambiente do Render).
- Valida tipos: se LEAGUE_ID não for número, o erro aparece na inicialização,
  e não no meio do ETL.
- Evita espalhar `os.getenv(...)` pelo código: todo mundo importa `settings`.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Caminho absoluto até backend/.env, para funcionar de qualquer pasta
# em que o script for executado.
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # ignora variáveis do .env que não estão declaradas aqui
    )

    # --- API-Football ---
    apisports_key: str = ""  # vazio por padrão; o cliente avisa se faltar
    apisports_base_url: str = "https://v3.football.api-sports.io"
    league_id: int = 71  # Brasileirão Série A
    season: int = 2026

    # --- Banco (Neon) ---
    database_url: str = ""

    # --- LLM (Fase 3) ---
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"


@lru_cache  # cria o objeto uma única vez e reaproveita
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
