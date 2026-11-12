-- =====================================================================
-- 002_views.sql — Views de leitura: rodada, estatísticas por time e médias
--
-- Como rodar (a partir de backend/):  python -m scripts.init_db
-- (o init_db executa todos os arquivos sql/*.sql em ordem alfabética)
--
-- Por que VIEWS e não tabelas?
-- Uma view é uma consulta salva com nome. Ela não guarda dados: recalcula
-- tudo a partir de `partidas` toda vez que é consultada. Assim as médias
-- nunca ficam desatualizadas em relação ao último sync, e não existe um
-- segundo lugar para "lembrar de atualizar". Com ~400 jogos por temporada,
-- recalcular é instantâneo.
--
-- IDEMPOTENTE: CREATE OR REPLACE VIEW pode rodar várias vezes.
-- Limitação do Postgres: o REPLACE só deixa ACRESCENTAR colunas no fim.
-- Para renomear, remover ou reordenar uma coluna, é preciso antes um
-- DROP VIEW nome_da_view CASCADE (o CASCADE derruba também as views que
-- dependem dela; rode o init_db de novo para recriá-las).
--
-- As views formam uma cadeia, então a ordem deste arquivo importa:
--   partidas -> v_partidas -> v_estatisticas_time_partida -> v_medias_time
--                                                         -> v_ultimos_5
-- =====================================================================


-- ---------------------------------------------------------------------
-- v_partidas: `partidas` + temporada + rodada derivada + flag de encerrada
-- ---------------------------------------------------------------------
-- Por que derivar a rodada?
-- A ESPN NÃO informa a rodada (event.week = null, sem notas). Mas, na
-- investigação de 12/11/2026, os 380 jogos ORIGINAIS de 2026, ordenados
-- pelo id da ESPN, formaram 38 blocos perfeitos de 10 jogos com os 20 times
-- cada: a ESPN cria a tabela de jogos de uma vez, rodada por rodada.
--
-- Regras:
-- 1. rodada = posição do jogo (por id) dentro da temporada / 10 + 1
--    (10 jogos por rodada, porque a Série A tem 20 times).
-- 2. Jogo ADIADO: a ESPN mantém o evento antigo como STATUS_POSTPONED e cria
--    um evento NOVO, com id bem maior, para a nova data. Esse jogo novo
--    ("remarcado") sairia da sequência, então ele herda a rodada do adiado
--    com o mesmo mandante e visitante.
--
-- ATENÇÃO: é uma heurística sobre IDs que a ESPN não documenta. Ela só
-- funciona com a temporada COMPLETA no banco (sync_round --temporada).
-- A coluna partidas.rodada continua NULL, porque não vem da fonte.
CREATE OR REPLACE VIEW v_partidas AS
WITH base AS (
    SELECT
        p.*,
        -- O Brasileirão segue o ano-calendário. Usamos o horário de Brasília
        -- para um jogo às 22h de 31/12 não cair no ano seguinte (em UTC).
        EXTRACT(YEAR FROM p.data_hora AT TIME ZONE 'America/Sao_Paulo')::INTEGER AS temporada
    FROM partidas p
),
remarcadas AS (
    -- Para cada jogo remarcado, o id do adiado MAIS ANTIGO do mesmo confronto
    -- (se um remarcado for adiado de novo, a rodada vem do original).
    SELECT nova.id, MIN(adiada.id) AS id_adiada
    FROM base nova
    JOIN base adiada
      ON adiada.status       = 'STATUS_POSTPONED'
     AND adiada.temporada    = nova.temporada
     AND adiada.time_casa_id = nova.time_casa_id
     AND adiada.time_fora_id = nova.time_fora_id
     AND adiada.id           < nova.id
    GROUP BY nova.id
),
originais AS (
    -- ROW_NUMBER numera 1, 2, 3... Subtraímos 1 para a divisão inteira
    -- agrupar 1-10 na rodada 1, 11-20 na rodada 2, e assim por diante.
    SELECT
        b.id,
        ((ROW_NUMBER() OVER (PARTITION BY b.temporada ORDER BY b.id) - 1) / 10 + 1)::INTEGER AS rodada
    FROM base b
    WHERE b.id NOT IN (SELECT id FROM remarcadas)
)
SELECT
    b.id,
    b.temporada,
    COALESCE(o.rodada, o_adiada.rodada) AS rodada,
    b.data_hora,
    b.status,
    -- Os únicos jogos que entram nas médias. Na liga só aparece FULL_TIME;
    -- AET/PEN (prorrogação/pênaltis) são os outros "encerrados" da ESPN.
    -- Adiado (POSTPONED) NÃO é encerrado, mesmo com state "post" na ESPN.
    b.status IN ('STATUS_FULL_TIME', 'STATUS_FINAL_AET', 'STATUS_FINAL_PEN') AS encerrada,
    b.time_casa_id,
    b.time_fora_id,
    b.gols_casa,
    b.gols_fora,
    b.estatisticas_json,
    b.analise_ia_json,
    b.atualizado_em
FROM base b
LEFT JOIN originais o        ON o.id = b.id
LEFT JOIN remarcadas r       ON r.id = b.id
LEFT JOIN originais o_adiada ON o_adiada.id = r.id_adiada;


-- ---------------------------------------------------------------------
-- v_estatisticas_time_partida: UMA LINHA POR (partida encerrada, time)
-- ---------------------------------------------------------------------
-- Por que "explodir" cada partida em duas linhas?
-- Em `partidas`, um jogo é uma linha com casa e fora lado a lado. Para tirar
-- a média de UM time, é muito mais fácil ter uma linha por time, sempre do
-- ponto de vista dele: "pro" = do time, "contra" = do adversário.
--
-- O estatisticas_json tem o formato {"3457": {"wonCorners": 9, ...}, "3445": {...}}.
-- As chaves são o time_id em TEXTO (em JSON toda chave é texto), por isso o
-- ::TEXT. O operador ->> devolve o valor como texto, e o ::INTEGER converte.
--
-- Mapeamento dos nomes da ESPN -> colunas:
--   wonCorners     -> escanteios_pro      (do adversário: escanteios_contra)
--   yellowCards    -> cartoes_amarelos
--   redCards       -> cartoes_vermelhos
--   foulsCommitted -> faltas              (do adversário: faltas_sofridas)
--   totalShots     -> chutes
--   shotsOnTarget  -> chutes_no_alvo      (do adversário: chutes_no_alvo_contra)
--   blockedShots   -> chutes_bloqueados   (a ESPN não documenta se são chutes DO
--                                          time bloqueados ou bloqueios FEITOS;
--                                          confirmar antes de usar em modelo)
--   possessionPct  -> posse               (escala 0-100)
CREATE OR REPLACE VIEW v_estatisticas_time_partida AS
SELECT
    p.temporada,
    p.rodada,
    p.data_hora,
    p.id AS partida_id,
    lado.time_id,
    lado.adversario_id,
    lado.mando,
    lado.gols_pro,
    lado.gols_contra,
    CASE
        WHEN lado.gols_pro > lado.gols_contra THEN 'V'
        WHEN lado.gols_pro = lado.gols_contra THEN 'E'
        ELSE 'D'
    END AS resultado,
    CASE
        WHEN lado.gols_pro > lado.gols_contra THEN 3
        WHEN lado.gols_pro = lado.gols_contra THEN 1
        ELSE 0
    END AS pontos,
    (p.gols_casa > 0 AND p.gols_fora > 0)  AS ambas_marcam,   -- mercado BTTS
    (p.gols_casa + p.gols_fora >= 3)       AS over_2_5,       -- 3+ gols = over 2.5
    (est.meu IS NOT NULL)                  AS tem_estatisticas,
    (est.meu ->> 'wonCorners')::INTEGER     AS escanteios_pro,
    (est.adv ->> 'wonCorners')::INTEGER     AS escanteios_contra,
    (est.meu ->> 'yellowCards')::INTEGER    AS cartoes_amarelos,
    (est.meu ->> 'redCards')::INTEGER       AS cartoes_vermelhos,
    (est.meu ->> 'yellowCards')::INTEGER
      + (est.meu ->> 'redCards')::INTEGER   AS cartoes,
    (est.adv ->> 'yellowCards')::INTEGER
      + (est.adv ->> 'redCards')::INTEGER   AS cartoes_adversario,
    (est.meu ->> 'foulsCommitted')::INTEGER AS faltas,
    (est.adv ->> 'foulsCommitted')::INTEGER AS faltas_sofridas,
    (est.meu ->> 'totalShots')::INTEGER     AS chutes,
    (est.meu ->> 'shotsOnTarget')::INTEGER  AS chutes_no_alvo,
    (est.adv ->> 'shotsOnTarget')::INTEGER  AS chutes_no_alvo_contra,
    (est.meu ->> 'blockedShots')::INTEGER   AS chutes_bloqueados,
    (est.meu ->> 'possessionPct')::NUMERIC(5, 1) AS posse
FROM v_partidas p
-- CROSS JOIN LATERAL (VALUES ...) gera as duas "visões" do mesmo jogo:
-- uma do mandante e outra do visitante.
CROSS JOIN LATERAL (
    VALUES
        ('casa', p.time_casa_id, p.time_fora_id, p.gols_casa, p.gols_fora),
        ('fora', p.time_fora_id, p.time_casa_id, p.gols_fora, p.gols_casa)
) AS lado (mando, time_id, adversario_id, gols_pro, gols_contra)
-- Separa o pedaço do JSON de cada lado, para não repetir a expressão acima.
CROSS JOIN LATERAL (
    SELECT
        p.estatisticas_json -> lado.time_id::TEXT       AS meu,
        p.estatisticas_json -> lado.adversario_id::TEXT AS adv
) AS est
WHERE p.encerrada;


-- ---------------------------------------------------------------------
-- v_medias_time: médias de cada time na temporada (geral, casa e fora)
-- ---------------------------------------------------------------------
-- Uma linha por (temporada, time, recorte). Para a média geral:
--   SELECT * FROM v_medias_time WHERE recorte = 'geral';
--
-- GROUPING SETS calcula vários agrupamentos numa consulta só: um sem o mando
-- (o "geral") e um com o mando ("casa" e "fora"). No agrupamento sem mando,
-- a coluna mando vem NULL, e o COALESCE transforma isso em 'geral'.
--
-- AVG ignora NULL: um jogo encerrado ainda sem estatísticas conta nos gols,
-- mas não puxa a média de escanteios para baixo. `jogos_com_estatisticas`
-- mostra em quantos jogos as médias de estatística se baseiam.
CREATE OR REPLACE VIEW v_medias_time AS
SELECT
    e.temporada,
    e.time_id,
    t.nome,
    COALESCE(e.mando, 'geral')                         AS recorte,
    COUNT(*)                                           AS jogos,
    COUNT(*) FILTER (WHERE e.tem_estatisticas)         AS jogos_com_estatisticas,
    -- 1X2
    ROUND(100.0 * AVG((e.resultado = 'V')::INTEGER), 1) AS pct_vitorias,
    ROUND(100.0 * AVG((e.resultado = 'E')::INTEGER), 1) AS pct_empates,
    ROUND(100.0 * AVG((e.resultado = 'D')::INTEGER), 1) AS pct_derrotas,
    -- Gols / ambas marcam
    ROUND(AVG(e.gols_pro), 2)                          AS media_gols_pro,
    ROUND(AVG(e.gols_contra), 2)                       AS media_gols_contra,
    ROUND(100.0 * AVG(e.ambas_marcam::INTEGER), 1)     AS pct_ambas_marcam,
    ROUND(100.0 * AVG(e.over_2_5::INTEGER), 1)         AS pct_over_2_5,
    -- Escanteios
    ROUND(AVG(e.escanteios_pro), 2)                    AS media_escanteios_pro,
    ROUND(AVG(e.escanteios_contra), 2)                 AS media_escanteios_contra,
    -- Cartões e faltas
    ROUND(AVG(e.cartoes), 2)                           AS media_cartoes,
    ROUND(AVG(e.cartoes_adversario), 2)                AS media_cartoes_adversario,
    ROUND(AVG(e.faltas), 2)                            AS media_faltas,
    ROUND(AVG(e.faltas_sofridas), 2)                   AS media_faltas_sofridas,
    -- Finalizações e posse
    ROUND(AVG(e.chutes_no_alvo), 2)                    AS media_chutes_no_alvo,
    ROUND(AVG(e.chutes_no_alvo_contra), 2)             AS media_chutes_no_alvo_contra,
    ROUND(AVG(e.chutes_bloqueados), 2)                 AS media_chutes_bloqueados,
    ROUND(AVG(e.posse), 1)                             AS media_posse
FROM v_estatisticas_time_partida e
JOIN times t ON t.id = e.time_id
GROUP BY GROUPING SETS (
    (e.temporada, e.time_id, t.nome),           -- geral
    (e.temporada, e.time_id, t.nome, e.mando)   -- casa / fora
);


-- ---------------------------------------------------------------------
-- v_ultimos_5: as mesmas métricas, só nos 5 jogos mais recentes de cada time
-- ---------------------------------------------------------------------
-- Mostra a FORMA ATUAL, que costuma pesar mais que a média da temporada.
-- ROW_NUMBER() OVER (PARTITION BY time_id ORDER BY data_hora DESC) numera os
-- jogos de cada time do mais recente (1) para o mais antigo; ficamos com 1 a 5.
-- Não separamos por temporada: no começo de um campeonato, os "últimos 5"
-- incluem o fim do anterior, que é o que interessa para a forma.
CREATE OR REPLACE VIEW v_ultimos_5 AS
WITH numerados AS (
    SELECT
        e.*,
        ROW_NUMBER() OVER (PARTITION BY e.time_id ORDER BY e.data_hora DESC) AS n
    FROM v_estatisticas_time_partida e
)
SELECT
    u.time_id,
    t.nome,
    COUNT(*)                                           AS jogos,
    COUNT(*) FILTER (WHERE u.tem_estatisticas)         AS jogos_com_estatisticas,
    -- Ex.: "VVEDV", do jogo mais recente para o mais antigo
    string_agg(u.resultado, '' ORDER BY u.data_hora DESC) AS forma,
    MAX(u.data_hora)                                   AS ultimo_jogo,
    ROUND(100.0 * AVG((u.resultado = 'V')::INTEGER), 1) AS pct_vitorias,
    ROUND(100.0 * AVG((u.resultado = 'E')::INTEGER), 1) AS pct_empates,
    ROUND(100.0 * AVG((u.resultado = 'D')::INTEGER), 1) AS pct_derrotas,
    ROUND(AVG(u.gols_pro), 2)                          AS media_gols_pro,
    ROUND(AVG(u.gols_contra), 2)                       AS media_gols_contra,
    ROUND(100.0 * AVG(u.ambas_marcam::INTEGER), 1)     AS pct_ambas_marcam,
    ROUND(100.0 * AVG(u.over_2_5::INTEGER), 1)         AS pct_over_2_5,
    ROUND(AVG(u.escanteios_pro), 2)                    AS media_escanteios_pro,
    ROUND(AVG(u.escanteios_contra), 2)                 AS media_escanteios_contra,
    ROUND(AVG(u.cartoes), 2)                           AS media_cartoes,
    ROUND(AVG(u.cartoes_adversario), 2)                AS media_cartoes_adversario,
    ROUND(AVG(u.faltas), 2)                            AS media_faltas,
    ROUND(AVG(u.faltas_sofridas), 2)                   AS media_faltas_sofridas,
    ROUND(AVG(u.chutes_no_alvo), 2)                    AS media_chutes_no_alvo,
    ROUND(AVG(u.chutes_no_alvo_contra), 2)             AS media_chutes_no_alvo_contra,
    ROUND(AVG(u.chutes_bloqueados), 2)                 AS media_chutes_bloqueados,
    ROUND(AVG(u.posse), 1)                             AS media_posse
FROM numerados u
JOIN times t ON t.id = u.time_id
WHERE u.n <= 5
GROUP BY u.time_id, t.nome;
