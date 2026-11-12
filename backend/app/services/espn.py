"""
Cliente HTTP da API pública (não oficial) da ESPN para o Brasileirão Série A.

Por que a ESPN?
- O plano Free da API-Football não libera a temporada 2026 (ver CLAUDE.md, A0).
- A ESPN não exige chave nem tem cota fixa de requisições.

Cuidados por ser uma API NÃO oficial:
1. Ela pode mudar o formato do JSON sem aviso. Por isso, todo acesso a chaves
   aninhadas fica concentrado AQUI, e um formato inesperado vira `EspnError`
   com mensagem clara (em vez de um KeyError perdido no meio do ETL).
2. O resto do projeto não deve ler o JSON cru da ESPN. Prefira os métodos que
   devolvem dados normalizados (`times`, `standings`, `estatisticas_partida`):
   eles são o nosso "contrato". Se um dia trocarmos de fonte, basta outro
   cliente devolver os mesmos formatos.
3. Mesmo sem cota, a regra do projeto continua valendo: só o ETL (scripts/)
   chama a ESPN e salva no banco. O frontend nunca chama a ESPN diretamente.

Detalhe das URLs (a ESPN usa dois prefixos diferentes):
- {base}/site/v2/sports/soccer/bra.1/...  -> scoreboard, summary, teams
- {base}/v2/sports/soccer/bra.1/standings -> classificação
"""

from datetime import date, timedelta
from typing import Any

import httpx

from app.core.config import settings

# Estatísticas de UM time em UMA partida, ex.: {"wonCorners": 9, "possessionPct": 44.4}
EstatisticasTime = dict[str, int | float | None]

# Quantos caracteres do corpo da resposta mostrar nas mensagens de erro.
LIMITE_TRECHO_ERRO = 300


class EspnError(Exception):
    """Erro ao falar com a ESPN: HTTP, rede, JSON inválido ou formato inesperado."""


# ----------------------------------------------------------------------
# Funções auxiliares (não dependem do cliente HTTP, por isso ficam fora da classe)
# ----------------------------------------------------------------------
def _trecho(texto: str, limite: int = LIMITE_TRECHO_ERRO) -> str:
    """Corta textos longos para a mensagem de erro não inundar o terminal."""
    return texto if len(texto) <= limite else texto[:limite] + "..."


def _converter_valor(texto: str | None) -> int | float | None:
    """
    Converte o `displayValue` da ESPN (sempre string) em número.

    No boxscore a ESPN só manda o texto que aparece na tela ("9", "44.4"),
    sem um campo numérico. Convertendo aqui, o resto do código pode somar e
    tirar médias sem se preocupar com strings.
    - "" ou "-" -> None  (dado ausente, que é diferente de zero!)
    - "9"       -> 9     (int)
    - "44.4"    -> 44.4  (float)
    - outro     -> None  (texto não numérico)
    """
    if texto is None:
        return None
    texto = texto.strip()
    if texto in ("", "-"):
        return None
    try:
        return int(texto)
    except ValueError:
        pass
    try:
        return float(texto)
    except ValueError:
        return None


def _inteiro_se_possivel(valor: float | None) -> int | float | None:
    """A tabela da ESPN manda tudo como float (76.0 pontos). Viramos int quando dá."""
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    return valor


def _jogo_encerrado(evento: dict) -> bool:
    """True se o evento do scoreboard já terminou (status.type.completed)."""
    return bool(evento.get("status", {}).get("type", {}).get("completed", False))


def _estado_do_jogo(summary: dict) -> str | None:
    """
    Estado do jogo dentro de um `summary`: "pre" (não começou), "in" (ao vivo)
    ou "post" (encerrado). None se a ESPN não informar.
    """
    try:
        return summary["header"]["competitions"][0]["status"]["type"]["state"]
    except (KeyError, IndexError, TypeError):
        return None


def normalizar_boxscore(summary: dict) -> dict[int, EstatisticasTime]:
    """
    Extrai as estatísticas da partida de um `summary` já baixado.

    Fica separada de `EspnClient.estatisticas_partida` para podermos
    reaproveitar um `summary` que já está em memória, sem outra requisição.

    Retorno: {time_id: {"wonCorners": 9, "yellowCards": 2, "possessionPct": 44.4, ...}}

    Atenção às escalas dos percentuais (vêm assim da ESPN):
    - possessionPct vai de 0 a 100 (44.4 = 44,4% de posse)
    - shotPct, passPct, crossPct, tacklePct, longballPct vão de 0 a 1 (0.5 = 50%)

    Antes do jogo começar (estado "pre"), o boxscore traz totais da TEMPORADA
    (totalGoals, goalsConceded...), e não da partida. Para não misturar as
    duas coisas, devolvemos {} nesse caso.
    """
    if _estado_do_jogo(summary) == "pre":
        return {}

    resultado: dict[int, EstatisticasTime] = {}
    for time in summary.get("boxscore", {}).get("teams", []):
        try:
            time_id = int(time["team"]["id"])  # a ESPN manda o ID como string
        except (KeyError, TypeError, ValueError) as e:
            raise EspnError(f"Formato inesperado no boxscore: {e!r}") from e
        resultado[time_id] = {
            stat["name"]: _converter_valor(stat.get("displayValue"))
            for stat in time.get("statistics", [])
            if "name" in stat
        }
    return resultado


def _nome_curto(time: dict) -> str:
    """
    Nome do time como o torcedor está acostumado a ver ("Athletico-PR").

    Cada endpoint da ESPN preenche `displayName` de um jeito: no /teams vem
    "Athletico-PR", mas no /standings vem "Athletico Paranaense". O campo
    `shortDisplayName` é igual nos dois, então ele tem prioridade.
    """
    return time.get("shortDisplayName") or time["displayName"]


def _normalizar_linha_tabela(entrada: dict) -> dict[str, Any]:
    """
    Converte uma linha da classificação da ESPN em um dict "achatado".

    Mantemos os nomes de estatística da própria ESPN (rank, points, wins...)
    para ficar fácil comparar com o JSON original quando algo der errado.
    """
    time = entrada["team"]
    linha: dict[str, Any] = {
        "time_id": int(time["id"]),
        "nome": _nome_curto(time),
        "sigla": time.get("abbreviation"),
    }
    for stat in entrada.get("stats", []):
        valor = stat.get("value")
        # "overall" (ex.: "23-7-4") não tem valor numérico: guardamos o texto.
        linha[stat["name"]] = (
            _inteiro_se_possivel(valor) if valor is not None else stat.get("displayValue")
        )
    return linha


def _escolher_escudo(logos: list[dict]) -> str | None:
    """
    A ESPN manda mais de uma versão do escudo (ex.: "default" e "dark").
    Preferimos a "default" (fundo claro); se não houver, usamos a primeira.
    """
    for logo in logos:
        if "default" in logo.get("rel", []):
            return logo.get("href")
    return logos[0].get("href") if logos else None


def _normalizar_time(time: dict) -> dict[str, Any]:
    """Converte um time da ESPN já no formato das colunas da tabela `times`."""
    return {
        "id": int(time["id"]),
        "nome": _nome_curto(time),  # ex.: "Athletico-PR"
        "sigla": time.get("abbreviation"),
        "escudo_url": _escolher_escudo(time.get("logos", [])),
    }


# ----------------------------------------------------------------------
# Cliente
# ----------------------------------------------------------------------
class EspnClient:
    """
    Uso recomendado (o `with` fecha a conexão sozinho):

        with EspnClient() as espn:
            jogos = espn.scoreboard()
    """

    def __init__(self, timeout: float = 20.0) -> None:
        self._liga = settings.espn_league
        # httpx.Client reaproveita a conexão TCP entre chamadas, o que deixa
        # bem mais rápido quando iteramos dia a dia no scoreboard.
        # follow_redirects=True: se a ESPN redirecionar, seguimos sem erro.
        self._client = httpx.Client(
            base_url=settings.espn_base_url,
            timeout=timeout,
            follow_redirects=True,
        )

    def __enter__(self) -> "EspnClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    # Infraestrutura HTTP
    # ------------------------------------------------------------------
    def _caminho_site(self, recurso: str) -> str:
        """Prefixo usado por scoreboard, summary e teams."""
        return f"/site/v2/sports/soccer/{self._liga}/{recurso}"

    def _caminho_v2(self, recurso: str) -> str:
        """Prefixo diferente, usado pela classificação (standings)."""
        return f"/v2/sports/soccer/{self._liga}/{recurso}"

    def _get(self, caminho: str, params: dict[str, Any] | None = None) -> dict:
        """
        Faz um GET e devolve o JSON. Qualquer problema vira `EspnError`.

        Diferente da API-Football, a ESPN usa os códigos HTTP corretamente
        (404 para evento inexistente, 400 para parâmetro inválido) e manda o
        motivo no corpo. Por isso mostramos um trecho do corpo no erro.
        """
        try:
            resp = self._client.get(caminho, params=params)
        except httpx.RequestError as e:  # sem rede, DNS, timeout...
            raise EspnError(
                f"Falha de rede ao acessar {caminho}: {type(e).__name__}: {e}"
            ) from e

        if resp.status_code >= 400:
            raise EspnError(
                f"HTTP {resp.status_code} em {resp.url}. Corpo: {_trecho(resp.text)}"
            )

        try:
            dados = resp.json()
        except ValueError as e:
            raise EspnError(
                f"Resposta não é JSON em {resp.url}. Corpo: {_trecho(resp.text)}"
            ) from e

        if not isinstance(dados, dict):
            raise EspnError(f"JSON inesperado em {resp.url}: esperava um objeto.")
        return dados

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------
    def scoreboard(self, data: date | None = None) -> list[dict]:
        """
        Jogos de UMA data (ou, sem data, os jogos dos próximos dias).

        A ESPN aceita só uma data por vez no formato YYYYMMDD. O formato de
        intervalo "YYYYMMDD-YYYYMMDD" responde HTTP 400, então para cobrir
        vários dias use `jogos_encerrados_desde`, que pede um dia de cada vez.

        Retorna a lista `events` já "desembrulhada" do JSON.
        """
        params = {"dates": data.strftime("%Y%m%d")} if data else None
        return self._get(self._caminho_site("scoreboard"), params).get("events", [])

    def jogos_encerrados_desde(self, dias: int, hoje: date | None = None) -> list[dict]:
        """
        Jogos encerrados entre `hoje - dias` e `hoje` (inclusive), do mais
        recente para o mais antigo.

        Faz uma requisição por dia (dias + 1 no total). Um jogo perto da
        meia-noite pode aparecer em duas datas, por isso guardamos os jogos
        num dict pelo `id`, o que já elimina duplicatas.

        `hoje` é opcional e serve para testes ou para reprocessar o passado.
        """
        if dias < 0:
            raise ValueError("`dias` não pode ser negativo.")
        hoje = hoje or date.today()

        por_id: dict[str, dict] = {}
        for dias_atras in range(dias + 1):
            dia = hoje - timedelta(days=dias_atras)
            for evento in self.scoreboard(dia):
                if _jogo_encerrado(evento):
                    por_id[evento["id"]] = evento

        # As datas vêm em ISO 8601 ("2026-11-06T22:00Z"), que ordena como texto.
        return sorted(por_id.values(), key=lambda e: e.get("date", ""), reverse=True)

    def summary(self, event_id: int | str) -> dict:
        """JSON completo de um jogo: boxscore, escalações, H2H, odds, lances..."""
        return self._get(self._caminho_site("summary"), {"event": event_id})

    def estatisticas_partida(self, event_id: int | str) -> dict[int, EstatisticasTime]:
        """
        Estatísticas normalizadas da partida, por time:
        {time_id: {"wonCorners": 9, "yellowCards": 2, "possessionPct": 44.4, ...}}

        Devolve {} se o jogo ainda não começou. Veja `normalizar_boxscore`
        para as escalas dos percentuais.
        """
        return normalizar_boxscore(self.summary(event_id))

    def standings(self) -> list[dict]:
        """
        Classificação atual, ordenada pela posição (`rank`).

        Cada linha: {"time_id": 819, "nome": "Flamengo", "sigla": "FLA",
                     "rank": 1, "points": 76, "wins": 23, "ties": 7, ...}

        Atenção: este endpoint usa o prefixo /v2 (e não /site/v2).
        """
        dados = self._get(self._caminho_v2("standings"))
        try:
            # Série A é pontos corridos: há um único grupo em `children`.
            entradas = dados["children"][0]["standings"]["entries"]
            linhas = [_normalizar_linha_tabela(e) for e in entradas]
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise EspnError(f"Formato inesperado em /standings: {e!r}") from e
        return sorted(linhas, key=lambda linha: linha.get("rank") or 999)

    def times(self) -> list[dict]:
        """
        Os 20 times da liga, já no formato da tabela `times` do banco:
        [{"id": 3458, "nome": "Athletico-PR", "sigla": "CAP", "escudo_url": "https://..."}]
        """
        dados = self._get(self._caminho_site("teams"))
        try:
            brutos = dados["sports"][0]["leagues"][0]["teams"]
            return [_normalizar_time(item["team"]) for item in brutos]
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise EspnError(f"Formato inesperado em /teams: {e!r}") from e
