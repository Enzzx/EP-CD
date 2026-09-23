# Ciência de Dados — EP01: ETL e Arquitetura Medalhão

## Contexto

Este trabalho consiste na construção de um pipeline de dados completo, da extração em fontes públicas até a publicação das análises finais em SQL.

Denomina-se **ETL** (*extract, transform, load*) o processo que extrai dados de sistemas de origem, os transforma em formato adequado à análise e os carrega em um repositório analítico. A **arquitetura medalhão** (*medallion architecture*) é o padrão que organiza esse processo em três camadas sucessivas, cada uma com responsabilidade e nível de refinamento distintos:

| Camada | Tecnologia neste trabalho | Conteúdo | Restrição |
|---|---|---|---|
| 🥉 **Bronze** | MongoDB | Dado bruto, idêntico ao que a fonte entregou | Não sofre transformação |
| 🥈 **Silver** | PostgreSQL | Dado limpo, conciliado e modelado dimensionalmente | Constitui a versão única da verdade |
| 🥇 **Gold** | PostgreSQL | Agregados no grão das perguntas de negócio | Atende ao consumo, não à modelagem |

A propriedade que sustenta o padrão é a imutabilidade da camada bronze: preservado o dado bruto, um erro de transformação é corrigido reprocessando as camadas seguintes, sem nova consulta às fontes originais. Essa é a razão pela qual a camada bronze não pode ser transformada.

As duas fontes deste trabalho têm naturezas opostas. A PokéAPI é uma API REST que retorna JSON aninhado e descreve **o que as entidades são**; o conjunto de dados de batalhas é um arquivo CSV plano que registra **o que ocorreu**. Ambas utilizam espaços de chave incompatíveis, apresentam ausências de naturezas distintas e divergem em alguns valores. A reconciliação dessas fontes constitui o núcleo técnico do trabalho.

## 1. Fontes de dados

### 1.1 PokéAPI

Base: `https://pokeapi.co/api/v2/`. API REST pública, sem autenticação, mantida por voluntários.

| Endpoint | Conteúdo | Campos de interesse |
|---|---|---|
| `/pokemon/{id}` | A **forma**, com as características físicas | `name`, `height`, `weight`, `base_experience`, `is_default`, `types[]`, `stats[]`, `abilities[]` |
| `/pokemon-species/{id}` | A **espécie**, com a classificação | `generation`, `habitat`, `color`, `shape`, `growth_rate`, `capture_rate`, `base_happiness`, `is_legendary`, `is_mythical`, `is_baby`, `varieties[]` |
| `/type/{id}` | A mecânica de combate | `name`, `damage_relations` |

**Escopo obrigatório:** gerações I a VI, correspondentes às 721 primeiras espécies, acrescidas de **todas as formas alternativas** dessas espécies, relacionadas no campo `varieties[]` de cada uma.

As formas alternativas não se restringem às Mega. O conjunto de dados de batalhas possui 800 registros para 721 espécies, e as 79 linhas excedentes compreendem, além das formas Mega, as formas Primal de Kyogre e Groudon e um conjunto de formas de outra natureza: `Deoxys Attack/Defense/Speed Forme`, `Giratina Altered/Origin Forme`, `Shaymin Sky Forme`, as formas `Therian` de Tornadus, Thundurus e Landorus, `Kyurem Black` e `Kyurem White`, `Keldeo Resolute Forme`, `Meloetta Pirouette Forme`, `Darmanitan Zen Mode`, os `Cloak` de Wormadam, os `Size` de Pumpkaboo e Gourgeist, `Aegislash Blade/Shield Forme` e `Hoopa Unbound`. Extrair apenas as formas Mega inviabiliza a conciliação de cerca de trinta registros que participam de combates.

O endpoint `/type/` retorna **21 tipos**, e não 18. Os identificadores de 1 a 18 correspondem aos tipos reais; `stellar` (19), `unknown` (10001) e `shadow` (10002) não existem no jogo que originou os dados de batalha. A decisão quanto a filtrá-los cabe ao grupo e deve ser justificada no `README.md`.

O campo `damage_relations` de cada tipo contém seis listas — `double_damage_to`, `half_damage_to`, `no_damage_to` e as três correspondentes com sufixo `_from`. Em conjunto, elas definem a **matriz de efetividade** de 18 por 18 posições: o fator pelo qual um tipo atacante multiplica o dano causado a um tipo defensor, com valores 0, 0,5, 1 ou 2. Essa matriz é insumo das análises 6 e 7.

Cabe distinguir a matriz do **multiplicador efetivo de um confronto**. A matriz relaciona um tipo a outro tipo e assume os quatro valores acima. O multiplicador efetivo relaciona um tipo atacante a um Pokémon defensor: quando o defensor possui dois tipos, os dois fatores se multiplicam, e o resultado assume qualquer dos seis valores 0, 0,25, 0,5, 1, 2 ou 4. Qual dos dois a análise 6 utiliza é objeto da decisão 3 da seção 4.2.

### 1.2 Conjunto de dados de batalhas

Dois arquivos CSV, sob licença MIT, de acesso público e sem autenticação:

- `https://raw.githubusercontent.com/cdiener/pokemon_app/master/pokemon.csv` — 800 linhas
- `https://raw.githubusercontent.com/cdiener/pokemon_app/master/combats.csv` — 50.000 linhas

O arquivo `pokemon.csv` tem o cabeçalho `#,Name,Type 1,Type 2,HP,Attack,Defense,Sp. Atk,Sp. Def,Speed,Generation,Legendary`. O arquivo `combats.csv` tem o cabeçalho `First_pokemon,Second_pokemon,Winner`, cujos três valores referenciam o campo `#` do arquivo anterior. O Pokémon indicado na primeira coluna é o que ataca primeiro.

```
First_pokemon,Second_pokemon,Winner
266,298,298
702,701,701
191,668,668
```

Trata-se de batalhas individuais **simuladas**: os resultados foram produzidos por um programa, e não por partidas efetivamente disputadas. Todo padrão medido nas análises é mediado por esse programa.

### 1.3 Taxa de vitórias

Define-se **taxa de vitórias** (*winrate*) como a proporção entre o número de combates vencidos e o número de combates disputados por uma entidade. Um Pokémon que disputou 120 combates e venceu 78 apresenta taxa de vitórias de 0,65. A definição se aplica a qualquer agrupamento: a taxa de vitórias de um tipo é a proporção de vitórias entre todos os combates disputados por Pokémon daquele tipo.

Duas propriedades da métrica são relevantes para as análises da seção 7:

- Por ser uma razão, seu valor é ininterpretável sem o denominador. Uma taxa de 100% sobre três combates indica amostra insuficiente, e não desempenho superior. Toda apresentação da taxa de vitórias deve ser acompanhada do número de combates.
- Por ser uma média de resultados binários, seu cálculo se reduz a uma média aritmética simples quando o resultado de cada combate é armazenado como valor numérico, e a uma contagem condicional quando armazenado como texto. O requisito RS6 da seção 4.1 decorre dessa propriedade.

### 1.4 Problemas conhecidos das fontes

Os três problemas a seguir estão documentados porque o objetivo de aprendizagem é o **tratamento** de cada um, e não a sua descoberta.

**Problema 1 — O campo `#` do `pokemon.csv` não corresponde ao número da Pokédex.**

Trata-se de um índice sequencial de 1 a 800 no qual as formas alternativas ocupam linhas próprias, imediatamente após a forma padrão da espécie:

```
7,Charizard,Fire,Flying,78,84,78,109,85,100,1,False
8,Mega Charizard X,Fire,Dragon,78,130,111,130,85,100,1,False
9,Mega Charizard Y,Fire,Flying,78,104,78,159,115,100,1,False
```

Charizard é o registro 7 no CSV e o registro 6 na Pokédex. Não há relação aritmética entre os dois espaços de chave: a conciliação só é possível por nome, e denominações como `Mega Charizard X` correspondem a `charizard-mega-x` na PokéAPI. Nenhuma estratégia de normalização produz correspondência integral; o requisito R4 trata das exceções.

**Problema 2 — A linha 63 do `pokemon.csv` não possui nome.**

```
63,,Fighting,,65,105,60,60,70,95,1,False
```

O registro existe, possui atributos e tipo, e participa de combates; apenas o nome está ausente. Configura-se um **dado ausente**: a informação existe no domínio, mas não foi registrada na fonte.

**Problema 3 — O campo `habitat` da PokéAPI é nulo para parte das espécies.**

A ausência não decorre de falha de coleta: o conceito de habitat existe apenas nos jogos até FireRed e LeafGreen. Para as gerações posteriores o campo não está vazio — ele **não se aplica**. Configura-se um **conceito inaplicável**.

Os problemas 2 e 3 se manifestam de forma idêntica no dado, como valor nulo em uma coluna, e exigem tratamentos distintos na modelagem. Aplicar o mesmo tratamento a ambos constitui erro conceitual.

Registre-se ainda a distinção entre **forma** e **espécie**: as formas alternativas possuem identificadores superiores a 10000 na PokéAPI, sendo `charizard-mega-x` o identificador 10034, e apresentam `is_default` com valor falso. Uma iteração de 1 a 721 não as alcança; elas são referenciadas no campo `varieties[]` da espécie correspondente. Essas formas existem em `/pokemon/` e não existem em `/pokemon-species/`.

Decorre daí uma consequência para a modelagem: os atributos de espécie — geração, habitat, cor, forma corporal, raridade — não estão disponíveis para as formas alternativas, e devem ser herdados da espécie a que pertencem. O procedimento adotado para essa herança deve constar do `README.md`.

## 2. Arquitetura exigida

```
   FONTES              🥉 BRONZE            🥈 SILVER              🥇 GOLD
                       (MongoDB)          (PostgreSQL)          (PostgreSQL)

  PokéAPI  ─────┐   ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
  (REST/JSON)   ├──▶│   pokemon    │    │   o modelo   │    │   os agregados   │
                │   │   especies   │    │  dimensional │    │  que respondem   │
                │   │   tipos      │───▶│              │───▶│   às perguntas   │
  combats.csv   │   │   pokemon_csv│    │  (projetado  │    │   (projetado     │
  pokemon.csv ──┘   │   combates   │    │  pelo grupo) │    │   pelo grupo)    │
  (CSV plano)       └──────────────┘    └──────────────┘    └────────┬─────────┘
                     extrair.py            carregar.py           publicar.py
                    (sem transformar)   (concilia e modela)    (agrega e publica)
                                                                     │
                                                                ┌────▼─────┐
                                                                │ análises │
                                                                └──────────┘
```

Três scripts, três fronteiras. Cada script lê da camada anterior, escreve na camada seguinte e não acessa camadas não adjacentes. Todos devem admitir execução repetida sem alteração do resultado obtido na primeira execução.

As camadas, os bancos e os scripts acima são obrigatórios. O conteúdo das camadas silver e gold é projetado pelo grupo, conforme as seções 4 e 5.

## 3. 🥉 Bronze — MongoDB

Banco `pokedex_bronze`, com cinco coleções, uma por fonte:

| Coleção | Documentos | Origem |
|---|---|---|
| `pokemon` | aproximadamente 800 | uma resposta de `/pokemon/{id}` por documento |
| `especies` | 721 | uma resposta de `/pokemon-species/{id}` por documento |
| `tipos` | 21 | uma resposta de `/type/{id}` por documento |
| `pokemon_csv` | 800 | uma linha do `pokemon.csv` por documento |
| `combates` | 50.000 | uma linha do `combats.csv` por documento |

Regras da camada:

- **O documento reproduz a resposta da fonte.** São vedadas conversões de unidade, renomeações de campo, achatamento de estrutura e descarte de campos considerados dispensáveis. Qualquer transformação nesta camada elimina a possibilidade de reprocessar o silver sem nova consulta à fonte, o que anula a função da camada.
- **Todo documento registra sua linhagem**, em campos prefixados para evitar colisão com os campos da fonte. Define-se **linhagem** (*data lineage*) como o conjunto de metadados que permite rastrear a origem e o momento de obtenção de cada registro:
  ```json
  { "_id": "pokemon/25",
    "_fonte": "pokeapi",
    "_url": "https://pokeapi.co/api/v2/pokemon/25",
    "_ingerido_em": "2026-09-05T14:32:10Z",
    "id": 25, "name": "pikachu", "height": 4, "...": "..." }
  ```
- **O campo `_id` deriva da chave natural da origem**, na forma `pokemon/25`, `especie/25` ou `combate/12345`. Essa escolha reduz a carga a uma operação de `update_one` com `upsert`, o que garante a **idempotência** da etapa: define-se como idempotente a operação cuja repetição produz o mesmo estado final da primeira execução.
- **O cache em disco precede a escrita no MongoDB.** A resposta bruta deve ser gravada em `dados_brutos/pokemon/25.json`, e a API só deve ser consultada quando o arquivo correspondente não existir. São mais de 1.500 requisições, e a depuração exige múltiplas execuções do script; a ausência de cache implica sobrecarga desnecessária de um serviço gratuito. Recomenda-se intervalo de 0,1 segundo entre requisições.

O `README.md` deve justificar o uso de um banco de documentos nesta camada, em lugar de uma tabela relacional, com referência a características concretas do JSON retornado pela PokéAPI.

## 4. 🥈 Silver — PostgreSQL

Schema `silver`, modelado como esquema estrela. **Esta é a camada cujo desenho não é fornecido pelo enunciado.**

As seções anteriores prescrevem camadas, bancos, scripts e fontes. Esta seção especifica apenas os **requisitos que o modelo deve satisfazer** e uma estrutura de referência. A definição das tabelas, colunas, tipos e denominações cabe ao grupo, e constitui o item de maior peso na avaliação.

Não há solução única. Há modelos que sustentam as análises da seção 7 com consultas curtas, e modelos que convertem cada pergunta em subconsultas aninhadas. A diferença entre ambos é o objeto principal da avaliação.

### 4.1 Requisitos do modelo

- **RS1 — Grão declarado.** Define-se **grão** (*grain*) como o significado de uma linha da tabela fato. O grão adotado deve ser declarado em uma frase no `README.md`, redigida antes do primeiro comando `CREATE TABLE`.
- **RS2 — Esquema estrela.** Uma tabela fato circundada por dimensões ligadas diretamente a ela. Dimensões ligadas a outras dimensões caracterizam normalização (*snowflake*), o que contraria o propósito da camada.
- **RS3 — Chaves substitutas.** Define-se **chave substituta** (*surrogate key*) como o identificador inteiro gerado pelo próprio repositório analítico, sem significado no sistema de origem. Toda dimensão deve possuir uma. As chaves naturais das fontes — o número da Pokédex e o campo `#` do CSV — devem constar como atributos, nunca como chave primária ou estrangeira, por constituírem o registro auditável da conciliação.
- **RS4 — Ausência de chave estrangeira nula.** Toda chave estrangeira da fato deve referenciar uma linha existente. A ausência deve ser representada por **membro especial** de dimensão, definido como a linha dedicada que representa explicitamente a ausência, em lugar do valor nulo. A definição de quais dimensões requerem membros especiais, em que quantidade e sob que denominação decorre da seção 1.4.
- **RS5 — Integridade declarada no banco.** Restrições `PRIMARY KEY` em todas as dimensões e `FOREIGN KEY` em todas as chaves da fato. Uma carga inconsistente deve ser recusada pelo PostgreSQL, e não detectada posteriormente nas análises.
- **RS6 — Métricas aditivas.** As **análises 3 a 7 e a proposta pelo grupo**, que versam sobre as batalhas, devem ser obtidas por `SUM`, `AVG` e `COUNT` sobre colunas numéricas da fato. A necessidade de reprocessar texto ou de consultar outra camada para responder a uma análise indica posicionamento incorreto das métricas.

  As **análises 1 e 2 não se submetem a este requisito**: são contagens e médias sobre o cadastro, e se obtêm das dimensões. Calculá-las a partir da fato incorre em dupla contagem, uma vez que cada Pokémon comparece a cerca de 125 combates — a média de velocidade por tipo passaria a ser ponderada pelo número de combates disputados, o que responde a outra pergunta.
- **RS7 — Efetividade de tipos consultável em SQL.** O conteúdo de `damage_relations` deve ser materializado no silver de modo que a análise 6 seja respondida por operação de junção. Solução baseada em estrutura de dados interna ao script Python não satisfaz o requisito.
- **RS8 — Suficiência.** O modelo deve responder às sete análises obrigatórias e à análise proposta pelo grupo sem acesso à camada bronze, à API ou aos arquivos CSV.

### 4.2 Decisões a cargo do grupo

As decisões a seguir admitem mais de uma solução defensável, cada uma com custos distintos. Todas devem ser tomadas e justificadas no `README.md`; a justificativa é avaliada com o mesmo peso da escolha.

1. **Grão da tabela fato.** Uma linha por combate, ou uma linha por participação em um combate, o que resulta em duas linhas por combate. A primeira alternativa exige localizar cada Pokémon em duas colunas distintas, com risco de dupla contagem; a segunda reduz o cálculo da taxa de vitórias à média de uma única coluna, ao custo de duplicar o volume da fato. A escolha do grão determina a complexidade de todas as consultas subsequentes.
2. **Representação da comparação entre os lados.** A análise 5 requer a diferença de velocidade entre os dois combatentes. Essa diferença pode ser gravada como métrica na fato, calculada em tempo de consulta, ou obtida por outra estrutura. O armazenamento tem custo de espaço e de carga; o cálculo tem custo em cada consulta.
3. **Representação da efetividade de tipos.** São 324 combinações de tipo atacante e tipo defensor, das quais o JSON lista apenas as exceções, sendo 1 o valor das combinações não listadas. As alternativas incluem tabela ponte, dimensão de confronto e coluna na própria fato. A escolha deve considerar o caso em que o defensor possui dois tipos, situação na qual os multiplicadores se multiplicam.
4. **Representação do oponente.** O oponente é uma instância da mesma entidade em papel distinto. Define-se **dimensão papel** (*role-playing dimension*) como a dimensão referenciada mais de uma vez pela mesma fato, em papéis diferentes. A forma de representá-la cabe ao grupo.
5. **Localização dos atributos de status.** Os campos de pontos de vida, ataque, defesa e velocidade descrevem o Pokémon, o que sugere dimensão; a análise 5 os utiliza para comparar combatentes, o que sugere fato. A duplicação em ambos os locais é admissível, e seu custo deve ser avaliado.
6. **Derivação da categoria de raridade.** Nenhuma das fontes fornece categoria consolidada, embora ambas forneçam os indicadores booleanos de lendário, mítico e bebê. O atributo pode ser derivado na carga ou resolvido por expressão condicional em cada consulta.

### 4.3 Estrutura sugerida

A estrutura a seguir é uma referência **de alto nível**, que indica quais tabelas costumam existir e o que cada uma contém. As colunas não são especificadas, conforme a seção 4.2. O grupo pode acrescentar, consolidar, renomear ou adotar desenho distinto, desde que o resultado satisfaça os requisitos da seção 4.1 e a divergência seja justificada no `README.md`.

```
                    ┌──────────────────┐
                    │   dim_geracao    │
                    └────────┬─────────┘
                             │
  ┌──────────────┐   ┌───────┴────────┐   ┌──────────────┐
  │ dim_pokemon  ├───┤ fato_confronto ├───┤   dim_tipo   │
  └──────────────┘   └───────┬────────┘   └──────────────┘
                             │
                  ┌──────────┴──────────┐
                  │  efetividade_tipo   │
                  └─────────────────────┘
```

| Tabela | Conteúdo | Ordem de grandeza |
|---|---|---|
| `silver.fato_confronto` | o resultado dos combates: quais Pokémon se enfrentaram e qual venceu | 50.000 ou 100.000 linhas, conforme o grão adotado |
| `silver.dim_pokemon` | identificação, classificação, raridade e atributos de cadastro de cada Pokémon | aproximadamente 800, mais o membro especial |
| `silver.dim_tipo` | os tipos, com denominação legível na consulta | 18, mais o membro especial |
| `silver.dim_geracao` | as gerações, com denominação e região | 6 |
| `silver.efetividade_tipo` | o multiplicador de dano de cada tipo atacante contra cada tipo defensor | 324 |
| `silver.log_conciliacao` | opcional: o resultado da conciliação de chaves, caso o grupo prefira a tabela ao arquivo `conciliacao.csv` | 800 |

Três ressalvas quanto a esta sugestão:

- **A denominação `fato_confronto` é deliberadamente neutra** quanto ao grão. Ela não indica se uma linha representa um combate ou uma participação em combate, o que permanece como decisão 1 da seção 4.2 e determina se a tabela terá 50 mil ou 100 mil linhas.
- **A tabela `efetividade_tipo` figura como estrutura separada, o que constitui uma alternativa entre outras.** A decisão 3 permanece em aberto: são igualmente admissíveis uma tabela ponte, uma dimensão de confronto ou uma coluna na fato, com custos distintos no caso de defensor com dois tipos.
- **A tabela `log_conciliacao` não integra o esquema estrela, e é opcional.** Não é fato nem dimensão: constitui uma das duas formas de entregar o artefato de auditoria exigido pelo requisito R4, cuja forma preferida é o arquivo `conciliacao.csv`. Não é consultada por nenhuma análise. O grupo que optar pelo arquivo não precisa criá-la.

A sugestão omite deliberadamente a localização dos atributos de status e a forma de representar as diferenças entre combatentes, correspondentes às decisões 2 e 5.

Valores de referência para verificação de consistência: 50.000 combates, aproximadamente 800 Pokémon após a conciliação, 18 tipos, 6 gerações e 324 pares de tipo atacante e defensor. Uma tabela fato com 800 linhas indica que apenas o cadastro foi modelado.

## 5. 🥇 Gold — PostgreSQL

Schema `gold`. Aplica-se aqui o mesmo princípio da camada anterior: os requisitos são definidos, o desenho cabe ao grupo.

O requisito é que **cada análise final sobre as batalhas (análises 3 a 7 e a proposta pelo grupo) seja atendida por uma tabela do schema `gold`** já agregada no grão da pergunta correspondente. À consulta final não cabe agregar nem percorrer o esquema estrela: ela lê dados previamente consolidados, na forma `SELECT ... FROM gold.<tabela>`.

**As cláusulas `WHERE`, `ORDER BY` e `LIMIT` sobre a tabela do gold são permitidas**, uma vez que selecionam e ordenam sem recalcular: a análise 3 pede os dez maiores e os dez menores valores, e a análise 7 pede a identificação das posições divergentes, e ambas se resolvem por ordenação e filtro sobre dado já agregado. **Não são admitidos** `GROUP BY`, funções de agregação, junção com tabelas do schema `silver` e leitura da camada bronze; a presença de qualquer um deles indica que a agregação não foi materializada.

O corte mínimo de combates exigido pela análise 3 deve estar **materializado como coluna** na tabela do gold — a contagem de combates de cada Pokémon —, e não aplicado durante a carga. Assim o valor do corte permanece alterável na consulta, sem reexecução do pipeline, e a quantidade de combates fica disponível ao lado da taxa de vitórias, como determina a seção 1.3.

**A camada consiste em dado materializado**, produzido uma vez pelo pipeline e lido repetidamente pelas consultas finais. A troca de custo de escrita por custo de leitura é a razão de existir da camada.

A quantidade de tabelas, sua denominação e seu conteúdo decorrem das análises publicadas. A sugestão a seguir é de alto nível, com uma linha por análise atendida:

| Tabela | Grão | Análise atendida |
|---|---|---|
| `gold.ranking_pokemon` | um Pokémon | 3 — taxa de vitórias por Pokémon |
| `gold.taxa_vitorias_por_tipo` | um tipo | 4 — taxa de vitórias por tipo primário |
| `gold.taxa_vitorias_por_faixa_velocidade` | uma faixa de diferença de velocidade | 5 — efeito da velocidade |
| `gold.taxa_vitorias_por_multiplicador` | um multiplicador de efetividade | 6 — efeito da vantagem de tipo |
| `gold.matriz_confronto` | tipo atacante e tipo defensor | 7 — matriz de confronto de 18 por 18 |
| `gold.<definida pelo grupo>` | o grão da pergunta proposta | 8 — a análise proposta pelo grupo |

Consolidações de tabelas de mesmo grão são adequadas. Não é adequada uma tabela única que replique o conteúdo do silver sob outra denominação.

As análises 1 e 2 não constam da sugestão por serem verificações sobre o cadastro: residem em `consultas.sql`, diretamente sobre o silver. A camada gold atende às análises sobre as batalhas, e não é obrigatório nela materializar todos os resultados calculados.

### 5.1 A transformação do silver para o gold

Esta etapa possui script próprio, `publicar.py`, cujo funcionamento difere dos anteriores: **os dados não são transferidos para fora do banco**.

As transformações anteriores requerem Python por atravessarem fronteiras tecnológicas — de HTTP para documento, e de documento para tabela relacional. Esta transformação ocorre integralmente no PostgreSQL, na forma `INSERT INTO gold.<tabela> SELECT ... FROM silver.<tabela>`, operação de conjunto executada de modo mais eficiente pelo próprio banco.

Constitui prática inadequada, vedada por este requisito e pelo R11, transferir o conteúdo do silver para a memória do processo Python, agregá-lo ali e reinseri-lo no gold. O resultado é equivalente, mas o procedimento transporta 100.000 linhas pela rede duas vezes para executar uma agregação que o banco realiza localmente, e não é escalável.

Cabem ao `publicar.py` as responsabilidades de orquestração, não supridas pelo arquivo SQL isoladamente:

- executar `sql/gold.sql` na ordem correta de dependências;
- repovoar as tabelas do gold a partir do silver, de modo que a reexecução produza o estado correto sem duplicação de linhas, uma vez que comandos `CREATE TABLE` só se aplicam à primeira execução;
- registrar a execução, com a contagem de linhas resultante em cada tabela do gold e o momento da carga.

Essa é a distinção entre um arquivo SQL e uma etapa de pipeline: a etapa verifica o próprio resultado e admite reexecução.

O MongoDB e o PostgreSQL estão disponíveis na máquina virtual da disciplina. O grupo pode utilizá-los de qualquer forma que prefira (máquina virtual da disciplina, instalação local ou contêineres), desde que o `README.md` documente o procedimento.

## 6. Requisitos e entregáveis

- **R1 — Extração da API.** Script `extrair.py` que obtém `/pokemon`, `/pokemon-species` e `/type` para o escopo definido na seção 1.1, incluindo **todas as formas alternativas** das 721 espécies, e grava o conteúdo na camada bronze sem modificação. A segunda execução consecutiva do script deve realizar zero requisições.
- **R2 — Extração dos arquivos CSV.** O mesmo script, ou um segundo script, carrega `pokemon.csv` e `combats.csv` na camada bronze, com um documento por linha, preservando os valores como texto quando a conversão for ambígua.
- **R3 — Linhagem e idempotência.** Todo documento da camada bronze contém `_fonte`, `_url` ou caminho do arquivo, e `_ingerido_em`. A carga utiliza `upsert` com `_id` derivado da chave natural. A segunda execução não duplica documentos.
- **R4 — Conciliação de chaves.** O script `carregar.py` resolve a correspondência entre o campo `#` do CSV e o número da Pokédex por nome. A estratégia de normalização deve ser descrita no `README.md`, acompanhada de um **relatório de conciliação** contendo a quantidade de registros conciliados, a relação dos não conciliados e o tratamento aplicado a estes. A forma preferida é o arquivo `conciliacao.csv`, versionado no repositório; o grupo que preferir pode entregá-lo como a tabela `silver.log_conciliacao`, descrita na seção 4.3. Uma das duas formas basta. O descarte silencioso de registros não conciliados não é admitido.
- **R5 — Modelagem do silver.** O modelo dimensional satisfaz os requisitos RS1 a RS8 da seção 4.1, e as seis decisões da seção 4.2 estão tomadas e justificadas no `README.md`. Nenhum combate pode ser omitido: as batalhas do registro sem nome, descrito na seção 1.4, permanecem no modelo, representadas por membro especial. Entram 50.000 combates e permanecem 50.000 combates.
- **R6 — DDL versionado.** O arquivo `sql/silver.sql` contém os comandos `CREATE TABLE` de todo o modelo, com as restrições `PRIMARY KEY` e `FOREIGN KEY` declaradas, e é executado pelo `carregar.py`, nos termos do R7. O modelo constitui artefato do repositório, e não estado exclusivo do banco de quem o executou.
- **R7 — Carga do silver.** Script `carregar.py` que **executa o `sql/silver.sql`**, lê da camada bronze — e não dos arquivos ou da API —, transforma e popula o PostgreSQL. Cabem-lhe as mesmas responsabilidades de orquestração que a seção 5.1 atribui ao `publicar.py`: executar o DDL respeitando a ordem de dependências entre as tabelas, uma vez que as dimensões precisam existir antes da fato que as referencia; repovoar de modo que a reexecução produza o estado correto sem duplicar linhas; e registrar, ao final, a contagem de linhas de cada tabela. Idempotente. A necessidade de qualquer informação ausente da camada bronze indica que a extração está incompleta.
- **R8 — Publicação do gold.** Arquivo `sql/gold.sql` criando o schema e as tabelas agregadas, e script `publicar.py` que o executa, repovoa as tabelas a partir do silver e registra o resultado da execução. A agregação ocorre no banco, nos termos da seção 5.1. Idempotente.
- **R9 — Análises finais.** Arquivo `sql/consultas.sql` com as oito análises da seção 7: as análises 1 e 2 sobre o schema `silver`; as análises 3 a 7 e a proposta pelo grupo como leitura direta das tabelas do schema `gold`, sem agregação nem junção na consulta final.
- **R10 — Documentação.** Arquivos `README.md` e `RELATORIO.md`, com o conteúdo exigido na seção *Documentação*.
- **R11 — Restrição de bibliotecas.** **O uso de `pandas` não é permitido em nenhuma etapa deste trabalho**, nem de qualquer outra biblioteca de manipulação de dados tabulares em memória, como `polars`, `numpy` ou `pyarrow`. São admitidos um cliente HTTP (`requests` ou `httpx`), os drivers de banco (`pymongo` e `psycopg`) e a biblioteca padrão do Python, na qual os módulos `csv` e `json` bastam para ler as fontes deste trabalho. Bibliotecas não previstas aqui devem ser justificadas no `README.md`, e o `requirements.txt` deve listar exatamente o que o pipeline utiliza.

  A restrição decorre do objeto da disciplina. Um `DataFrame` intermediário entre a fonte e o banco desloca para a memória do processo Python o trabalho que cabe a cada camada: a conciliação de chaves vira um `merge`, a agregação vira um `groupby`, e o modelo dimensional deixa de ser exercitado. O que se avalia aqui é a modelagem e o uso dos dois bancos, não a manipulação de estruturas tabulares em memória.

### Verificação na correção

A avaliação não se restringe à leitura do código. Os seguintes procedimentos serão executados:

- **Os três scripts serão executados duas vezes consecutivas**, na ordem, a partir de instâncias vazias do MongoDB e do PostgreSQL. A segunda execução não pode duplicar documentos ou linhas, nem realizar requisições à PokéAPI.
- **O script `carregar.py` será executado sem acesso à rede.** A falha nessa condição demonstra que o script consulta a API ou os arquivos originais, e não a camada bronze.
- **Uma das perguntas das análises 3 a 7 será formulada** e deve ser respondida por leitura de uma única tabela do schema `gold`, nos termos da seção 5: `WHERE`, `ORDER BY` e `LIMIT` são admitidos; `GROUP BY`, funções de agregação e junção com o `silver` indicam que a camada gold não cumpre sua função.
- **As dependências e as importações dos três scripts serão inspecionadas**, para verificar o cumprimento do R11. A presença de `pandas` ou equivalente, no `requirements.txt` ou em qualquer `import`, caracteriza descumprimento de requisito.
- **Será formulada ao modelo uma pergunta de negócio não constante deste enunciado**, de natureza equivalente às oito análises. Ela deve ser respondível por uma consulta sobre o schema `silver`, sem alteração do esquema, sem acesso à camada bronze e sem releitura dos arquivos CSV. Um modelo dimensional destina-se a responder perguntas não previstas em sua construção.

## 7. Análises

São exigidas sete análises especificadas e uma proposta pelo grupo.

Todas devem ser respondidas exclusivamente por SQL, sem acesso aos documentos JSON ou aos arquivos CSV: as análises 1 e 2 sobre o schema `silver`; as análises 3 a 7 e a proposta pelo grupo como leitura direta das tabelas do schema `gold` (seção 5). As consultas devem ser entregues em `consultas.sql`, comentadas, e devolver resultados legíveis, com denominações em lugar de identificadores.

O enunciado especifica o que cada análise deve responder. A forma de obtê-la a partir do modelo projetado integra o trabalho; dificuldade excessiva em alguma delas indica problema na modelagem da seção 4, e não na consulta.

### Análises sobre o cadastro

1. **Quantidade de Pokémon por tipo primário e por geração**, em matriz de tipo por geração. Serve como verificação de consistência: divergências em relação ao esperado indicam falha na conciliação.
2. **Média de cada atributo de status por tipo primário.** Identificar o tipo de maior velocidade média, o de maior resistência média, e verificar a existência de tipo com desempenho superior em todos os atributos.

### Análises sobre as batalhas

3. **Taxa de vitórias por Pokémon**, com os dez maiores e os dez menores valores. São 50.000 combates distribuídos de forma não uniforme entre aproximadamente 800 Pokémon. Um Pokémon com três combates e 100% de vitórias representa ruído estatístico, e não desempenho superior. Deve-se estabelecer um número mínimo de combates, justificar o corte adotado e apresentar a quantidade de combates ao lado da taxa de vitórias.
4. **Taxa de vitórias por tipo primário**, ordenada. Verificar a existência de tipo dominante.
5. **Relação entre diferença de velocidade e vitória.** Agrupar os confrontos por faixa de diferença de velocidade entre os combatentes e calcular a taxa de vitórias de cada faixa. A quantidade de faixas e a posição dos cortes cabem ao grupo, e devem ser justificadas, uma vez que alteram a leitura do resultado.
6. **Relação entre vantagem de tipo e vitória.** Cruzar o resultado dos confrontos com a efetividade obtida da PokéAPI e calcular a taxa de vitórias por multiplicador, com uma linha por multiplicador existente no modelo. Os valores possíveis são 0, 0,5, 1 e 2 quando se considera apenas o tipo primário do defensor, e 0, 0,25, 0,5, 1, 2 e 4 quando se consideram seus dois tipos, conforme a distinção da seção 1.1. Qual dos dois critérios foi adotado decorre da decisão 3 da seção 4.2 e deve ser declarado no `README.md`. Caso a mecânica do jogo esteja refletida nos dados, os multiplicadores superiores a 1 devem corresponder a taxa de vitórias significativamente superior; **um resultado que não mostre esse efeito, corretamente apurado e interpretado, constitui resposta válida** — as batalhas são simuladas, e o programa que as produziu pode não ter empregado a tabela de tipos. Esta análise verifica a decisão 3 da seção 4.2: sem modelagem da efetividade, ela não é obtenível em SQL.
7. **Matriz de confronto entre tipos**, de 18 por 18 posições, com a taxa de vitórias do tipo atacante contra o tipo defensor e o multiplicador de efetividade correspondente. Identificar as posições em que os dois valores divergem.

   A orientação da matriz decorre do **vencedor**, e não da coluna `First_pokemon`: a célula (A, B) contém a proporção dos confrontos entre um Pokémon de tipo primário A e um de tipo primário B que foram vencidos pelo de tipo A. Cada confronto alimenta duas células — como vitória em (A, B) e como derrota em (B, A) —, de modo que as duas somam 1. Quem atacou primeiro não participa desta análise.

### Análise proposta pelo grupo

Além das sete análises especificadas, o grupo deve propor e entregar uma oitava análise, de tema livre, observadas as seguintes condições:

- Responde a uma **pergunta de negócio formulada pelo grupo**, registrada no `RELATORIO.md` antes da consulta que a responde.
- Não constitui variação das análises obrigatórias. A substituição de tipo primário por geração na análise 4, ou o uso da diferença de ataque em lugar da diferença de velocidade na análise 5, caracteriza o mesmo exercício com outra coluna e não satisfaz o requisito.
- **Exige do modelo alguma capacidade não exigida pelas sete anteriores**: dimensão não utilizada pelas demais, cruzamento não realizado pelas demais, ou grão distinto. O `README.md` deve identificar essa capacidade.
- Possui tabela própria no schema `gold`.
- É acompanhada de interpretação no `RELATORIO.md`.

Esta análise verifica se o modelo projetado constitui de fato um modelo dimensional, e não uma estrutura ajustada às sete perguntas do enunciado. A necessidade de retornar à camada bronze, reabrir um arquivo CSV ou alterar o esquema do silver para respondê-la indica insuficiência do desenho da seção 4.

As fontes contêm atributos não explorados pelas sete análises obrigatórias: habitat, cor, forma, taxa de captura, felicidade base, altura, peso, grupos de ovo, quantidade de habilidades e a distinção entre forma alternativa e forma padrão. O arquivo `combats.csv` registra ainda qual combatente atacou primeiro, informação não explorada em profundidade pelas análises obrigatórias.

## Documentação

Todo o conteúdo exigido por este enunciado deve estar documentado no repositório. A ausência de documentação implica desconto na avaliação.

O `README.md` deve cobrir, no mínimo:

- **O procedimento de execução do pipeline**, incluindo a inicialização do MongoDB e do PostgreSQL, e a ordem de execução dos scripts.
- **A justificativa do uso de banco de documentos na camada bronze**, com referência a características concretas do JSON retornado pela PokéAPI.
- **A frase do grão** da tabela fato e o diagrama do esquema estrela projetado.
- **As seis decisões de modelagem da seção 4.2**, individualmente, com a justificativa de cada escolha e seus custos.
- **A análise proposta pelo grupo**: a pergunta formulada, sua relevância, e a capacidade do modelo que ela exige e que as sete análises obrigatórias não exigem.
- **O procedimento que garante a idempotência** em cada camada.
- **As decisões de projeto** não especificadas pelo enunciado, com a justificativa de cada uma.

O `RELATORIO.md` documenta a leitura dos resultados, e deve conter:

- **O resultado de cada uma das oito análises** (a saída da consulta, em tabela Markdown ou captura de tela).
- **A interpretação de cada uma das oito análises**, quanto ao que o resultado informa sobre o fenômeno.

A documentação deve ser específica do projeto entregue. Um `README.md` genérico, aplicável a qualquer pipeline equivalente e sem referência às decisões tomadas pelo grupo, é considerado ausência de documentação.

## Formato e forma de entrega

**O único entregável é a URL de um repositório público no GitHub.** Não são aceitos arquivos, anexos ou documentos em PDF enviados por outros meios: todo o material avaliado deve estar versionado no repositório.

**Uma entrega por grupo.** O grupo seleciona um repositório e submete uma única URL. Submissões repetidas por integrantes distintos são contadas como uma só, considerando-se a primeira recebida.

**O `README.md` deve iniciar com o nome completo de todos os integrantes do grupo.** Integrantes não listados no `README.md` não recebem nota por este trabalho, ainda que constem do histórico de commits do repositório.

Estrutura esperada do repositório:

```
repositorio/
├── README.md                     # integrantes e a documentação exigida
├── RELATORIO.md                  # resultados e interpretação das análises
├── requirements.txt
├── extrair.py                    # fontes  -> bronze  (MongoDB)
├── carregar.py                   # bronze  -> silver  (PostgreSQL)
├── publicar.py                   # silver  -> gold    (PostgreSQL)
├── sql/
│   ├── silver.sql                # DDL do modelo projetado
│   ├── gold.sql                  # schema e tabelas agregadas
│   └── consultas.sql             # as sete análises e a proposta pelo grupo
├── conciliacao.csv               # relatório de conciliação exigido por R4
│                                 # (dispensável se entregue como silver.log_conciliacao)
└── dados_brutos/                 # cache local, não versionado (.gitignore)
```

O diretório `dados_brutos/` é excluído do versionamento por conter cache reconstituível automaticamente. Repositórios não se destinam a armazenar os arquivos JSON obtidos da API, que podem ser novamente requisitados.

## Política de IA generativa e antiplágio

Este trabalho está sujeito às seguintes políticas:

- [Política de uso de ferramentas generativas de IA](https://crivelaro.notion.site/Pol-tica-de-uso-de-ferramentas-generativas-de-IA-1b53bb4e12a54b4aa06eaa02e62192f4?pvs=74)
- [Política antiplágio](https://crivelaro.notion.site/Pol-tica-antipl-gio-5187d7b1ab514bfb8424ac0fcfb59dba?pvs=74)

## Conceitos-chave

Arquitetura medalhão, linhagem, idempotência, chave natural, chave substituta, grão, dimensão conformada, dimensão papel, dimensão degenerada, membro especial, tabela ponte, camada de consumo.

## Referências

Arquitetura medalhão e camadas de dados:

- [What is a Medallion Architecture? — Databricks](https://www.databricks.com/glossary/medallion-architecture)
- KIMBALL, R.; ROSS, M. *The Data Warehouse Toolkit*, 3ª ed. Wiley, 2013. Capítulos 1 a 3, sobre grão, fatos e dimensões, e 19, sobre ETL.
- INMON, W. H. *Building the Data Warehouse*, 4ª ed. Wiley, 2005. Capítulo 1.

Fontes de dados:

- [PokéAPI — documentação da v2](https://pokeapi.co/docs/v2)
- [cdiener/pokemon_app — espelho sob licença MIT dos arquivos CSV de batalha](https://github.com/cdiener/pokemon_app)
- [Pokémon: Weedle's Cave — conjunto de dados original, no Kaggle](https://www.kaggle.com/datasets/terminus7/pokemon-challenge)

Ferramentas:

- [MongoDB — updateOne e a opção upsert](https://www.mongodb.com/docs/manual/reference/method/db.collection.updateOne/)
- [PostgreSQL — Identity Columns](https://www.postgresql.org/docs/current/sql-createtable.html)
