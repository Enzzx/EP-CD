#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extrair.py — Fontes -> Camada Bronze (MongoDB)
· PokéAPI (REST/JSON)
· pokemon.csv / combats.csv
Cache em dados_brutos/ precede a escrita no MongoDB; a API só é consultada
quando o arquivo correspondente não existe (segunda execução = 0 requisições).
"""
import csv
import io
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from pymongo import MongoClient, UpdateOne

BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = BASE_DIR / "dados_brutos"

POKEAPI_BASE = "https://pokeapi.co/api/v2"
URL_POKEMON_CSV = "https://raw.githubusercontent.com/cdiener/pokemon_app/master/pokemon.csv"
URL_COMBATS_CSV = "https://raw.githubusercontent.com/cdiener/pokemon_app/master/combats.csv"

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = "pokedex_bronze"

# Escopo: gerações I a VI (espécies 1..721). Formas alternativas entram via varieties.
SPECIES_IDS = list(range(1, 722))
TYPE_API_IDS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 10001, 10002]

INTERVALO_REQ_SEG = 0.1


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_dirs():
    for sub in ("pokemon", "especies", "tipos", "csv"):
        (RAW_DIR / sub).mkdir(parents=True, exist_ok=True)


def get_json(url, session):
    r = session.get(url, timeout=120)
    r.raise_for_status()
    return r.json()


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def upsert_mongo(collection, docs):
    if not docs:
        return
    ops = [UpdateOne({"_id": d["_id"]}, {"$set": d}, upsert=True) for d in docs]
    collection.bulk_write(ops)


def ingerir_api():
    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB]
    col_p, col_e, col_t = db["pokemon"], db["especies"], db["tipos"]

    session = requests.Session()

    # --- Espécies ------------------------------------------------------------------
    docs = []
    for sid in SPECIES_IDS:
        p = RAW_DIR / "especies" / f"{sid}.json"
        if p.exists():
            data = load_json(p)
        else:
            data = get_json(f"{POKEAPI_BASE}/pokemon-species/{sid}", session)
            save_json(p, data)
            time.sleep(INTERVALO_REQ_SEG)
        doc = dict(data)
        doc["_id"] = f"especie/{sid}"
        doc["_fonte"] = "pokeapi"
        doc["_url"] = f"{POKEAPI_BASE}/pokemon-species/{sid}"
        doc["_ingerido_em"] = utc_now_iso()
        docs.append(doc)
    upsert_mongo(col_e, docs)
    docs.clear()
    print(f"[extrair] especies: {len(SPECIES_IDS)}")

    # --- Pokémon (formas padrão + alternativas, via varieties) -----------------------
    pokemon_ids = set()
    for sid in SPECIES_IDS:
        data = load_json(RAW_DIR / "especies" / f"{sid}.json")
        for v in data.get("varieties", []):
            url = (v.get("pokemon") or {}).get("url", "")
            if url:
                try:
                    pokemon_ids.add(int(url.rstrip("/").split("/")[-1]))
                except (ValueError, IndexError):
                    continue
    pokemon_ids = sorted(pokemon_ids)

    for pid in pokemon_ids:
        p = RAW_DIR / "pokemon" / f"{pid}.json"
        if p.exists():
            data = load_json(p)
        else:
            data = get_json(f"{POKEAPI_BASE}/pokemon/{pid}", session)
            save_json(p, data)
            time.sleep(INTERVALO_REQ_SEG)
        doc = dict(data)
        doc["_id"] = f"pokemon/{pid}"
        doc["_fonte"] = "pokeapi"
        doc["_url"] = f"{POKEAPI_BASE}/pokemon/{pid}"
        doc["_ingerido_em"] = utc_now_iso()
        docs.append(doc)
    upsert_mongo(col_p, docs)
    docs.clear()
    print(f"[extrair] pokemon: {len(pokemon_ids)}")

    # --- Tipos (21) -------------------------------------------------------------------
    for tid in TYPE_API_IDS:
        p = RAW_DIR / "tipos" / f"{tid}.json"
        if p.exists():
            data = load_json(p)
        else:
            data = get_json(f"{POKEAPI_BASE}/type/{tid}", session)
            save_json(p, data)
            time.sleep(INTERVALO_REQ_SEG)
        doc = dict(data)
        doc["_id"] = f"tipo/{tid}"
        doc["_fonte"] = "pokeapi"
        doc["_url"] = f"{POKEAPI_BASE}/type/{tid}"
        doc["_ingerido_em"] = utc_now_iso()
        docs.append(doc)
    upsert_mongo(col_t, docs)
    print(f"[extrair] tipos: {len(TYPE_API_IDS)}")

    client.close()


def baixar_csv_local(url, fname):
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    (RAW_DIR / "csv" / fname).write_text(r.text, encoding="utf-8")
    return r.text


def ler_csv_bytes(text):
    f = io.StringIO(text)
    return list(csv.DictReader(f))


def ingerir_csvs():
    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB]

    # pokemon.csv
    fname_p = "pokemon.csv"
    if not (RAW_DIR / "csv" / fname_p).exists():
        text = baixar_csv_local(URL_POKEMON_CSV, fname_p)
    else:
        text = (RAW_DIR / "csv" / fname_p).read_text(encoding="utf-8")
    rows = ler_csv_bytes(text)

    col = db["pokemon_csv"]
    cabecalho_original = list(rows[0].keys()) if rows else []
    docs = []
    for i, r in enumerate(rows, start=1):
        # MongoDB não representa chaves com ponto ('.') de forma literal; o ponto
        # é resolvido como separador de subcampos. Para preservar a coluna
        # 'Sp. Atk' / 'Sp. Def' como campo de nível superior, substituímos o
        # ponto por '_' e registramos o cabeçalho original como linhagem.
        doc = {k.replace(".", "_"): v for k, v in r.items()}
        doc["_cabecalho_original"] = cabecalho_original
        doc["_id"] = f"pokemon_csv/{i}"
        doc["_fonte"] = "csv"
        doc["_url"] = URL_POKEMON_CSV
        doc["_ingerido_em"] = utc_now_iso()
        docs.append(doc)
    upsert_mongo(col, docs)
    print(f"[extrair] pokemon_csv: {len(rows)}")

    # combats.csv
    fname_c = "combats.csv"
    if not (RAW_DIR / "csv" / fname_c).exists():
        text = baixar_csv_local(URL_COMBATS_CSV, fname_c)
    else:
        text = (RAW_DIR / "csv" / fname_c).read_text(encoding="utf-8")
    rows = ler_csv_bytes(text)

    col = db["combates"]
    docs = []
    for i, r in enumerate(rows, start=1):
        doc = dict(r)
        doc["_id"] = f"combate/{i}"
        doc["_fonte"] = "csv"
        doc["_url"] = URL_COMBATS_CSV
        doc["_ingerido_em"] = utc_now_iso()
        docs.append(doc)
    upsert_mongo(col, docs)
    print(f"[extrair] combates: {len(rows)}")

    client.close()


def main():
    ensure_dirs()
    ingerir_api()
    ingerir_csvs()
    print("[extrair] camada bronze concluída")


if __name__ == "__main__":
    main()