#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
carregar.py — Camada Bronze (MongoDB) -> Camada Silver (PostgreSQL)
· Executa sql/silver.sql (DDL versionado)
· Concilia chaves CSV x Pokédex por nome (formatos Mega, Primal, Forme, Size, Cloak, Mode, Therian, ...)
· Popula modelo dimensional (esquema estrela) de forma idempotente
· Gera conciliacao.csv (auditoria R4) e preenche silver.log_conciliacao
"""
import csv
import io
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from pymongo import MongoClient

BASE_DIR = Path(__file__).resolve().parent

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = "pokedex_bronze"

PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("PG_PORT", "5432"))
PG_DB = os.getenv("PG_DB", "pokedex")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "postgres")

GENERACOES = {
    1: ("Geração I", "Kanto"),
    2: ("Geração II", "Johto"),
    3: ("Geração III", "Hoenn"),
    4: ("Geração IV", "Sinnoh"),
    5: ("Geração V", "Unova"),
    6: ("Geração VI", "Kalos"),
}

# ---- Normalização de nomes para conciliação ---------------------------------------
# Abordagem híbrida: chave canônica por conjunto de tokens (regra geral) somada a
# exceções explícitas para os raros nomes do CSV que a normalização não alcança.
def slugify(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


# Qualificadores que o CSV usa e a PokéAPI não registra no slug.
RUIDO = {"forme", "form", "mode", "cloak", "size", "style", "standard", "ordinary"}

# Exceções: nomes do CSV em que o conjunto de tokens não casa com o slug
# (palavra duplicada, token mesclado ou qualificador fora do ruído).
EXCECOES = {
    "DeoxysAttack Forme": "deoxys-attack",
    "Kyurem Black Kyurem": "kyurem-black",
    "Kyurem White Kyurem": "kyurem-white",
    "Zygarde Half Forme": "zygarde-50",
    "Hoopa Confined": "hoopa",
}


def normalizar(nome):
    """Reduz um nome a uma chave canônica: conjunto de tokens ordenado.
    'Mega Charizard X' e 'charizard-mega-x' produzem a mesma chave."""
    if not nome:
        return ""
    texto = nome.replace("♀", " f").replace("♂", " m")
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower()
    texto = texto.replace("'", "").replace(".", "")
    tokens = [t for t in re.split(r"[^a-z0-9]+", texto) if t and t not in RUIDO]
    return "|".join(sorted(tokens))


def indexar_formas(api_forms, species_by_form):
    """Índice chave normalizada -> formas. Formas padrão também entram pela
    chave do nome da espécie ('Deoxys' casa com 'deoxys-normal')."""
    indice = {}
    for forma in api_forms.values():
        chave = normalizar(forma["name"])
        if chave:
            indice.setdefault(chave, []).append(forma)
        if forma.get("is_default"):
            sp = species_by_form.get(forma["name"])
            if sp and sp.get("name"):
                chave_esp = normalizar(sp["name"])
                if chave_esp:
                    indice.setdefault(chave_esp, []).append(forma)
    return indice


def reconhecer(nome_csv, indice, api_slugs):
    """Devolve (slug, formato, observacao) para um nome do CSV."""
    nm = (nome_csv or "").strip()
    if not nm:
        return None, "NAO_CONCILIADO_NOME_AUSENTE", "Nome ausente no CSV (linha 63) - membro especial"
    if nm in EXCECOES:
        slug = EXCECOES[nm]
        if slug in api_slugs:
            return slug, "FORMA_ALTERNATIVA", "Exceção explícita: nome do CSV não normaliza genericamente para a PokéAPI."
        return None, "NAO_CONCILIADO", f"Exceção configurada ('{nm}') sem forma correspondente na PokéAPI."
    forma = indice.get(normalizar(nm))
    if forma:
        slug = forma[0]["name"]
        formato = "EXATO" if slug == slugify(nm) else "FORMA_ALTERNATIVA"
        return slug, formato, ""
    return None, "NAO_CONCILIADO", "Forma sem correspondência na PokéAPI; atributos de espécie herdados por associação."


# ---- Leitura do bronze --------------------------------------------------------------
def ler_bronze():
    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB]
    docs = {
        "especies": list(db["especies"].find()),
        "pokemon": list(db["pokemon"].find()),
        "tipos": list(db["tipos"].find()),
        "pokemon_csv": list(db["pokemon_csv"].find()),
        "combates": list(db["combates"].find()),
    }
    client.close()
    return docs


def ordenar_csv_docs(docs):
    def num(d):
        return int(d["_id"].split("/")[-1])
    return sorted(docs, key=num)


# ---- PostgreSQL helper --------------------------------------------------------------
def conectar():
    return psycopg.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASSWORD,
    )


def exec_sql_file(conn, path):
    sql = path.read_text(encoding="utf-8")
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
    except psycopg.errors.ProgrammingError:
        with conn.cursor() as cur:
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                cur.execute(stmt)


def main():
    bronze = ler_bronze()

    especies = bronze["especies"]
    pokemon_api = bronze["pokemon"]
    tipos_api = {t["id"]: t for t in bronze["tipos"]}
    pokemon_csv = ordenar_csv_docs(bronze["pokemon_csv"])
    combates = ordenar_csv_docs(bronze["combates"])

    # ---- mapas da PokéAPI -----------------------------------------------------------
    # forma (slug) -> doc pokemon
    api_forms = {p["name"]: p for p in pokemon_api}
    # forma (slug) -> espécie a que pertence
    species_by_form = {}
    for sp in especies:
        for v in sp.get("varieties", []):
            name = (v.get("pokemon") or {}).get("name")
            if name:
                species_by_form[name] = sp
    # geração -> id numérico
    def geracao_id(g):
        m = re.match(r"^generation-([a-z]+)$", g or "")
        if not m:
            return None
        rom = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}
        return rom.get(m.group(1))

    def parse_int(v, default=None):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def base_stats(form):
        st = {}
        for s in form.get("stats", []) or []:
            st[s["stat"]["name"]] = s["base_stat"]
        return st

    def form_types(form):
        return [t["type"]["name"] for t in form.get("types", []) or []]

    def species_attrs(sp):
        habitat = (sp.get("habitat") or {}).get("name")
        return {
            "geracao": geracao_id((sp.get("generation") or {}).get("name")),
            "habitat": habitat,  # None -> conceito inaplicável (problema 3)
            "cor": (sp.get("color") or {}).get("name") or "SEM_INFORMACAO",
            "forma_corporal": (sp.get("shape") or {}).get("name") or "SEM_INFORMACAO",
            "taxa_crescimento": (sp.get("growth_rate") or {}).get("name") or "SEM_INFORMACAO",
            "taxa_captura": sp.get("capture_rate"),
            "felicidade_base": sp.get("base_happiness"),
            "lendario": bool(sp.get("is_legendary")),
            "mitico": bool(sp.get("is_mythical")),
            "bebe": bool(sp.get("is_baby")),
            "id_species": sp.get("id"),
        }

    conn = conectar()
    try:
        exec_sql_file(conn, BASE_DIR / "sql" / "silver.sql")
        # limpeza p/ repovoamento idempotente (ordem de dependências invertida)
        with conn.cursor() as cur:
            for tbl in [
                "silver.fato_confronto",
                "silver.efetividade_tipo",
                "silver.dim_pokemon",
                "silver.dim_tipo",
                "silver.dim_geracao",
                "silver.dim_habitat",
                "silver.dim_cor",
                "silver.dim_forma_corporal",
                "silver.dim_taxa_crescimento",
                "silver.log_conciliacao",
            ]:
                cur.execute(f"TRUNCATE TABLE {tbl} RESTART IDENTITY CASCADE;")
        conn.commit()

        # ---- dimensões base -----------------------------------------------------------
        sk_ger = {}
        for g, (desc, reg) in GENERACOES.items():
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO silver.dim_geracao (id_geracao, descricao_geracao, regiao) "
                    "VALUES (%s, %s, %s) RETURNING sk_geracao;",
                    (g, desc, reg),
                )
                sk_ger[g] = cur.fetchone()[0]
        conn.commit()

        sk_tipo = {}
        for tid in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18]:
            name = tipos_api[tid]["name"]
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO silver.dim_tipo (id_tipo_api, nome_tipo, eh_valido_jogo) "
                    "VALUES (%s, %s, TRUE) RETURNING sk_tipo;",
                    (tid, name),
                )
                sk_tipo[name] = cur.fetchone()[0]
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO silver.dim_tipo (id_tipo_api, nome_tipo, eh_valido_jogo, eh_membro_especial) "
                "VALUES (NULL, 'SEM_TIPO', FALSE, TRUE) RETURNING sk_tipo;"
            )
            sk_tipo["SEM_TIPO"] = cur.fetchone()[0]
        conn.commit()

        # habitat
        for habitat, obs in [("NAO_SE_APLICA", "conceito não se aplica ao jogo de origem"), ("SEM_INFORMACAO", "sem informação na fonte")]:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO silver.dim_habitat (nome_habitat, observacao) VALUES (%s, %s);",
                    (habitat, obs),
                )
        # cor / forma corporal / taxa de crescimento
        for tbl, col in [("dim_cor", "nome_cor"),
                          ("dim_forma_corporal", "nome_forma_corporal"),
                          ("dim_taxa_crescimento", "nome_taxa_crescimento")]:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO silver.{tbl} ({col}) VALUES (%s);",
                    ("SEM_INFORMACAO",),
                )
        conn.commit()

        def inserir_lazy(conn, tabela, coluna_nome, pk_col, valor):
            nome_tbl = tabela.split(".")[-1]
            mp = _dims_cache.get(nome_tbl)
            if mp is None:
                mp = {}
                with conn.cursor() as cur:
                    cur.execute(f"SELECT {coluna_nome}, {pk_col} FROM {tabela}")
                    mp.update(cur.fetchall())
                _dims_cache[nome_tbl] = mp
            if valor not in mp:
                with conn.cursor() as cur:
                    cur.execute(f"INSERT INTO {tabela} ({coluna_nome}) VALUES (%s) RETURNING {pk_col};", (valor,))
                    mp[valor] = cur.fetchone()[0]
                conn.commit()
            return mp[valor]

        global _dims_cache
        _dims_cache = {}

        # insere membros especiais em habitat/cor/forma/taxa
        sk_habitat = inserir_lazy(conn, "silver.dim_habitat", "nome_habitat", "sk_habitat", "NAO_SE_APLICA")
        sk_habitat_des = inserir_lazy(conn, "silver.dim_habitat", "nome_habitat", "sk_habitat", "SEM_INFORMACAO")

        # ---- conciliação CSV x API ------------------------------------------------------
        api_slugs = set(api_forms.keys())
        indice = indexar_formas(api_forms, species_by_form)

        # Tabelas: (id_csv, nome_csv, ...)
        csv_rows = []
        for doc in pokemon_csv:
            id_csv = int(doc["_id"].split("/")[-1])
            rec = {
                "id_csv": id_csv,
                "name": (doc.get("Name") or "").strip(),
                "type1": (doc.get("Type 1") or "").strip(),
                "type2": (doc.get("Type 2") or "").strip(),
                "hp": parse_int(doc.get("HP")),
                "atk": parse_int(doc.get("Attack")),
                "def": parse_int(doc.get("Defense")),
                "spa": parse_int(doc.get("Sp_ Atk") or doc.get("Sp. Atk")),
                "spd": parse_int(doc.get("Sp_ Def") or doc.get("Sp. Def")),
                "spe": parse_int(doc.get("Speed")),
                "geracao": parse_int(doc.get("Generation")),
                "legend": (doc.get("Legendary") or "").strip().lower() == "true",
            }
            csv_rows.append(rec)

        reconciliacao = []  # (id_csv, numero, nome_csv, nome_api, formato, obs, conciliado)
        csv_id_sk = {}
        dim_rows = []  # dicionário com atributos p/ inserir
        api_used = set()

        for rec in csv_rows:
            nome_csv = rec["name"]
            if not nome_csv:
                slug = None
                obs = "Nome ausente no CSV (linha 63) - membro especial"
                formato = "NAO_CONCILIADO_NOME_AUSENTE"
            else:
                slug, formato, obs = reconhecer(nome_csv, indice, api_slugs)

            if slug:
                form = api_forms[slug]
                api_used.add(slug)
                species = species_by_form.get(slug)
                numero = species.get("id") if species else None
                attrs = species_attrs(species) if species else {}
                nome_api = form["name"]
                eh_padrao = bool(form.get("is_default"))
                row = {
                    "id_csv": rec["id_csv"],
                    "id_api": form["id"],
                    "numero": numero,
                    "nome_original": nome_csv or form["name"],
                    "nome_api": nome_api,
                    "eh_forma_alternativa": not eh_padrao,
                    "eh_padrao": eh_padrao,
                    "altura": form.get("height"),
                    "peso": form.get("weight"),
                    "exp": form.get("base_experience"),
                    "stats": base_stats(form),
                    "type1": rec["type1"],
                    "type2": rec["type2"],
                    "geracao": rec["geracao"],
                    "legend": rec["legend"],
                    "attrs": attrs,
                    "status_csv": True,
                }
                sk_formato = formato
            else:
                # sem forma api: usa stats do CSV; espécie via associação por nome base
                form = None
                base = None
                if nome_csv.startswith("Primal "):
                    base = slugify(nome_csv[7:])
                elif nome_csv and nome_csv.startswith("Mega "):
                    rest = nome_csv[5:].strip().split()
                    if rest and rest[-1] in ("X", "Y"):
                        base = slugify(" ".join(rest[:-1]))
                    else:
                        base = slugify(nome_csv[5:])
                else:
                    base = slugify(nome_csv)
                species = None
                form_base = api_forms.get(base) if base else None
                if form_base:
                    species = species_by_form.get(base)
                attrs = species_attrs(species) if species else {}
                numero = species.get("id") if species else None
                row = {
                    "id_csv": rec["id_csv"],
                    "id_api": None,
                    "numero": numero,
                    "nome_original": nome_csv or "DESCONHECIDO",
                    "nome_api": (base if species else None) or (nome_csv if nome_csv.startswith(("Primal ", "Mega ")) else None),
                    "eh_forma_alternativa": True,
                    "eh_padrao": False,
                    "altura": form_base.get("height") if form_base else None,
                    "peso": form_base.get("weight") if form_base else None,
                    "exp": form_base.get("base_experience") if form_base else None,
                    "stats": base_stats(form_base) if form_base else {},
                    "type1": rec["type1"],
                    "type2": rec["type2"],
                    "geracao": rec["geracao"],
                    "legend": rec["legend"],
                    "attrs": attrs,
                    "status_csv": True,
                }
                sk_formato = formato

            # stats do CSV são a autoridade do simulador de batalhas
            if rec["hp"] is not None:
                row["stats"].update({
                    "hp": rec["hp"], "attack": rec["atk"], "defense": rec["def"],
                    "special-attack": rec["spa"], "special-defense": rec["spd"], "speed": rec["spe"],
                })
            numero_final = row["numero"] if row["numero"] else rec["geracao"]
            reconciliacao.append((rec["id_csv"], numero_final, nome_csv, row["nome_api"], sk_formato, obs, bool(slug)))
            dim_rows.append(row)

        # adiciona formas da API ausentes do CSV (cadastro completo)
        for slug, form in api_forms.items():
            if slug in api_used:
                continue
            species = species_by_form.get(slug)
            if not species:
                continue
            attrs = species_attrs(species)
            row = {
                "id_csv": None,
                "id_api": form["id"],
                "numero": species["id"],
                "nome_original": form["name"],
                "nome_api": form["name"],
                "eh_forma_alternativa": not bool(form.get("is_default")),
                "eh_padrao": bool(form.get("is_default")),
                "altura": form.get("height"),
                "peso": form.get("weight"),
                "exp": form.get("base_experience"),
                "stats": base_stats(form),
                "type1": form_types(form)[0] if form_types(form) else "SEM_TIPO",
                "type2": form_types(form)[1] if len(form_types(form)) > 1 else "SEM_TIPO",
                "geracao": geracao_id((species.get("generation") or {}).get("name")),
                "legend": None,
                "attrs": attrs,
                "status_csv": False,
            }
            dim_rows.append(row)

        # ---- insere dim_pokemon --------------------------------------------------------
        rows_values = []
        for r in dim_rows:
            type1 = r["type1"].strip().lower() if r["type1"] else "SEM_TIPO"
            type2 = r["type2"].strip().lower() if r["type2"] else "SEM_TIPO"
            g = r["geracao"] if r["geracao"] in GENERACOES else None
            sk_g = sk_ger.get(g) if g else None
            # habitat
            hab = r["attrs"].get("habitat")
            if hab is None:
                if r["status_csv"] and not r["attrs"]:
                    hab_sk = sk_habitat_des
                else:
                    hab_sk = sk_habitat  # NAO_SE_APLICA
            else:
                hab_sk = inserir_lazy(conn, "silver.dim_habitat", "nome_habitat", "sk_habitat", hab)
            cor_sk = inserir_lazy(conn, "silver.dim_cor", "nome_cor", "sk_cor", r["attrs"].get("cor", "SEM_INFORMACAO"))
            forma_sk = inserir_lazy(conn, "silver.dim_forma_corporal", "nome_forma_corporal", "sk_forma_corporal", r["attrs"].get("forma_corporal", "SEM_INFORMACAO"))
            taxa_sk = inserir_lazy(conn, "silver.dim_taxa_crescimento", "nome_taxa_crescimento", "sk_taxa_crescimento", r["attrs"].get("taxa_crescimento", "SEM_INFORMACAO"))
            # tipos
            sk_t1 = sk_tipo.get(type1, sk_tipo["SEM_TIPO"])
            sk_t2 = sk_tipo.get(type2, sk_tipo["SEM_TIPO"])
            # categoria de raridade
            lend = r["attrs"].get("lendario")
            mit = r["attrs"].get("mitico")
            beb = r["attrs"].get("bebe")
            if beb:
                cat = "BEBE"
            elif mit:
                cat = "MITICO"
            elif lend:
                cat = "LENDARIO"
            else:
                cat = "COMUM"
            vel = r["stats"].get("speed")
            rows_values.append((
                r["numero"], r["id_api"], r["id_csv"], r["nome_original"], r["nome_api"],
                r["eh_forma_alternativa"], r["eh_padrao"],
                r["numero"],
                r["altura"], r["peso"], r["exp"],
                r["stats"].get("hp"), r["stats"].get("attack"), r["stats"].get("defense"),
                r["stats"].get("special-attack"), r["stats"].get("special-defense"), vel,
                r["attrs"].get("taxa_captura"), r["attrs"].get("felicidade_base"),
                lend, mit, beb, cat,
                sk_g, hab_sk, cor_sk, forma_sk, taxa_sk, sk_t1, sk_t2,
            ))
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO silver.dim_pokemon (
                    numero_pokedex, id_api, id_csv, nome_original, nome_api,
                    eh_forma_alternativa, eh_padrao, pokemon_pai_pokedex,
                    altura, peso, experiencia_base,
                    hp_base, ataque_base, defesa_base, ataque_especial_base,
                    defesa_especial_base, velocidade_base,
                    taxa_captura, felicidade_base,
                    eh_lendario, eh_mitico, eh_bebe, categoria_raridade,
                    sk_geracao, sk_habitat, sk_cor, sk_forma_corporal,
                    sk_taxa_crescimento, sk_tipo_primario, sk_tipo_secundario)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);""",
                rows_values,
            )
        conn.commit()

        # mapa id_csv -> sk
        with conn.cursor() as cur:
            cur.execute("SELECT id_csv, sk_pokemon FROM silver.dim_pokemon WHERE id_csv IS NOT NULL")
            csv_id_sk.update(cur.fetchall())
        # mapa nome -> sk (para vel de api-only nao necessario)
        with conn.cursor() as cur:
            cur.execute("SELECT id_csv, velocidade_base FROM silver.dim_pokemon WHERE id_csv IS NOT NULL")
            vel_csv = dict(cur.fetchall())

        # ---- efetividade_tipo ------------------------------------------------------------
        rows_ef = []
        tipos_validos = [tipos_api[i]["name"] for i in range(1, 19)]
        for ta in tipos_validos:
            d = tipos_api[[i for i in range(1, 19) if tipos_api[i]["name"] == ta][0]].get("damage_relations", {})
            d_to = {x["name"] for x in d.get("double_damage_to", [])}
            h_to = {x["name"] for x in d.get("half_damage_to", [])}
            n_to = {x["name"] for x in d.get("no_damage_to", [])}
            for tb in tipos_validos:
                m = 1.0
                if tb in d_to:
                    m = 2.0
                elif tb in h_to:
                    m = 0.5
                elif tb in n_to:
                    m = 0.0
                rows_ef.append((sk_tipo[ta], sk_tipo[tb], m))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO silver.efetividade_tipo (sk_tipo_atacante, sk_tipo_defensor, multiplicador) VALUES (%s,%s,%s);",
                rows_ef,
            )
        conn.commit()

        # ---- fato_confronto ---------------------------------------------------------------
        rows_fato = []
        for doc in combates:
            ic = int(doc["_id"].split("/")[-1])
            c1 = int(doc["First_pokemon"])
            c2 = int(doc["Second_pokemon"])
            win = int(doc["Winner"])
            sk1 = csv_id_sk[c1]
            sk2 = csv_id_sk[c2]
            v1 = vel_csv.get(c1)
            v2 = vel_csv.get(c2)
            venceu1 = 1 if win == c1 else 0
            venceu2 = 1 if win == c2 else 0
            rows_fato.append((ic, 1, sk1, sk2, venceu1, 1, v1, v2, (v1 - v2) if (v1 and v2) else None))
            rows_fato.append((ic, 2, sk2, sk1, venceu2, 0, v2, v1, (v2 - v1) if (v1 and v2) else None))
        for i in range(0, len(rows_fato), 5000):
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO silver.fato_confronto
                       (id_combate, lado, sk_pokemon, sk_oponente, venceu, atacou_primeiro, velocidade_combatente, velocidade_oponente, diff_velocidade)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s);""",
                    rows_fato[i:i + 5000],
                )
            conn.commit()

        # ---- log_conciliacao + conciliacao.csv --------------------------------------------
        cabecalho = ["pokemon_id_csv", "numero_pokedex", "nome_csv", "nome_api", "formato", "observacao"]
        with open(BASE_DIR / "conciliacao.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(cabecalho)
            w.writerows([(r[0], r[1], r[2], r[3], r[4], r[5]) for r in reconciliacao])
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO silver.log_conciliacao (pokemon_id_csv, numero_pokedex, nome_csv, nome_api, formato, observacao, conciliado) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s);",
                [(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in reconciliacao],
            )
        conn.commit()

        # ---- contagens -------------------------------------------------------------------
        with conn.cursor() as cur:
            for tbl in ["dim_geracao", "dim_tipo", "dim_pokemon", "efetividade_tipo", "fato_confronto", "log_conciliacao"]:
                cur.execute(f"SELECT count(*) FROM silver.{tbl}")
                print(f"[carregar] silver.{tbl}: {cur.fetchone()[0]}")

        conciliados = sum(1 for r in reconciliacao if r[6])
        print(f"[carregar] conciliação: {conciliados}/{len(reconciliacao)} conciliados")
        nao = [(r[2], r[4]) for r in reconciliacao if not r[6]]
        print("[carregar] não conciliados:", nao)

        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()