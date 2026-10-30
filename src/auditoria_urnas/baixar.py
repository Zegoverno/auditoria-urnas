"""Download com manifesto e conferência de hash.

Cada arquivo baixado é conferido contra o .sha512 que o TSE publica ao lado
(quando existe) e registrado em data/manifesto.csv com URL, data, tamanho,
SHA-512 calculado e o resultado da conferência.

Uso: python -m auditoria_urnas.baixar <padrão SQL LIKE sobre url> [...]
"""

import csv
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import httpx

RAW = Path("data/raw")
MANIFESTO = Path("data/manifesto.csv")
CAMPOS = ["url", "arquivo", "bytes", "sha512", "sha512_tse", "confere", "baixado_em"]


def destino(url: str) -> Path:
    partes = url.split("/")
    return RAW / partes[-2] / partes[-1]


def sha512_tse(cliente: httpx.Client, url: str) -> str:
    r = cliente.get(url + ".sha512", timeout=60, follow_redirects=True)
    if r.status_code != 200:
        return ""
    return r.text.split()[0].strip().lower()


def baixar(cliente: httpx.Client, url: str) -> dict:
    caminho = destino(url)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha512()
    tmp = caminho.with_suffix(caminho.suffix + ".part")
    with cliente.stream("GET", url, timeout=600, follow_redirects=True) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for bloco in r.iter_bytes(1 << 20):
                f.write(bloco)
                h.update(bloco)
    tmp.rename(caminho)
    calculado = h.hexdigest()
    oficial = sha512_tse(cliente, url)
    return {
        "url": url,
        "arquivo": str(caminho),
        "bytes": caminho.stat().st_size,
        "sha512": calculado,
        "sha512_tse": oficial,
        "confere": "sem_hash_tse" if not oficial else ("sim" if oficial == calculado else "NAO"),
        "baixado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def registrar(linha: dict) -> None:
    novo = not MANIFESTO.exists()
    with MANIFESTO.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if novo:
            w.writeheader()
        w.writerow(linha)


def ja_baixados() -> set[str]:
    if not MANIFESTO.exists():
        return set()
    with MANIFESTO.open() as f:
        return {l["url"] for l in csv.DictReader(f) if l["confere"] in ("sim", "sem_hash_tse")}


def urls(padroes: list[str]) -> list[str]:
    filtro = " or ".join("url like ?" for _ in padroes)
    q = f"select distinct url from 'data/inventario.csv' where ({filtro}) and url not like '%.sha512' order by url"
    return [r[0] for r in duckdb.execute(q, padroes).fetchall()]


def main() -> None:
    feitos = ja_baixados()
    with httpx.Client() as cliente:
        for url in urls(sys.argv[1:]):
            if url in feitos and destino(url).exists():
                print(f"= {url}", flush=True)
                continue
            linha = baixar(cliente, url)
            registrar(linha)
            print(f"{'ok' if linha['confere'] != 'NAO' else '!! HASH DIVERGENTE'} {linha['confere']} {linha['bytes']/1e6:.0f} MB {url}", flush=True)


if __name__ == "__main__":
    main()
