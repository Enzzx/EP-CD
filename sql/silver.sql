-- Modelo dimensional da camada Silver (esquema estrela)
-- Grão da fato: uma participação de um Pokémon em um combate (2 linhas por combate).
--
-- RS2: todas as dimensões ligam-se DIRETAMENTE à fato_confronto.
-- dim_pokemon é a única dimensão de cadastro; seus atributos descritivos
-- (tipo, geração, habitat, cor, forma corporal, taxa de crescimento) são
-- armazenados como colunas de texto, sem FK para outras dimensões (sem snowflake).
-- As mesmas dimensões de atributo também se ligam à fato, para os agregados de
-- batalha, conforme a estrutura de referência da seção 4.3 do enunciado.

CREATE SCHEMA IF NOT EXISTS silver;

-- Dimensão geração
CREATE TABLE IF NOT EXISTS silver.dim_geracao (
  sk_geracao             SERIAL PRIMARY KEY,
  id_geracao             INT UNIQUE NOT NULL,
  descricao_geracao      TEXT,
  regiao                 TEXT
);

-- Dimensão habitat (atributo de espécie; conceito inaplicável após as gerações I-III)
CREATE TABLE IF NOT EXISTS silver.dim_habitat (
  sk_habitat             SERIAL PRIMARY KEY,
  nome_habitat           TEXT UNIQUE NOT NULL,
  observacao             TEXT
);

-- Dimensão cor
CREATE TABLE IF NOT EXISTS silver.dim_cor (
  sk_cor                 SERIAL PRIMARY KEY,
  nome_cor               TEXT UNIQUE NOT NULL
);

-- Dimensão forma corporal
CREATE TABLE IF NOT EXISTS silver.dim_forma_corporal (
  sk_forma_corporal      SERIAL PRIMARY KEY,
  nome_forma_corporal    TEXT UNIQUE NOT NULL
);

-- Dimensão taxa de crescimento
CREATE TABLE IF NOT EXISTS silver.dim_taxa_crescimento (
  sk_taxa_crescimento    SERIAL PRIMARY KEY,
  nome_taxa_crescimento  TEXT UNIQUE NOT NULL
);

-- Dimensão tipo
CREATE TABLE IF NOT EXISTS silver.dim_tipo (
  sk_tipo                SERIAL PRIMARY KEY,
  id_tipo_api            INT UNIQUE,
  nome_tipo              TEXT UNIQUE NOT NULL,
  eh_valido_jogo         BOOLEAN NOT NULL DEFAULT TRUE,
  eh_membro_especial     BOOLEAN NOT NULL DEFAULT FALSE
);

-- Dimensão Pokémon (cadastro completo; atributos de espécie desnormalizados
-- como texto, para as análises 1 e 2 sobre o cadastro — RS6)
CREATE TABLE IF NOT EXISTS silver.dim_pokemon (
  sk_pokemon             SERIAL PRIMARY KEY,
  numero_pokedex         INT,
  id_api                 INT,
  id_csv                 INT UNIQUE,
  nome_original          TEXT,
  nome_api               TEXT,
  eh_forma_alternativa   BOOLEAN DEFAULT FALSE,
  eh_padrao              BOOLEAN DEFAULT TRUE,
  pokemon_pai_pokedex    INT,
  -- atributos descritivos (cópias legíveis, sem FK; evitam snowflake)
  tipo_primario          TEXT,
  tipo_secundario        TEXT,
  geracao                INT,
  habitat                TEXT,
  cor                    TEXT,
  forma_corporal         TEXT,
  taxa_crescimento       TEXT,
  -- atributos físicos e de status
  altura                 NUMERIC(8,3),
  peso                   NUMERIC(8,3),
  experiencia_base       INT,
  hp_base                INT,
  ataque_base            INT,
  defesa_base            INT,
  ataque_especial_base   INT,
  defesa_especial_base   INT,
  velocidade_base        INT,
  taxa_captura           INT,
  felicidade_base        INT,
  eh_lendario            BOOLEAN DEFAULT FALSE,
  eh_mitico              BOOLEAN DEFAULT FALSE,
  eh_bebe                BOOLEAN DEFAULT FALSE,
  categoria_raridade     TEXT,
  eh_membro_especial     BOOLEAN DEFAULT FALSE,
  _fonte                 TEXT,
  _ingerido_em           TIMESTAMPTZ
);

-- Matriz de efetividade de tipos (multiplicador do tipo atacante contra o defensor).
-- Estrutura de apoio (factless fact), chaveada por dim_tipo; insumo das análises 6 e 7.
CREATE TABLE IF NOT EXISTS silver.efetividade_tipo (
  sk_efetividade         SERIAL PRIMARY KEY,
  sk_tipo_atacante       INT NOT NULL REFERENCES silver.dim_tipo(sk_tipo),
  sk_tipo_defensor       INT NOT NULL REFERENCES silver.dim_tipo(sk_tipo),
  multiplicador          NUMERIC(4,2) NOT NULL,
  UNIQUE (sk_tipo_atacante, sk_tipo_defensor)
);

-- Tabela fato: uma linha por participação de um Pokémon em um combate.
-- Todas as dimensões ligam-se diretamente a esta tabela (esquema estrela — RS2).
CREATE TABLE IF NOT EXISTS silver.fato_confronto (
  sk_fato_confronto           SERIAL PRIMARY KEY,
  id_combate                  INT NOT NULL,
  lado                        SMALLINT NOT NULL CHECK (lado IN (1,2)),
  sk_pokemon                  INT NOT NULL REFERENCES silver.dim_pokemon(sk_pokemon),
  sk_oponente                 INT NOT NULL REFERENCES silver.dim_pokemon(sk_pokemon),
  sk_tipo_primario            INT NOT NULL REFERENCES silver.dim_tipo(sk_tipo),
  sk_tipo_secundario          INT NOT NULL REFERENCES silver.dim_tipo(sk_tipo),
  sk_tipo_primario_oponente   INT NOT NULL REFERENCES silver.dim_tipo(sk_tipo),
  sk_tipo_secundario_oponente INT NOT NULL REFERENCES silver.dim_tipo(sk_tipo),
  sk_geracao                  INT NOT NULL REFERENCES silver.dim_geracao(sk_geracao),
  sk_habitat                  INT NOT NULL REFERENCES silver.dim_habitat(sk_habitat),
  sk_cor                      INT NOT NULL REFERENCES silver.dim_cor(sk_cor),
  sk_forma_corporal           INT NOT NULL REFERENCES silver.dim_forma_corporal(sk_forma_corporal),
  sk_taxa_crescimento         INT NOT NULL REFERENCES silver.dim_taxa_crescimento(sk_taxa_crescimento),
  venceu                      SMALLINT NOT NULL CHECK (venceu IN (0,1)),
  atacou_primeiro             SMALLINT NOT NULL CHECK (atacou_primeiro IN (0,1)),
  velocidade_combatente       INT,
  velocidade_oponente         INT,
  diff_velocidade             INT,
  UNIQUE (id_combate, lado)
);

-- Log de conciliação (auditoria R4; complementa conciliacao.csv)
CREATE TABLE IF NOT EXISTS silver.log_conciliacao (
  id_log                 SERIAL PRIMARY KEY,
  pokemon_id_csv         INT,
  numero_pokedex         INT,
  nome_csv               TEXT,
  nome_api               TEXT,
  formato                TEXT,
  observacao             TEXT,
  conciliado             BOOLEAN DEFAULT FALSE
);
