# CONTEXTO DO PROJETO: Sports Analytics AI (Brasileirão Série A)

## 1. Visão Geral e Objetivo
O **Sports Analytics AI** é uma plataforma de inteligência esportiva e análise preditiva voltada para torcedores e apostadores da **Série A do Campeonato Brasileiro**. O projeto tem dupla finalidade:
1. Entregar um dashboard interativo que cruza **dados estatísticos quantitativos** (escanteios, cartões, chutes, histórico H2H) com **contexto jornalístico qualitativo** (desfalques, lesões, clima de bastidores, troca de técnicos) processado via RAG e LLM.
2. Servir como projeto de portfólio de nível profissional demonstrando engenharia de dados, arquitetura RAG, integração com LLMs e desenvolvimento full-stack sob **infraestrutura 100% gratuita (Custo Zero)**.

### Dinâmica da Equipe
- **Líder Técnico / Arquitetura & IA:** Responsável pela arquitetura geral, banco vetorial (`pgvector`), pipeline RAG, engenharia de prompts e dashboard frontend.
- **Desenvolvedor Júnior (Parceiro em Mentoria):** Primeiro projeto prático; atuará na ingestão de dados, scrapers básicos em Python e modelagem relacional inicial. *Nota para a IA: Sempre que gerar código para o backend/scraping, mantenha-o modular, bem comentado e didático.*

---

## 2. Stack Tecnológica & Infraestrutura (100% Free Tier)

| Camada | Tecnologia | Hospedagem / Provedor | Justificativa & Limites |
| :--- | :--- | :--- | :--- |
| **Backend & ETL** | Python 3.11+, FastAPI, `httpx`, `BeautifulSoup4`, `feedparser` | **Render** (Free Web Service) | Ecossistema ideal para dados/IA. Hiberna após 15 min de inatividade (aceitável para portfólio). |
| **Banco Relacional + Vetorial** | PostgreSQL 16 + extensão `pgvector` | **Neon DB** (Free Tier) | Unifica dados estruturados de partidas e embeddings vetoriais no mesmo banco via SQL. |
| **Embeddings** | `sentence-transformers` — preferir `intfloat/multilingual-e5-small` (384 dims, suporta PT-BR); `all-MiniLM-L6-v2` é treinado majoritariamente em inglês | Execução local / Worker Python | Custo zero, sem consumo de cota de API externa para vetorização. |
| **Motor de IA (LLM)** | Google Gemini API — modelo configurável via `GEMINI_MODEL`, padrão **`gemini-3.8-flash`** (fixo, não usar `-latest`). `gemini-2.5-flash` retorna 404 para contas novas (validado 12/11/2026); fallback: `gemini-3.5-flash-lite` (NÃO existe `gemini-3.8-flash-lite` de texto). Cliente da Fase 3 deve tratar 503, 429 e timeout com retry + backoff e cair para o fallback. Google recomenda a "Interactions API" — avaliar na Fase 3. Fallback de provedor: Groq (Llama) | **Google AI Studio** (Free Tier) | Limites do free tier mudam com frequência — conferir no AI Studio. Suporta JSON estruturado. |
| **Frontend** | React / Next.js (App Router), Tailwind CSS, Recharts/Lucide | **Vercel** (Hobby / Free Tier) | Deploy contínuo via GitHub com CDN global gratuita. |

---

## 3. Especificação das Fontes de Dados

### A0. DECISÃO (12/11/2026): Fonte principal da temporada atual = API pública da ESPN
- **Motivo:** o plano Free da API-Football só libera as temporadas 2022–2024 (`"Free plans do not have access to this season, try from 2022 to 2024."`).
- **Base:** `https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1` — sem chave, sem cota fixa. Não oficial: isolar em `services/espn.py` atrás de uma interface de provedor, para poder trocar de fonte.
- **Endpoints validados:**
  - `GET /scoreboard` (jogos dos próximos dias) e `GET /scoreboard?dates=YYYYMMDD` (uma data só; **intervalo `A-B` retorna 400**)
  - `GET /summary?event={id}` → `boxscore.teams[].statistics[]` (`name`/`displayValue`)
  - `GET /teams` → 20 times (`id`, `shortDisplayName`, `abbreviation`, `logos[]`)
  - `GET https://site.api.espn.com/apis/v2/sports/soccer/bra.1/standings` → tabela (**prefixo `/apis/v2`, não `/apis/site/v2`**); `stats[]` com `value` numérico
  - Erros vêm com HTTP correto e motivo no corpo (404 evento inexistente, 400 parâmetro inválido)
- **Campos confirmados (jogo 401841302, Vitória x Fluminense, 06/11/2026):** `wonCorners`, `yellowCards`, `redCards`, `foulsCommitted`, `totalShots`, `shotsOnTarget`, `blockedShots`, `possessionPct`, `offsides`, `saves`, `totalCrosses`, `accurateCrosses`, passes, desarmes, interceptações, cortes.
- **IDs:** times e eventos passam a usar os IDs da ESPN (não os da API-Football).
- **Mapeado em 12/11/2026** (`scripts/explore_espn_summary.py`, jogos encerrados e pré-jogo):
  - **TEM (estruturado):** H2H em `seasonseries[0]` (últimos 5 confrontos, várias temporadas); forma em `lastFiveGames[].events[]` (`gameResult` W/D/L, do **mais antigo para o mais recente**, mistura Copa do Brasil/Libertadores — filtrar por `leagueName` se quiser só Série A); escalações em `rosters[]` (`formation`, `starter`; vazio antes da divulgação); odds DraftKings em `odds[]`/`pickcenter[]` (formato americano, `overUnder` de gols); lances (gols, cartões, substituições) em `keyEvents[]`.
  - **NÃO TEM → vem do RAG de notícias:** árbitro (`gameInfo.officials` ausente em todos os jogos testados), lesões/suspensões (só texto livre em `commentary`/`news`), pendurados, clima, troca de técnico.
  - **Pegadinhas:** boxscore só tem `displayValue` (string); `possessionPct` vai de 0–100 e `shotPct`/`passPct` de 0–1; no pré-jogo o boxscore traz totais da **temporada** (o `EspnClient` devolve `{}`); `header...form` vem invertido e inclui o próprio jogo quando encerrado, então usar `lastFiveGames`; `displayName` muda entre endpoints ("Athletico-PR" × "Athletico Paranaense"), então usar `shortDisplayName`.
- **API-Football (2024):** mantida apenas como fonte de histórico/backtest (`SEASON=2024`).

### A. Dados Estruturados (histórico): API-Football v3 (Direto via API-Sports)
- **Base URL:** `https://v3.football.api-sports.io/`
- **Autenticação (Header obrigatório):** `{"x-apisports-key": os.getenv("APISPORTS_KEY")}`
- **Plano:** Free Tier direto no dashboard (`dashboard.api-football.com`) — **100 requisições/dia** (reset 00:00 UTC) e limite por minuto. Atenção: o free tier pode restringir o intervalo de temporadas acessíveis — validar `season=2026` com o script de teste.
- **IDs de Referência:**
  - **League ID (Brasileirão Série A):** `71`
  - **Season:** `2026`
- **Endpoints Principais a Utilizar:**
  - `GET /fixtures?league=71&season=2026&next=10` (Próximos jogos da rodada)
  - `GET /fixtures/statistics?fixture={fixture_id}` (Escanteios, chutes no gol, posse, faltas, cartões de jogos encerrados)
  - `GET /fixtures/headtohead?h2h={team1_id}-{team2_id}` (Histórico de confrontos diretos)
  - `GET /standings?league=71&season=2026` (Tabela de classificação e forma recente)
  - `GET /injuries?league=71&season=2026` (Lesões e suspensões oficiais)
- **Regra Crítica de Cache / Economia de Cota:** Nunca consultar a API-Football em tempo real a partir do frontend. O pipeline ETL roda **2x por rodada** (24h antes da rodada para pré-jogo e após o encerramento da rodada para pós-jogo) e persiste tudo no Neon DB.

### B. Dados Não Estruturados: Scraping de Notícias (RAG)
- **Fontes Alvo:** GE (Globo Esporte), UOL Esporte e TNT Sports Brasil.
- **Método:** Leitura via Feeds RSS públicos (`feedparser`) + extração limpa do corpo da matéria (`BeautifulSoup4`) filtrando pelos 20 clubes da Série A.
- **Pipeline RAG:** Limpeza de HTML -> Chunking por parágrafos relevantes (foco em escalação, desfalques, desgaste físico, arbitragem e rivalidade) -> Geração de embeddings -> Upsert na tabela `noticias` do Neon DB com metadados (`clube`, `data_publicacao`, `fonte`, `url`).

---

## 4. Requisitos Funcionais & Mercados de Apostas Cobertos

O dashboard e o relatório da IA devem cruzar estatísticas e notícias para cobrir os 4 principais mercados esportivos:

1. **Resultado Final (1X2 / Match Odds):** Aproveitamento casa/fora, H2H, troca de técnico, desgaste de viagens/copas.
2. **Escanteios (Over/Under, Handicap):** Média de cantos a favor/contra, chutes bloqueados, esquema tático (jogo pelos pontas) e condições climáticas.
3. **Cartões (Amarelos/Vermelhos):** Média de faltas cometidas/sofridas, rigor do árbitro escalado, clássicos regionais e jogadores pendurados.
4. **Gols & Ambas Marcam (Over/Under Gols, BTTS):** Finalizações no alvo, gols marcados/sofridos nos últimos 5 jogos e desfalques no setor defensivo ou ofensivo.

---

## 5. Modelagem Inicial do Banco de Dados (PostgreSQL + PGVector)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE times (
    id INTEGER PRIMARY KEY, -- ID oficial da API-Football
    nome VARCHAR(100) NOT NULL,
    sigla VARCHAR(10),
    escudo_url TEXT
);

CREATE TABLE partidas (
    id INTEGER PRIMARY KEY, -- Fixture ID oficial da API-Football
    rodada VARCHAR(50),
    data_hora TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(20), -- 'NS' (Not Started), 'FT' (Finished), etc.
    time_casa_id INTEGER REFERENCES times(id),
    time_fora_id INTEGER REFERENCES times(id),
    gols_casa INTEGER,
    gols_fora INTEGER,
    estatisticas_json JSONB, -- Armazena escanteios, cartões, chutes, posse
    analise_ia_json JSONB,   -- Cache do relatório gerado pelo Gemini
    atualizado_em TIMESTAMP DEFAULT NOW()
);

CREATE TABLE noticias (
    id SERIAL PRIMARY KEY,
    time_id INTEGER REFERENCES times(id),
    portal VARCHAR(50) NOT NULL, -- 'GE', 'UOL', 'TNT'
    titulo TEXT NOT NULL,
    conteudo_chunk TEXT NOT NULL,
    url TEXT UNIQUE NOT NULL,
    publicado_em TIMESTAMP WITH TIME ZONE,
    embedding vector(384) -- Dimensão compatível com all-MiniLM-L6-v2 / multilingual-e5-small
);

CREATE INDEX ON noticias USING hnsw (embedding vector_cosine_ops);
```

> Nota de modelagem: com chunking, uma mesma URL gera vários chunks — `url UNIQUE` impede isso. Considerar `UNIQUE (url, chunk_index)`.

---

## 6. Estrutura de Diretórios (Monorepo)

```
sports-analytics-ai/
├── backend/
│   ├── app/
│   │   ├── api/            # Rotas FastAPI (/partidas, /partidas/{id}/analise)
│   │   ├── core/           # Configs, conexão Neon DB, variáveis de ambiente
│   │   ├── services/
│   │   │   ├── api_football.py  # Cliente HTTP com header x-apisports-key
│   │   │   ├── scraper.py       # Coleta RSS/HTML (GE, UOL, TNT)
│   │   │   └── rag_engine.py    # Embeddings + busca vetorial + Gemini API
│   │   └── models/         # Schemas Pydantic e modelos SQLAlchemy
│   ├── scripts/
│   │   ├── test_connection.py  # Teste de conexão com a API-Football
│   │   └── sync_round.py       # Script ETL agendado para rodar por rodada
│   ├── .env.example
│   └── requirements.txt
├── frontend/               # Aplicação Next.js + Tailwind CSS
├── docker-compose.yml      # Ambiente local opcional
├── .gitignore
└── CLAUDE.md               # Este arquivo de contexto
```

---

## 7. Roadmap de Implementação (Sprints)

- **Fase 1 (Fundação):** Setup do repositório, criação do banco no Neon DB com pgvector, execução do DDL SQL e script de carga inicial dos 20 times + tabela da Série A (league=71, season=2026).
- **Fase 2 (Ingestão & Scraping):** Pipeline de estatísticas históricas das últimas 5 rodadas + scraper de RSS/notícias de GE, UOL e TNT com vetorização e gravação no Postgres.
- **Fase 3 (Motor RAG & API):** Busca semântica (top-k notícias recentes dos dois times da partida) + prompt estruturado para a Gemini API retornar JSON validado via Pydantic + endpoints FastAPI.
- **Fase 4 (Frontend & Deploy):** Dashboard interativo em Next.js consumindo a API, deploy do backend no Render e frontend na Vercel.

---

## 8. Diretrizes de Execução para o Claude

- **Segurança:** Nunca hardcode chaves de API (`APISPORTS_KEY`, `GEMINI_API_KEY`, `DATABASE_URL`). Utilize sempre `pydantic-settings`.
- **Idempotência no ETL:** Todos os scripts de ingestão devem usar `INSERT ... ON CONFLICT DO UPDATE` (Upsert) para evitar duplicação de partidas ou notícias caso o pipeline rode mais de uma vez.
- **Respostas Estruturadas da IA:** Ao integrar com o Gemini, utilize Structured Outputs (JSON Schema) para garantir que o frontend receba campos previsíveis (`resumo_geral`, `tendencia_escanteios`, `tendencia_cartoes`, `tendencia_gols`, `alertas_desfalques`).
- **Status atual:** Repositório em `~/dev/ProjetosPessoais/sports-analytics-ai` (WSL). Prontos: estrutura do `backend/`, config, `scripts/test_connection.py` (API-Football OK), cliente `app/services/espn.py` (`EspnClient`: `scoreboard`, `jogos_encerrados_desde`, `summary`, `estatisticas_partida`, `standings`, `times`) e `scripts/explore_espn_summary.py`. ESPN mapeada: TEM H2H (`seasonseries`, últimos 5 entre temporadas), forma (`lastFiveGames`, antigo→recente, mistura competições), escalações (`rosters`, só após divulgação), odds DraftKings (`odds`/`pickcenter`), tabela e times; NÃO TEM árbitro nem lesões/suspensões estruturadas (só texto livre → virão do RAG). Banco no Neon: `backend/sql/001_init.sql` (tabelas, IDs da ESPN, sem coluna de árbitro) e `002_views.sql` (views); o `scripts/init_db.py` executa todos os `sql/*.sql` em ordem alfabética, e mudanças futuras de esquema vão em `003_...` com `ALTER TABLE`. `app/core/db.py` (`get_engine()`, `pool_pre_ping`), `scripts/load_teams.py` (20 times). **Fase 2, parte 1 concluída (12/11/2026):** `scripts/sync_round.py` (`--temporada` = temporada inteira pelo `leagues[0].calendar`; `--dias N` = últimos N + próximos 7) carregou as **383 partidas de 2026** (339 `FULL_TIME` com estatísticas, 41 `SCHEDULED`, 3 `POSTPONED`). Varre **por mês** (`dates=YYYYMM&limit=1000`; sem `limit` a ESPN corta em 100); faz upsert sem tocar em `estatisticas_json`/`analise_ia_json`; busca estatísticas de encerrados sem dados e **revisa as das últimas 48h**. Regra de status: placar só se `completed` ou `state="in"` (adiado vem `post` + `completed=false` com "0" falso → NULL). Views: `v_partidas` (temporada, `encerrada`, **rodada derivada** dos IDs da ESPN: blocos de 10, remarcado herda do adiado; validado 38 rodadas × 10 jogos × 20 times), `v_estatisticas_time_partida` (1 linha por time/jogo), `v_medias_time` (geral/casa/fora) e `v_ultimos_5` (com `forma`). Testes: `backend/tests/` (pytest; fixtures reais congeladas + `pytest -m online` contra a ESPN atual). Próximo (Fase 2, parte 2): scraper RSS de notícias (GE, UOL, TNT) + embeddings; agendar o `sync_round --dias` com frequência alta em dia de jogo (Fase 4).