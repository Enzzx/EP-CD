# Ciência de Dados — EP01: ETL e Arquitetura Medalhão

**Integrantes do grupo:**
- Enzo Silva Bellotti
- Clóvis de Almeida Pelosini

---

## 1. Visão geral

Pipeline ETL completo sobre fontes públicas, organizado na **arquitetura medalhão**:

| Camada | Tecnologia | Conteúdo |
|---|---|---|
| 🥉 Bronze | MongoDB (`pokedex_bronze`) | Dados brutos idênticos às fontes, com linhagem |
| 🥈 Silver | PostgreSQL (schema `silver`) | Modelo dimensional em estrela (versão única da verdade) |
| 🥇 Gold | PostgreSQL (schema `gold`) | Agregados materializados no grão das perguntas |

Scripts com fronteiras estritas (cada um lê da camada anterior e escreve na seguinte):

- `extrair.py` — **fontes → bronze** (PokéAPI + `pokemon.csv` / `combats.csv`)
- `carregar.py` — **bronze → silver** (concilia chaves e modela o esquema estrela)
- `publicar.py` — **silver → gold** (agrega e materializa, sem tirar dados do banco)

## 2. Execução do pipeline

### 2.1 Bancos de dados (sugestão via contêineres)

```bash
# MongoDB
docker run -d --name mongo-ep -p 27017:27017 mongo:7

# PostgreSQL
docker run -d --name pg-ep -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=pokedex postgres:16
```

Se necessário, instale dependências: `pip install -r requirements.txt`.

Podem ser usadas variáveis de ambiente para sobrescrever as conexões padrão
(`MONGO_URI=mongodb://localhost:27017`, `PG_HOST/PG_PORT/PG_DB/PG_USER/PG_PASSWORD`).

### 2.2 Ordem de execução

```bash
python extrair.py     # 1) fontes -> bronze (1.ª execução faz ~1.700 requisições; 2.ª faz ZERO)
python carregar.py    # 2) bronze  -> silver (gera também conciliacao.csv)
python publicar.py    # 3) silver  -> gold
```

As consultas finais estão em `sql/consultas.sql` e devem ser executadas no PostgreSQL
após `publicar.py`.

## 3. Camada Bronze — justificativa do banco de documentos

**Por que MongoDB em vez de tabela relacional nesta camada?**

A PokéAPI retorna JSON profundamente aninhado e **heterogêneo entre endpoints**:

- `/pokemon/{id}`: `types[]`, `stats[]`, `abilities[]` (listas de objetos com subcampos);
- `/pokemon-species/{id}`: `varieties[]` (lista de variações da forma), `generation`, `habitat`, `growth_rate`;
- `/type/{id}`: `damage_relations` com **seis listas** (`double_damage_to`, `half_damage_to`, …).

Armazenar isso em tabelas relacionais na camada bronze exigiria achatamento ou
normalização — exatamente a transformação que a camada proíbe. O documento Mongo
preserva a **estrutura original byte a byte**, o que sustenta a imutabilidade e a
reprodutibilidade da camada: um erro de transformação é corrigido reprocessando o
silver a partir do bronze, sem nova consulta à fonte. O `upsert` por `_id` derivado da
chave natural também é nativo do modelo de documentos.

**Fidelidade de documento e uma ressalva concreta (decisão de projeto):**
o `pokemon.csv` possui colunas com ponto no nome (`Sp. Atk`, `Sp. Def`). O MongoDB
interpreta pontos como separadores de subcampo — se enviarmos a chave `Sp. Atk` em um
`$set`, ela é transformada em `{"Sp": {"Atk": …}}`. Para preservar o valor e manter a
coluna legível, o `extrair.py` grava a chave como `Sp_ Atk`/`Sp_ Def` (ponto → `_`) e
registra o cabeçalho original em `_cabecalho_original` (linhagem). Nenhum valor é
modificado; apenas a denominação interna do campo. A correspondência é documentada e
revertida na leitura em `carregar.py`. O arquivo em `dados_brutos/` permanece intacto
com o cabeçalho original.

**Linhagem:** todo documento tem `_fonte` (`pokeapi` / `csv`), `_url` (URL da
requisição ou do CSV) e `_ingerido_em` (ISO 8601 UTC), além de `_id` derivado da chave
natural (`pokemon/25`, `especie/25`, `tipo/1`, `pokemon_csv/1`, `combate/1`).

**Cache em disco:** cada resposta bruta é gravada em `dados_brutos/<tipo>/<id>.json`
antes de ir ao MongoDB. A API só é consultada quando o arquivo não existe; intervalo de
0,1 s entre requisições. Na segunda execução de `extrair.py` não há nenhuma requisição.

## 4. Camada Silver — modelo dimensional

### 4.1 Grão declarado (RS1) — antes do `CREATE TABLE`

> **Uma linha da tabela fato `fato_confronto` representa uma participação de um Pokémon
> em um combate (um dos dois lados).** Cada combate gera exatamente duas linhas:
> 50.000 combates → 100.000 linhas.

### 4.2 Diagrama do esquema estrela (RS2)

Todas as dimensões ligam-se **diretamente** à tabela fato — não há dimensão ligada a
dimensão (ausência de *snowflake*). `dim_pokemon` concentra o cadastro e guarda os
atributos descritivos (tipo, geração, habitat, cor, forma corporal, taxa de crescimento)
como **colunas de texto**, sem chave estrangeira para outras dimensões. As dimensões de
atributo (`dim_tipo`, `dim_geracao`, `dim_habitat`, `dim_cor`, `dim_forma_corporal`,
`dim_taxa_crescimento`) ligam-se à fato, no grão da participação, para os agregados de
batalha.

```
   dim_tipo     dim_geracao     dim_habitat     dim_cor
      ▲              ▲               ▲             ▲
      │              │               │             │
      │        dim_forma_corporal   dim_taxa_crescimento
      │              ▲               ▲
      │              │               │
   ┌──┴──────────────┴───────────────┴─────────────┴──────────┐
   │                      fato_confronto                       │
   │  sk_pokemon, sk_oponente          → dim_pokemon (papel)   │
   │  sk_tipo_primario/secundario      → dim_tipo              │
   │  sk_tipo_primario/secundario_oponente → dim_tipo          │
   │  sk_geracao → dim_geracao   sk_habitat → dim_habitat      │
   │  sk_cor → dim_cor   sk_forma_corporal → dim_forma_corporal│
   │  sk_taxa_crescimento → dim_taxa_crescimento               │
   │  lado, venceu, atacou_primeiro, velocidade_combatente,    │
   │  velocidade_oponente, diff_velocidade                     │
   └───────────────────────────────────────────────────────────┘

   dim_pokemon (cadastro; tipo/geração/habitat também como texto, p/ análises 1–2)
   efetividade_tipo (sk_tipo_atacante, sk_tipo_defensor, multiplicador) → dim_tipo
   log_conciliacao (tabela de auditoria fora do estrela, opcional)
```

Relações (todas `PRIMARY KEY` nas dimensões e `FOREIGN KEY` na fato — RS3/RS5):

- `fato_confronto.sk_pokemon  → dim_pokemon.sk_pokemon`
- `fato_confronto.sk_oponente → dim_pokemon.sk_pokemon` (dimensão papel, decisão 4)
- `fato_confronto.sk_tipo_primario / sk_tipo_secundario → dim_tipo` (do participante)
- `fato_confronto.sk_tipo_primario_oponente / sk_tipo_secundario_oponente → dim_tipo`
- `fato_confronto.sk_geracao / sk_habitat / sk_cor / sk_forma_corporal / sk_taxa_crescimento →` respectivas dimensões
- `efetividade_tipo → dim_tipo` (duas vezes)

> `dim_pokemon` não possui chave estrangeira alguma: os atributos descritivos são
> colunas de texto. As FKs que ligam as dimensões de atributo à fato existem para que os
> agregados de batalha (análises 4, 6, 7 e 8) sejam obtidos sem atravessar `dim_pokemon`
> e para cumprir o RS2 (toda dimensão ligada diretamente à fato). As cópias textuais em
> `dim_pokemon` servem às análises de cadastro (1 e 2), que não podem passar pela fato
> sob pena de dupla contagem (RS6).

### 4.3 As seis decisões da seção 4.2 (individuais e justificadas)

**Decisão 1 — Grão da tabela fato.** *Uma linha por participação (dois lados por combate).*
Razão: a taxa de vitórias de qualquer agrupamento (RS6, análises 3–7) reduz-se a
`AVG(venceu)` ou `SUM(venceu)/COUNT(*)` sobre uma **única coluna** numérica; não há risco
de dupla contagem por procurar o Pokémon em duas colunas distintas; a comparação entre os
lados fica explícita na própria linha (veja decisão 4). Custo: a tabela dobra de volume
(100.000 linhas), mas nenhuma análise precisa navegar entre "coluna 1" e "coluna 2".

**Decisão 2 — Representação da diferença de velocidade (análise 5).** A diferença é
**gravada como métrica** na fato: `diff_velocidade = velocidade_combatente −
velocidade_oponente` (junto com as duas velocidades, decisão 5). O cálculo em tempo de
consulta custaria em cada leitura e precisaria ser repetido pelo gold; o armazenamento é
resolvido na carga e torna o gold uma simples bandagem por `CASE`. Custo: leve redundância
em disco (uma coluna inteira a mais na fato).

**Decisão 3 — Representação da efetividade de tipos (análise 6).** O relacionamento
atacante × defensor é modelado como **tabela separada** `silver.efetividade_tipo`
(324 pares, valores 0, 0,5, 1, 2). Para o defensor com dois tipos, a análise **multiplica
os dois multiplicadores** (valores 0, 0,25, 0,5, 1, 2, 4), conforme a distinção da seção
1.1 — esse é o critério que a análise 6 materializa. Alternativas (coluna na fato ou
dimensão de confronto) onerariam a carga ou ampliariam dimensões sem ganho; a tabela
separada mantém o requisito **RS7** (responder a análise 6 por **junção** em SQL — o que
acontece dentro da própria materialização do gold, no banco).

**Decisão 4 — Representação do oponente.** O oponente é uma instância da mesma entidade
em papel distinto → **dimensão papel**: `dim_pokemon` é referenciada **duas vezes** pela
fato (`sk_pokemon` e `sk_oponente`). Nenhuma tabela de ponte é necessária porque o grão é
por participação. Custo/benefício: a dupla referência exige dois `JOIN`s quando se busca
atributos dos dois lados, mas o design elimina o risco de dupla contagem de combates.

**Decisão 5 — Localização dos atributos de status.** Os status moram em `dim_pokemon`
(estado de cadastro). Para a análise 5, que compara combatentes, **a velocidade é
também replicada** na fato (`velocidade_combatente`, `velocidade_oponente`, e a diferença
pré-calculada). A duplicação é deliberada e de escopo mínimo: as demais estatísticas
(hp, ataque, defesa) permanecem somente na dimensão. Custo: uma coluna `INT` redundante
por linha; benefício: gold lê a comparação sem `JOIN`.

**Decisão 6 — Derivação da categoria de raridade.** Nenhuma fonte oferece categoria
consolidada; a categoria é **derivada na carga** (em `carregar.py`) e persistida como
`dim_pokemon.categoria_raridade` (`BEBE` > `MITICO` > `LENDARIO` > `COMUM`, por precedência
das flags). A alternativa (expressão condicional em toda consulta) repetiria a lógica em
cada análise e dispersaria a regra de negócio; materializá-la permite usar a categoria em
qualquer consulta, inclusive na análise proposta, sem reescrita.

### 4.4 Tratamento dos problemas das fontes (R4, RS4)

- **Problema 1 (CSV `#` ≠ Pokédex):** conciliação **exclusivamente por nome**, em duas
  camadas: (a) `slugify` (lowercase, remoção de acentos, não‑alfanuméricos → `-`) para o
  caso exato e (b) uma **chave canônica por conjunto de tokens ordenados** que torna
  equivalentes nomes como `Mega Charizard X` e `charizard-mega-x` (função `normalizar`,
  em `carregar.py`). A forma padrão de cada espécie é indexada também pelo nome da
  própria espécie, cobrindo os casos em que o slug da forma padrão diverge
  (`Deoxys → deoxys-normal`, `Giratina → giratina-altered`, `Shaymin → shaymin-land`,
  `Tornadus → tornadus-incarnate`, `Basculin`, `Frillish/Jellicent`, `Pyroar`). Um
  pequeno dicionário de **exceções explícitas** trata os nomes cujo conjunto de tokens
  não casa (`DeoxysAttack Forme`, `Kyurem Black Kyurem`, `Kyurem White Kyurem`,
  `Zygarde Half Forme`, `Hoopa Confined`). **Resultado: 799 de 800 registros
  conciliados (99,9%)** — 700 por slug exato e 99 como forma alternativa; o único não
  conciliado é a `linha 63` (nome ausente, problema 2). Auditoria completa em
  `conciliacao.csv` (800 linhas) e em `silver.log_conciliacao`.
  - As formas **Primal** de Kyogre e Groudon **são conciliadas** (`kyogre-primal` e
    `groudon-primal` existem em `/pokemon/`); a herança de atributos de espécie das
    formas alternativas é feita pela lista `varieties[]` da espécie pai. Nenhuma linha é
    descartada silenciosamente.
- **Problema 2 (nome ausente, linha 63):** conceituado como **dado ausente** (a informação
  existe no domínio, faltou na fonte). O Pokémon participa de 54 combates no conjunto de
  dados; permanece no modelo como linha própria com id_csv=63, tipo e geração
  provenientes do CSV e `nome_original` = `DESCONHECIDO`. Seus confrontos não são
  descartados — **entram 50.000 combates e permanecem 50.000**.
- **Problema 3 (habitat nulo pós‑FRLG):** conceituado como **conceito inaplicável**.
  Espécies das gerações I–III com habitat definido apontam para `dim_habitat` real;
  espécies posteriores (ou nulas por não se aplicar) apontam para o membro especial
  `NAO_SE_APLICA` (não simplesmente NULL — distinto de `SEM_INFORMACAO`, usado apenas
  onde realmente não há dado). **Formas alternativas herdam os atributos de espécie da
  espécie pai**, resolvida pela lista `varieties[]` da própria espécie (forma → espécie).

**Tipos (decisão sobre os 21):** a PokéAPI retorna 21 tipos; **apenas os 18 tipos reais
(IDs 1–18) são carregados** em `dim_tipo` (mais o membro especial `SEM_TIPO`). `stellar`
(19), `unknown` (10001) e `shadow` (10002) não existem no jogo que originou as batalhas;
incluí-los contaminaria a matriz de efetividade e as análises 6 e 7. A decisão está
registrada aqui e no relatório de conciliação.

## 5. Camada Gold — materialização

`publicar.py` executa `sql/gold.sql` (DDL) e **repopula cada tabela
com `INSERT INTO gold.<tabela> SELECT … FROM silver.*` — toda a agregação acontece no
PostgreSQL**, nunca em memória Python. As responsabilidades do script são de orquestração:
esvaziar e recarregar na ordem de não‑dependência, registrar contagem de linhas e momento
(`gold.log_publicacao`), e permitir reexecução sem duplicação.

| Tabela | Grão | Atende |
|---|---|---|
| `gold.ranking_pokemon` | um Pokémon (com `qtd_combates` materializada) | análise 3 |
| `gold.taxa_vitorias_por_tipo` | um tipo | análise 4 |
| `gold.taxa_vitorias_por_faixa_velocidade` | uma faixa de diferença de velocidade | análise 5 |
| `gold.taxa_vitorias_por_multiplicador` | um multiplicador de efetividade | análise 6 |
| `gold.matriz_confronto` | par (tipo atacante, tipo defensor) | análise 7 |
| `gold.taxa_vitorias_por_habitat` | um habitat | análise 8 (proposta) |

O corte mínimo de combates da análise 3 é **materializado como coluna**
(`qtd_combates`) e aplicado apenas na consulta final (`WHERE qtd_combates >= 30`),
permitindo alterá-lo sem reexecutar o pipeline.

## 6. Camada Gold — análise proposta pelo grupo (análise 8)

**Pergunta de negócio:** *O habitat da espécie a que o Pokémon pertence influencia sua
taxa de vitórias em batalhas simuladas?*

**Relevância:** o enunciado lista habitat entre os atributos não explorados pelas
análises obrigatórias. Saber se Pokémon de certos habitats (caverna, floresta, mar,
cidade…) sistematicamente vencem mais informa tanto sobre a mecânica do simulador quanto
sobre as estatísticas de base associadas a cada bioma (ex.: Pokémon de águas são
historicamente volumosos e lentos).

**Capacidade que esta análise exige e as sete obrigatórias não exigem:** ela cruza a
**dimensão `dim_habitat`**, que nenhuma análise obrigatória utiliza, e depende de duas
capacidades do modelo que só existem por causa dele: (a) a dimensão conformada de habitat
com membro especial para o conceito inaplicável (problema 3 tratado na modelagem) e
(b) a **herança de atributos de espécie para formas alternativas** — sem ela, os
agregados por habitat perderiam todas as formas Mega/Therian/etc. A análise é materializada
em tabela própria no gold, provando que o modelo não foi desenhado apenas para as sete
perguntas do enunciado.

## 7. Procedimentos de idempotência

- **Bronze (`extrair.py`):** cache em disco precede escrita; carga por `update_one(upsert)`
  com `_id` = chave natural. Segunda execução não duplica documentos e **não realiza
  requisição** (todos os arquivos já existem).
- **Silver (`carregar.py`):** executa `sql/silver.sql` (DDL com `IF NOT EXISTS`), aplica
  `TRUNCATE ... RESTART IDENTITY CASCADE` em ordem inversa de dependência e repopula as
  dimensões antes da fato. Segunda execução produz exatamente o mesmo estado (100.000
  linhas em `fato_confronto`, 941 em `dim_pokemon`, 324 em `efetividade_tipo`).
- **Gold (`publicar.py`):** executa `sql/gold.sql`, `TRUNCATE` das tabelas agregadas e
  reconstrói. `gold.log_publicacao` guarda uma linha por tabela por carga (reescrita).
- **Auditoria de valores:** contagens esperadas — 6 gerações; 18 tipos (+ membro
  especial); 324 pares de efetividade; 50.000 combates (100.000 participações);
  800 registros no CSV (799 conciliados); 941 Pokémon na dimensão (§ 4.3).

## 8. Decisões de projeto não especificadas pelo enunciado

1. **Persistência do cabeçalho CSV com ponto** (`Sp. Atk` → `Sp_ Atk` + `_cabecalho_original`):
   limitação de representação do MongoDB documentada (§ 3).
2. **Dupla entrega de conciliação (arquivo + tabela):** `conciliacao.csv` versionado e
   também `silver.log_conciliacao` — satisfaz R4 por ambas as vias.
3. **Faixas da análise 5:** 7 faixas simétricas sobre inteiros de `diff_velocidade`
   (`<-80`, `[-80,-41]`, `[-40,-1]`, `=0`, `[1,40]`, `[41,80]`, `>80`), mantendo o espelho
   e isolando o empate exato; justificativa detalhada no `RELATORIO.md`.
4. **Precedência de raridade:** `BEBE > MITICO > LENDARIO > COMUM` (decisão 6).
5. **Membros especiais:** `SEM_TIPO` em `dim_tipo`; `NAO_SE_APLICA` (conceito inaplicável)
   e `SEM_INFORMACAO` (dado realmente ausente) em `dim_habitat`; `SEM_INFORMACAO` nas demais
   dimensões de atributo de espécie.
6. **Formas da API ausentes do CSV**: também entram em `dim_pokemon` (iden­tificação
   completa do cadastro, análise 1–2) mesmo sem participar de batalhas.
7. **Estrela estrita: atributos de espécie replicados na fato.** Para satisfazer o RS2
   (nenhuma dimensão ligada a dimensão), `dim_pokemon` **não** referencia
   `dim_tipo`/`dim_geracao`/`dim_habitat`/…; guarda tipo, geração e habitat como texto e
   serve às análises de cadastro (1 e 2). As mesmas dimensões ligam-se à `fato_confronto`
   via chaves estrangeiras, servindo aos agregados de batalha (4, 6, 7 e 8). Custo: as
   dimensões de atributo aparecem nos dois pontos (fato e cópia textual em
   `dim_pokemon`); benefício: cumpre o RS2 sem dupla contagem nas análises 1–2.

## 9. Restrição de bibliotecas (R11)

O pipeline usa apenas: `requests` (HTTP), `pymongo` (driver Mongo), `psycopg` (driver
PostgreSQL) e biblioteca padrão (`csv`, `json`, `unicodedata`, `re`, `pathlib`, `datetime`).
**Não são utilizados `pandas`, `polars`, `numpy` ou `pyarrow`**, nem qualquer função de
manipulação de dados tabulares em memória. `requirements.txt` lista exatamente o usado.
