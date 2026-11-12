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
| **Motor de IA (LLM)** | Google Gemini API — modelo configurável via `GEMINI_MODEL` (Gemini 1.5 Flash foi aposentado; usar um Flash/Flash-Lite atual do free tier). Fallback: Groq (Llama) | **Google AI Studio** (Free Tier) | Limites do free tier mudam com frequência — conferir no AI Studio. Suporta JSON estruturado. |
| **Frontend** | React / Next.js (App Router), Tailwind CSS, Recharts/Lucide | **Vercel** (Hobby / Free Tier) | Deploy contínuo via GitHub com CDN global gratuita. |

---

## 3. Especificação das Fontes de Dados

### A0. DECISÃO (12/11/2026): Fonte principal da temporada atual = API pública da ESPN
- **Motivo:** o plano Free da API-Football só libera as temporadas 2022–2024 (`"Free plans do not have access to this season, try from 2022 to 2024."`).
- **Base:** `https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1` — sem chave, sem cota fixa. Não oficial: isolar em `services/espn.py` atrás de uma interface de provedor, para poder trocar de fonte.
- **Endpoints validados:**
  - `GET /scoreboard` (jogos dos próximos dias) e `GET /scoreboard?dates=YYYYMMDD` (uma data só; **intervalo `A-B` retorna 400**)
  - `GET /summary?event={id}` → `boxscore.teams[].statistics[]` (`name`/`displayValue`)
- **Campos confirmados (jogo 401841302, Vitória x Fluminense, 06/11/2026):** `wonCorners`, `yellowCards`, `redCards`, `foulsCommitted`, `totalShots`, `shotsOnTarget`, `blockedShots`, `possessionPct`, `offsides`, `saves`, `totalCrosses`, `accurateCrosses`, passes, desarmes, interceptações, cortes.
- **IDs:** times e eventos passam a usar os IDs da ESPN (não os da API-Football).
- **A validar:** endpoint de standings, H2H e lesões/árbitro no `summary`.
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
- **Status atual:** Repositório em `~/dev/ProjetosPessoais/sports-analytics-ai` (WSL). Estrutura do `backend/`, config, `scripts/test_connection.py` (API-Football OK com chave válida) e `scripts/test_espn.py` (ESPN validada) prontos. Próximo: `services/espn.py` + mapear standings/H2H/lesões na ESPN, depois DDL no Neon (IDs da ESPN) e carga dos 20 times.