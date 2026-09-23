-- Camada Gold: agregados materializados no grão das perguntas de negócio (análises 3-8).

CREATE SCHEMA IF NOT EXISTS gold;

-- Análise 3 — taxa de vitórias por Pokémon (grão: um Pokémon)
CREATE TABLE IF NOT EXISTS gold.ranking_pokemon (
  sk_pokemon            INT PRIMARY KEY,
  nome_pokemon          TEXT,
  numero_pokedex        INT,
  taxa_vitorias         NUMERIC(7,5),
  qtd_combates          INT
);

-- Análise 4 — taxa de vitórias por tipo primário (grão: um tipo)
CREATE TABLE IF NOT EXISTS gold.taxa_vitorias_por_tipo (
  sk_tipo               INT PRIMARY KEY,
  nome_tipo             TEXT,
  taxa_vitorias         NUMERIC(7,5),
  qtd_combates          INT
);

-- Análise 5 — taxa de vitórias por faixa de diferença de velocidade (grão: uma faixa)
CREATE TABLE IF NOT EXISTS gold.taxa_vitorias_por_faixa_velocidade (
  faixa_velocidade      TEXT PRIMARY KEY,
  ordem_faixa           INT NOT NULL,
  limite_inferior       INT,
  limite_superior       INT,
  taxa_vitorias         NUMERIC(7,5),
  qtd_confrontos        INT
);

-- Análise 6 — taxa de vitórias por multiplicador de efetividade (grão: um multiplicador)
CREATE TABLE IF NOT EXISTS gold.taxa_vitorias_por_multiplicador (
  multiplicador         NUMERIC(4,2) PRIMARY KEY,
  taxa_vitorias_atacante NUMERIC(7,5),
  qtd_confrontos        INT
);

-- Análise 7 — matriz de confronto 18×18 (grão: par tipo atacante x tipo defensor)
CREATE TABLE IF NOT EXISTS gold.matriz_confronto (
  sk_tipo_atacante      INT NOT NULL,
  sk_tipo_defensor      INT NOT NULL,
  nome_tipo_atacante    TEXT,
  nome_tipo_defensor    TEXT,
  taxa_vitorias_atacante NUMERIC(7,5),
  multiplicador_efetividade NUMERIC(4,2),
  qtd_confrontos        INT,
  PRIMARY KEY (sk_tipo_atacante, sk_tipo_defensor)
);

-- Análise 8 (proposta) — taxa de vitórias por habitat (grão: um habitat)
CREATE TABLE IF NOT EXISTS gold.taxa_vitorias_por_habitat (
  sk_habitat            INT PRIMARY KEY,
  nome_habitat          TEXT,
  taxa_vitorias         NUMERIC(7,5),
  qtd_combates          INT
);

-- Registro de execução da camada gold
CREATE TABLE IF NOT EXISTS gold.log_publicacao (
  id_log                SERIAL PRIMARY KEY,
  tabela                TEXT NOT NULL,
  qtd_linhas            INT NOT NULL,
  carregado_em          TIMESTAMPTZ NOT NULL DEFAULT now()
);