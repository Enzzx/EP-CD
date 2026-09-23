-- ============================================================================
-- Consultas finais — EP01
-- Análises 1 e 2 sobre o schema silver (cadastro)
-- Análises 3 a 8 como leitura direta das tabelas do schema gold
-- (sem GROUP BY, funções de agregação ou junção com silver nas consultas finais)
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Análise 1 — Quantidade de Pokémon por tipo primário e por geração (matriz)
-- (verificação de consistência da conciliação)
-- ---------------------------------------------------------------------------
SELECT
    t.nome_tipo                   AS tipo_primario,
    g.id_geracao                  AS geracao,
    COUNT(p.sk_pokemon)           AS qtd_pokemon
FROM silver.dim_pokemon p
JOIN silver.dim_tipo t            ON t.sk_tipo = p.sk_tipo_primario
JOIN silver.dim_geracao g         ON g.sk_geracao = p.sk_geracao
GROUP BY t.nome_tipo, g.id_geracao
ORDER BY t.nome_tipo, g.id_geracao;

-- ---------------------------------------------------------------------------
-- Análise 2 — Média de cada atributo de status por tipo primário
-- ---------------------------------------------------------------------------
SELECT
    t.nome_tipo                   AS tipo_primario,
    ROUND(AVG(p.hp_base)::numeric, 2)                 AS hp_medio,
    ROUND(AVG(p.ataque_base)::numeric, 2)             AS ataque_medio,
    ROUND(AVG(p.defesa_base)::numeric, 2)             AS defesa_medio,
    ROUND(AVG(p.ataque_especial_base)::numeric, 2)    AS sp_atk_medio,
    ROUND(AVG(p.defesa_especial_base)::numeric, 2)    AS sp_def_medio,
    ROUND(AVG(p.velocidade_base)::numeric, 2)         AS velocidade_medio,
    COUNT(p.sk_pokemon)           AS qtd_pokemon
FROM silver.dim_pokemon p
JOIN silver.dim_tipo t            ON t.sk_tipo = p.sk_tipo_primario
GROUP BY t.nome_tipo
ORDER BY velocidade_medio DESC;

-- Tipo de maior velocidade média
SELECT t.nome_tipo, ROUND(AVG(p.velocidade_base)::numeric,2) AS velocidade_medio
FROM silver.dim_pokemon p
JOIN silver.dim_tipo t ON t.sk_tipo = p.sk_tipo_primario
GROUP BY t.nome_tipo
ORDER BY velocidade_medio DESC
LIMIT 1;

-- Tipo de maior resistência média
SELECT t.nome_tipo, ROUND(AVG(p.defesa_base)::numeric,2) AS defesa_medio
FROM silver.dim_pokemon p
JOIN silver.dim_tipo t ON t.sk_tipo = p.sk_tipo_primario
GROUP BY t.nome_tipo
ORDER BY defesa_medio DESC
LIMIT 1;

-- ---------------------------------------------------------------------------
-- Análise 3 — Taxa de vitórias por Pokémon (top e bottom 10)
-- Corte mínimo de 30 combates: com ~125 combates médios por Pokémon, 30 já
-- representam ~24% da amostra e garantem erro relativo aceitável;
-- 100% em poucos combates é ruído estatístico e seria eliminado pelo filtro.
-- O corte é aplicado na consulta (coluna materializada), não na carga.
-- ---------------------------------------------------------------------------
-- Top 10
SELECT nome_pokemon, numero_pokedex, taxa_vitorias, qtd_combates
FROM gold.ranking_pokemon
WHERE qtd_combates >= 30
ORDER BY taxa_vitorias DESC, qtd_combates DESC
LIMIT 10;

-- Bottom 10
SELECT nome_pokemon, numero_pokedex, taxa_vitorias, qtd_combates
FROM gold.ranking_pokemon
WHERE qtd_combates >= 30
ORDER BY taxa_vitorias ASC, qtd_combates DESC
LIMIT 10;

-- ---------------------------------------------------------------------------
-- Análise 4 — Taxa de vitórias por tipo primário (ordenada)
-- ---------------------------------------------------------------------------
SELECT nome_tipo, taxa_vitorias, qtd_combates
FROM gold.taxa_vitorias_por_tipo
ORDER BY taxa_vitorias DESC;

-- ---------------------------------------------------------------------------
-- Análise 5 — Relação entre diferença de velocidade e vitória (por faixa)
-- Faixas simétricas do desvio da velocidade (combatente - oponente).
-- ---------------------------------------------------------------------------
SELECT faixa_velocidade, taxa_vitorias, qtd_confrontos
FROM gold.taxa_vitorias_por_faixa_velocidade
ORDER BY ordem_faixa;

-- ---------------------------------------------------------------------------
-- Análise 6 — Relação entre vantagem de tipo e vitória (por multiplicador)
-- Multiplicador efetivo: considera os dois tipos do defensor (0, 0.25, 0.5, 1, 2, 4).
-- ---------------------------------------------------------------------------
SELECT multiplicador, taxa_vitorias_atacante, qtd_confrontos
FROM gold.taxa_vitorias_por_multiplicador
ORDER BY multiplicador;

-- ---------------------------------------------------------------------------
-- Análise 7 — Matriz de confronto 18x18 (taxa de vitórias x efetividade)
-- Célula (A,B) = proporção de confrontos entre tipo primário A (atacante)
-- e tipo primário B (defensor) vencidos pelo de tipo A.
-- ---------------------------------------------------------------------------
SELECT
    nome_tipo_atacante            AS tipo_atacante,
    nome_tipo_defensor            AS tipo_defensor,
    taxa_vitorias_atacante,
    multiplicador_efetividade,
    qtd_confrontos
FROM gold.matriz_confronto
ORDER BY nome_tipo_atacante, nome_tipo_defensor;

-- Posições em que a taxa de vitórias diverge do multiplicador esperado
-- (vantagem declarada pela tabela de tipos, mas derrota na prática, e vice-versa)
SELECT
    nome_tipo_atacante            AS tipo_atacante,
    nome_tipo_defensor            AS tipo_defensor,
    multiplicador_efetividade,
    taxa_vitorias_atacante,
    qtd_confrontos
FROM gold.matriz_confronto
WHERE (multiplicador_efetividade > 1 AND taxa_vitorias_atacante < 0.5)
   OR (multiplicador_efetividade < 1 AND taxa_vitorias_atacante > 0.5)
ORDER BY nome_tipo_atacante, nome_tipo_defensor;

-- ---------------------------------------------------------------------------
-- Análise 8 — Proposta do grupo: relação entre habitat e taxa de vitórias
-- Pergunta de negócio: o habitat de origem da espécie influencia o desempenho
-- em batalhas simuladas?
-- A análise exige a dimensão dim_habitat (não usada pelas análises 1-7),
-- herdada de espécie para as formas alternativas.
-- ---------------------------------------------------------------------------
SELECT nome_habitat, taxa_vitorias, qtd_combates
FROM gold.taxa_vitorias_por_habitat
ORDER BY taxa_vitorias DESC, qtd_combates DESC;