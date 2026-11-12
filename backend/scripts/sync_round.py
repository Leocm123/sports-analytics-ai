"""
ETL de partidas do Brasileirão: ESPN -> tabela `partidas`.

Como rodar (a partir da pasta backend/):
    python -m scripts.sync_round --temporada   # carga inicial: a temporada inteira
    python -m scripts.sync_round               # uso recorrente: últimos 10 dias + próximos 7
    python -m scripts.sync_round --dias 2      # janela menor, para rodar com frequência

O que ele faz, em duas etapas:
1. PARTIDAS: busca os jogos da janela na ESPN (um mês por requisição),
   converte com `normalizar_evento` e grava com upsert
   (INSERT ... ON CONFLICT DO UPDATE). Placar e status são atualizados a
   cada execução, inclusive de jogos em andamento.
2. ESTATÍSTICAS: para cada jogo ENCERRADO sem estatísticas, busca o summary
   e grava `estatisticas_json`. Jogos encerrados nas últimas 48h são buscados
   de novo, para pegar as correções que a ESPN faz depois do apito. Fora
   dessa janela, o que já está no banco não é buscado de novo.

Seguro para rodar várias vezes (idempotente) e "retomável": as transações são
curtas (uma por mês nas partidas, uma por jogo nas estatísticas). Se cair no
meio, basta rodar de novo e ele continua de onde parou.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import get_engine
from app.services.espn import EspnClient, EspnError, EstatisticasTime, normalizar_evento

DIAS_PADRAO = 10  # --dias: quantos dias para trás
DIAS_PARA_FRENTE = 7  # sempre olhamos 7 dias à frente (próximos jogos)
PAUSA_ENTRE_CHAMADAS = 0.3  # segundos. A ESPN não tem cota, mas é educado não martelar.
HORAS_REVISAO_ESTATISTICAS = 48  # re-busca estatísticas de jogos dessa janela
PROGRESSO_A_CADA = 25  # imprime o progresso das estatísticas a cada N jogos

# Upsert de uma partida. Os valores entram por parâmetro (:id, :status...),
# nunca por f-string. Repare que o UPDATE NÃO mexe em estatisticas_json nem
# em analise_ia_json: esses campos têm dono próprio (etapa 2 e a IA), e o
# upsert do placar não pode apagá-los.
SQL_UPSERT_PARTIDA = text(
    """
    INSERT INTO partidas (
        id, rodada, data_hora, status, time_casa_id, time_fora_id,
        gols_casa, gols_fora, atualizado_em
    )
    VALUES (
        :id, :rodada, :data_hora, :status, :time_casa_id, :time_fora_id,
        :gols_casa, :gols_fora, NOW()
    )
    ON CONFLICT (id) DO UPDATE SET
        data_hora     = EXCLUDED.data_hora,
        status        = EXCLUDED.status,
        time_casa_id  = EXCLUDED.time_casa_id,
        time_fora_id  = EXCLUDED.time_fora_id,
        gols_casa     = EXCLUDED.gols_casa,
        gols_fora     = EXCLUDED.gols_fora,
        atualizado_em = NOW()
    """
)

# Jogos que precisam de estatísticas: encerrados e (sem estatísticas OU
# encerrados há pouco). Usamos a view v_partidas porque é nela que fica a
# regra de "encerrada", definida num lugar só (sql/002_views.sql).
SQL_PARTIDAS_PARA_ESTATISTICAS = text(
    """
    SELECT id, estatisticas_json IS NOT NULL AS revisao
    FROM v_partidas
    WHERE encerrada
      AND (
            estatisticas_json IS NULL
         OR data_hora >= NOW() - make_interval(hours => :horas)
      )
    ORDER BY data_hora
    """
)

SQL_GRAVAR_ESTATISTICAS = text(
    """
    UPDATE partidas
    SET estatisticas_json = CAST(:estatisticas AS JSONB),
        atualizado_em     = NOW()
    WHERE id = :id
    """
)


@dataclass(frozen=True)
class Janela:
    """Período (inclusive) cujos jogos serão sincronizados."""

    inicio: date
    fim: date

    @property
    def dias(self) -> int:
        return (self.fim - self.inicio).days + 1

    def contem(self, dia: date) -> bool:
        return self.inicio <= dia <= self.fim


@dataclass
class Resumo:
    """Contadores para o log final."""

    meses_consultados: int = 0
    inseridas: int = 0
    atualizadas: int = 0
    puladas: list[str] = field(default_factory=list)
    estatisticas_novas: int = 0  # gravadas pela primeira vez
    estatisticas_revisadas: int = 0  # já existiam, buscadas de novo (janela de 48h)
    sem_estatisticas: int = 0  # a ESPN ainda não tem; tentaremos na próxima
    erros: int = 0  # falha ao buscar um jogo; o ETL seguiu


# ----------------------------------------------------------------------
# Janela e argumentos
# ----------------------------------------------------------------------
def ler_argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sincroniza partidas da ESPN no banco.")
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument(
        "--temporada",
        action="store_true",
        help="carga completa: do primeiro ao último dia da temporada",
    )
    modo.add_argument(
        "--dias",
        type=int,
        default=DIAS_PADRAO,
        help=f"últimos N dias + próximos {DIAS_PARA_FRENTE} (padrão: {DIAS_PADRAO})",
    )
    args = parser.parse_args(argv)
    if args.dias < 0:
        parser.error("--dias não pode ser negativo")
    return args


def pausar() -> None:
    """Pausa curta antes de cada chamada à ESPN."""
    time.sleep(PAUSA_ENTRE_CHAMADAS)


def calcular_janela(espn: EspnClient, args: argparse.Namespace, hoje: date) -> Janela:
    """
    --temporada: do 1º ao último dia com jogo, segundo o calendário da ESPN
                 (nada de data fixa no código: muda a cada ano).
    --dias N:    de hoje-N até hoje+7.
    """
    if args.temporada:
        pausar()
        temporada = espn.temporada()
        # +1 dia: o calendário da ESPN usa o dia local, e o último jogo
        # (02/12 à noite em Brasília) já é 03/12 em UTC.
        return Janela(temporada.inicio, temporada.fim + timedelta(days=1))
    return Janela(hoje - timedelta(days=args.dias), hoje + timedelta(days=DIAS_PARA_FRENTE))


def meses_da_janela(janela: Janela) -> list[tuple[int, int]]:
    """
    Lista de (ano, mês) que cobrem a janela, com 1 dia de folga em cada ponta.

    Por que a folga? Não sabemos em que fuso a ESPN decide o "mês" de um jogo.
    Um jogo às 22h de 31/10 em Brasília já é 01/11 em UTC. Com a folga, o mês
    vizinho também é consultado, e o filtro por data decide o que entra.
    """
    primeiro = janela.inicio - timedelta(days=1)
    ultimo = janela.fim + timedelta(days=1)
    meses: list[tuple[int, int]] = []
    ano, mes = primeiro.year, primeiro.month
    while (ano, mes) <= (ultimo.year, ultimo.month):
        meses.append((ano, mes))
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return meses


# ----------------------------------------------------------------------
# Etapa 1: partidas
# ----------------------------------------------------------------------
def buscar_partidas_do_mes(
    espn: EspnClient, ano: int, mes: int, janela: Janela
) -> list[dict[str, Any]]:
    """Jogos do mês já no formato de `partidas`, filtrados pela janela."""
    pausar()
    partidas = [normalizar_evento(evento) for evento in espn.eventos_do_mes(ano, mes)]
    # data_hora está em UTC; comparamos pela data UTC.
    return [p for p in partidas if janela.contem(p["data_hora"].date())]


def carregar_ids_times(conn: Connection) -> set[int]:
    """IDs da tabela `times`, para checar a chave estrangeira antes do INSERT."""
    return set(conn.execute(text("SELECT id FROM times")).scalars())


def separar_times_desconhecidos(
    partidas: list[dict[str, Any]], times: set[int], resumo: Resumo
) -> list[dict[str, Any]]:
    """
    Remove (e avisa) jogos com time que não está em `times`.

    Sem isso, o INSERT quebraria na chave estrangeira e derrubaria o mês
    inteiro. Pulamos só o jogo problemático e seguimos.
    """
    validas = []
    for partida in partidas:
        faltando = {partida["time_casa_id"], partida["time_fora_id"]} - times
        if faltando:
            aviso = f"jogo {partida['id']}: time(s) {sorted(faltando)} fora da tabela `times`"
            print(f"  [AVISO] {aviso}. Pulado (rode scripts.load_teams?).")
            resumo.puladas.append(aviso)
        else:
            validas.append(partida)
    return validas


def ids_existentes(conn: Connection, ids: list[int]) -> set[int]:
    """Quais desses ids já estão no banco (para contar inseridas x atualizadas)."""
    resultado = conn.execute(
        text("SELECT id FROM partidas WHERE id = ANY(:ids)"), {"ids": ids}
    )
    return set(resultado.scalars())


def gravar_partidas(conn: Connection, partidas: list[dict[str, Any]], resumo: Resumo) -> None:
    """Upsert de um lote de partidas (lista de dicts -> executemany)."""
    if not partidas:
        return
    ja_existiam = ids_existentes(conn, [p["id"] for p in partidas])
    conn.execute(SQL_UPSERT_PARTIDA, partidas)
    resumo.atualizadas += len(ja_existiam)
    resumo.inseridas += len(partidas) - len(ja_existiam)


def sincronizar_partidas(espn: EspnClient, janela: Janela, resumo: Resumo) -> None:
    """Etapa 1: um mês por requisição e uma transação por mês."""
    engine = get_engine()
    with engine.connect() as conn:
        times = carregar_ids_times(conn)

    for ano, mes in meses_da_janela(janela):
        partidas = buscar_partidas_do_mes(espn, ano, mes, janela)
        resumo.meses_consultados += 1
        partidas = separar_times_desconhecidos(partidas, times, resumo)
        # Transação curta: se cair no mês seguinte, este já está salvo.
        with engine.begin() as conn:
            gravar_partidas(conn, partidas, resumo)
        print(f"  {ano}-{mes:02d}: {len(partidas)} partidas gravadas")


# ----------------------------------------------------------------------
# Etapa 2: estatísticas
# ----------------------------------------------------------------------
def partidas_para_estatisticas(conn: Connection) -> list[tuple[int, bool]]:
    """(id, revisao) dos jogos que precisam de estatísticas. revisao=True: já tinha."""
    linhas = conn.execute(
        SQL_PARTIDAS_PARA_ESTATISTICAS, {"horas": HORAS_REVISAO_ESTATISTICAS}
    )
    return [(linha.id, linha.revisao) for linha in linhas]


def gravar_estatisticas(conn: Connection, partida_id: int, stats: dict[int, EstatisticasTime]) -> None:
    """
    Grava o dict de estatísticas como JSONB.
    json.dumps transforma as chaves int (time_id) em texto: {"3457": {...}}.
    """
    conn.execute(
        SQL_GRAVAR_ESTATISTICAS, {"id": partida_id, "estatisticas": json.dumps(stats)}
    )


def buscar_e_gravar_estatisticas(
    espn: EspnClient, partida_id: int, revisao: bool, resumo: Resumo
) -> None:
    """Uma partida: busca o summary e grava, numa transação só dela."""
    pausar()
    try:
        stats = espn.estatisticas_partida(partida_id)
    except EspnError as e:
        print(f"  [AVISO] jogo {partida_id}: falha ao buscar estatísticas ({e}). Seguindo.")
        resumo.erros += 1
        return

    if not stats or not any(stats.values()):
        # A ESPN às vezes demora a publicar. Deixamos NULL para tentar de novo.
        print(f"  [AVISO] jogo {partida_id}: ESPN ainda sem estatísticas. Tentaremos depois.")
        resumo.sem_estatisticas += 1
        return

    with get_engine().begin() as conn:
        gravar_estatisticas(conn, partida_id, stats)
    if revisao:
        resumo.estatisticas_revisadas += 1
    else:
        resumo.estatisticas_novas += 1


def sincronizar_estatisticas(espn: EspnClient, resumo: Resumo) -> None:
    """Etapa 2: vale para TODO o banco, não só para a janela (pega pendências antigas)."""
    with get_engine().connect() as conn:
        pendentes = partidas_para_estatisticas(conn)
    print(f"Estatísticas a buscar: {len(pendentes)}")

    for i, (partida_id, revisao) in enumerate(pendentes, start=1):
        buscar_e_gravar_estatisticas(espn, partida_id, revisao, resumo)
        if i % PROGRESSO_A_CADA == 0:
            print(f"  ... {i}/{len(pendentes)}")


# ----------------------------------------------------------------------
# Execução
# ----------------------------------------------------------------------
def imprimir_resumo(janela: Janela | None, resumo: Resumo, segundos: float) -> None:
    print("\n=== Resumo ===")
    if janela:
        print(f"Período varrido      : {janela.inicio} a {janela.fim} ({janela.dias} dias)")
    print(f"Meses consultados    : {resumo.meses_consultados}")
    print(f"Partidas inseridas   : {resumo.inseridas}")
    print(f"Partidas atualizadas : {resumo.atualizadas}")
    print(f"Jogos pulados        : {len(resumo.puladas)}")
    print(
        f"Estatísticas         : {resumo.estatisticas_novas} novas, "
        f"{resumo.estatisticas_revisadas} revisadas, "
        f"{resumo.sem_estatisticas} sem dados na ESPN, {resumo.erros} com erro"
    )
    print(f"Duração              : {segundos:.1f}s")


def main(argv: list[str] | None = None) -> int:
    args = ler_argumentos(argv)
    resumo = Resumo()
    janela: Janela | None = None
    inicio = time.monotonic()
    codigo = 0

    try:
        with EspnClient() as espn:
            janela = calcular_janela(espn, args, date.today())
            print(f"Janela: {janela.inicio} a {janela.fim} ({janela.dias} dias)")
            sincronizar_partidas(espn, janela, resumo)
            sincronizar_estatisticas(espn, resumo)
    except KeyboardInterrupt:
        print("\n[INTERROMPIDO] O que já foi gravado está salvo. Rode de novo para continuar.")
        codigo = 130
    except EspnError as e:
        print(f"[ERRO ESPN] {e}")
        codigo = 1
    except (SQLAlchemyError, RuntimeError) as e:
        print(f"[ERRO de banco] {type(e).__name__}: {e}")
        codigo = 1

    imprimir_resumo(janela, resumo, time.monotonic() - inicio)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
