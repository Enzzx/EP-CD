#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publicar.py — Camada Silver (PostgreSQL) -> Camada Gold (PostgreSQL)
· Executa sql/gold.sql (DDL versionado)
· Repovoa as tabelas gold a partir do silver, integralmente no banco
  (INSERT INTO gold.<tabela> SELECT ... FROM silver.<tabela>)
· Registra a contagem de linhas e o momento de cada carga (idempotente)
"""
import os
from pathlib import Path

import psycopg

BASE_DIR = Path(__file__).resolve().parent

PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("PG_PORT", "5432"))
PG_DB = os.getenv("PG_DB", "pokedex")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "postgres")


def conectar():
    return psycopg.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASSWORD)


def exec_sql_file(conn, path):
    sql = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)


MATERIALIZACOES = {
    # Análise 3 — taxa de vitórias por Pokémon (grão: Pokémon)
    "gold.ranking_pokemon": """
        WITH participantes AS (
            SELECT sk_pokemon, SUM(venceu) AS vitorias, COUNT(*) AS combates
            FROM silver.fato_confronto
            GROUP BY sk_pokemon
        )
        SELECT p.sk_pokemon, d.nome_original, d.numero_pokedex,
               ROUND(vitorias::numeric / combates, 5), combates
        FROM participantes p
        JOIN silver.dim_pokemon d ON d.sk_pokemon = p.sk_pokemon;
    """,
    # Análise 4 — taxa de vitórias por tipo primário (grão: tipo)
    "gold.taxa_vitorias_por_tipo": """
        SELECT t.sk_tipo, t.nome_tipo,
               ROUND(AVG(f.venceu::numeric), 5) AS taxa,
               COUNT(*) AS combates
        FROM silver.fato_confronto f
        JOIN silver.dim_tipo t ON t.sk_tipo = f.sk_tipo_primario
        GROUP BY t.sk_tipo, t.nome_tipo;
    """,
    # Análise 5 — taxa de vitórias por faixa de diferença de velocidade (grão: faixa)
    "gold.taxa_vitorias_por_faixa_velocidade": """
        WITH bandas AS (
            SELECT
                CASE
                    WHEN f.diff_velocidade <= -81 THEN '<-80'
                    WHEN f.diff_velocidade <= -41 THEN '[-80,-41]'
                    WHEN f.diff_velocidade <= -1  THEN '[-40,-1]'
                    WHEN f.diff_velocidade = 0    THEN '=0'
                    WHEN f.diff_velocidade <= 40  THEN '[1,40]'
                    WHEN f.diff_velocidade <= 80  THEN '[41,80]'
                    ELSE                               '>80'
                END AS faixa,
                CASE
                    WHEN f.diff_velocidade <= -81 THEN 1
                    WHEN f.diff_velocidade <= -41 THEN 2
                    WHEN f.diff_velocidade <= -1  THEN 3
                    WHEN f.diff_velocidade = 0    THEN 4
                    WHEN f.diff_velocidade <= 40  THEN 5
                    WHEN f.diff_velocidade <= 80  THEN 6
                    ELSE                               7
                END AS ordem,
                CASE
                    WHEN f.diff_velocidade <= -81 THEN NULL
                    WHEN f.diff_velocidade <= -41 THEN -80
                    WHEN f.diff_velocidade <= -1  THEN -40
                    WHEN f.diff_velocidade = 0    THEN 0
                    WHEN f.diff_velocidade <= 40  THEN 1
                    WHEN f.diff_velocidade <= 80  THEN 41
                    ELSE                               81
                END AS li,
                CASE
                    WHEN f.diff_velocidade <= -81 THEN -81
                    WHEN f.diff_velocidade <= -41 THEN -41
                    WHEN f.diff_velocidade <= -1  THEN -1
                    WHEN f.diff_velocidade = 0    THEN 0
                    WHEN f.diff_velocidade <= 40  THEN 40
                    WHEN f.diff_velocidade <= 80  THEN 80
                    ELSE                               NULL
                END AS ls,
                f.venceu
            FROM silver.fato_confronto f
            WHERE f.diff_velocidade IS NOT NULL
        )
        SELECT faixa, ordem, li, ls,
               ROUND(AVG(venceu::numeric), 5), COUNT(*)
        FROM bandas
        GROUP BY faixa, ordem, li, ls
        ORDER BY ordem;
    """,
    # Análise 6 — taxa de vitórias por multiplicador de efetividade (grão: multiplicador)
    "gold.taxa_vitorias_por_multiplicador": """
        WITH mult AS (
            SELECT f.venceu,
                   ef1.multiplicador * COALESCE(ef2.multiplicador, 1) AS multiplicador
            FROM silver.fato_confronto f
            JOIN silver.efetividade_tipo ef1
                 ON ef1.sk_tipo_atacante = f.sk_tipo_primario
                AND ef1.sk_tipo_defensor = f.sk_tipo_primario_oponente
            LEFT JOIN silver.efetividade_tipo ef2
                 ON ef2.sk_tipo_atacante = f.sk_tipo_primario
                AND ef2.sk_tipo_defensor = f.sk_tipo_secundario_oponente
        )
        SELECT multiplicador,
               ROUND(AVG(venceu::numeric), 5) AS taxa,
               COUNT(*) AS qtd
        FROM mult
        GROUP BY multiplicador
        ORDER BY multiplicador;
    """,
    # Análise 7 — matriz de confronto 18x18 (grão: par tipo atacante x tipo defensor)
    "gold.matriz_confronto": """
        SELECT f.sk_tipo_primario, f.sk_tipo_primario_oponente,
               t1.nome_tipo, t2.nome_tipo,
               ROUND(AVG(f.venceu::numeric), 5) AS taxa,
               ef.multiplicador,
               COUNT(*) AS qtd
        FROM silver.fato_confronto f
        JOIN silver.dim_tipo t1 ON t1.sk_tipo = f.sk_tipo_primario
        JOIN silver.dim_tipo t2 ON t2.sk_tipo = f.sk_tipo_primario_oponente
        JOIN silver.efetividade_tipo ef
             ON ef.sk_tipo_atacante = f.sk_tipo_primario
            AND ef.sk_tipo_defensor = f.sk_tipo_primario_oponente
        GROUP BY f.sk_tipo_primario, f.sk_tipo_primario_oponente,
                 t1.nome_tipo, t2.nome_tipo, ef.multiplicador
        ORDER BY t1.nome_tipo, t2.nome_tipo;
    """,
    # Análise 8 (proposta) — taxa de vitórias por habitat (grão: habitat)
    "gold.taxa_vitorias_por_habitat": """
        SELECT h.sk_habitat, h.nome_habitat,
               ROUND(AVG(f.venceu::numeric), 5) AS taxa,
               COUNT(*) AS qtd
        FROM silver.fato_confronto f
        JOIN silver.dim_habitat h ON h.sk_habitat = f.sk_habitat
        GROUP BY h.sk_habitat, h.nome_habitat
        ORDER BY h.nome_habitat;
    """,
}


def main():
    conn = conectar()
    try:
        exec_sql_file(conn, BASE_DIR / "sql" / "gold.sql")

        # repovoação idempotente: esvazia e recarrega, na ordem de não-dependência
        com_ordem = [
            "gold.taxa_vitorias_por_faixa_velocidade",
            "gold.taxa_vitorias_por_multiplicador",
            "gold.matriz_confronto",
            "gold.ranking_pokemon",
            "gold.taxa_vitorias_por_tipo",
            "gold.taxa_vitorias_por_habitat",
        ]
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE " + ", ".join(com_ordem) + " RESTART IDENTITY;")
        conn.commit()

        for tabela in com_ordem:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM gold.log_publicacao WHERE tabela = %s;", (tabela,))
                cur.execute("INSERT INTO " + tabela + " " + MATERIALIZACOES[tabela])
                cur.execute("SELECT count(*) FROM " + tabela)
                qtd = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO gold.log_publicacao (tabela, qtd_linhas) VALUES (%s, %s);",
                    (tabela, qtd),
                )
                print(f"[publicar] {tabela}: {qtd} linhas")
            conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT tabela, qtd_linhas, carregado_em FROM gold.log_publicacao ORDER BY id_log;")
            for r in cur.fetchall():
                print(f"[publicar] log: {r[0]} | {r[1]} linhas | {r[2]}")
        print("[publicar] camada gold concluída")
    finally:
        conn.close()


if __name__ == "__main__":
    main()