-- =====================================================================
-- 001_init.sql — Esquema inicial do Sports Analytics AI
--
-- Como rodar (a partir de backend/):  python -m scripts.init_db
--
-- IDEMPOTENTE: pode rodar quantas vezes quiser. Tudo usa IF NOT EXISTS,
-- então uma segunda execução não apaga nem duplica nada.
--
-- Atenção: IF NOT EXISTS NÃO altera uma tabela que já existe. Se mudarmos
-- o esquema no futuro, a mudança vai num arquivo novo (002_..., 003_...)
-- com ALTER TABLE, e este arquivo fica como está.
-- =====================================================================

-- pgvector: adiciona o tipo "vector" para guardar embeddings e buscar
-- notícias por similaridade direto no SQL.
CREATE EXTENSION IF NOT EXISTS vector;


-- ---------------------------------------------------------------------
-- times: os clubes da Série A
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS times (
    -- ID da ESPN (team.id, ex.: 819 = Flamengo). A ESPN manda como texto
    -- ("819") e o ETL converte para inteiro. NÃO é o ID da API-Football,
    -- que fica só para o histórico de 2024.
    id          INTEGER PRIMARY KEY,
    nome        VARCHAR(100) NOT NULL,  -- nome curto: "Athletico-PR"
    sigla       VARCHAR(10),            -- "CAP"
    escudo_url  TEXT
);


-- ---------------------------------------------------------------------
-- partidas: um jogo da Série A (agendado, ao vivo ou encerrado)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partidas (
    -- ID do evento na ESPN (event.id, ex.: 401841302). Cabe em INTEGER,
    -- que vai até ~2,1 bilhões.
    id                 INTEGER PRIMARY KEY,
    rodada             VARCHAR(50),
    data_hora          TIMESTAMPTZ NOT NULL,
    -- Status no formato da ESPN (status.type.name): "STATUS_SCHEDULED",
    -- "STATUS_FULL_TIME"... Por isso VARCHAR(40), e não os 20 do rascunho
    -- original (que esperava códigos curtos da API-Football, como "FT").
    status             VARCHAR(40),
    time_casa_id       INTEGER REFERENCES times(id),
    time_fora_id       INTEGER REFERENCES times(id),
    gols_casa          INTEGER,
    gols_fora          INTEGER,
    -- Saída de EspnClient.estatisticas_partida: {time_id: {"wonCorners": 9, ...}}.
    -- Lembrete: em JSON as chaves são sempre texto, então o time_id vira "3457".
    estatisticas_json  JSONB,
    analise_ia_json    JSONB,  -- cache do relatório gerado pelo Gemini (Fase 3)
    -- TIMESTAMPTZ (com fuso), igual a data_hora. Misturar colunas com e sem
    -- fuso causa erros de horas ao comparar datas.
    atualizado_em      TIMESTAMPTZ DEFAULT NOW()

    -- Por que não há coluna de árbitro?
    -- A exploração da ESPN (scripts/explore_espn_summary.py, 12/11/2026)
    -- mostrou que o summary NÃO tem `gameInfo.officials`, nem antes nem
    -- depois do jogo: o nome do árbitro só aparece em texto de notícias.
    -- Quando o RAG trouxer esse dado, ele entra numa migração nova (002_...).
);


-- ---------------------------------------------------------------------
-- noticias: pedaços (chunks) de matérias + embedding, para o RAG
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS noticias (
    id              SERIAL PRIMARY KEY,
    time_id         INTEGER REFERENCES times(id),
    portal          VARCHAR(50) NOT NULL,  -- 'GE', 'UOL', 'TNT'
    titulo          TEXT NOT NULL,
    url             TEXT NOT NULL,
    -- Uma matéria vira vários pedaços (chunks). chunk_index é a posição do
    -- pedaço dentro da matéria: 0, 1, 2...
    chunk_index     INTEGER NOT NULL,
    conteudo_chunk  TEXT NOT NULL,
    publicado_em    TIMESTAMPTZ,
    -- 384 dimensões = intfloat/multilingual-e5-small (ver CLAUDE.md).
    embedding       vector(384),

    -- A mesma URL pode aparecer várias vezes (um registro por chunk), mas o
    -- par (url, chunk_index) é único. É nesse par que o ETL faz o upsert:
    -- INSERT ... ON CONFLICT (url, chunk_index) DO UPDATE.
    CONSTRAINT noticias_url_chunk_uniq UNIQUE (url, chunk_index)
);

-- Índice HNSW: deixa a busca por similaridade de cosseno rápida.
-- O NOME é obrigatório para o IF NOT EXISTS funcionar. Sem nome, o Postgres
-- inventaria um nome novo a cada execução e criaria índices duplicados.
CREATE INDEX IF NOT EXISTS noticias_embedding_hnsw_idx
    ON noticias USING hnsw (embedding vector_cosine_ops);
