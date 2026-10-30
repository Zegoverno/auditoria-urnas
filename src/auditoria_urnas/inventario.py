"""Fase 1: inventário dos conjuntos de dados do Portal de Dados Abertos do TSE.

Lista cada recurso (arquivo) dos conjuntos relevantes, com URL e tamanho
(via HEAD), sem baixar nada. Saída: data/inventario.csv.
"""

import csv
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

API = "https://dadosabertos.tse.jus.br/api/3/action"
CONJUNTOS = [
    "resultados-2026",
    "resultados-2026-boletim-de-urna",
    "resultados-2026-arquivos-transmitidos-para-totalizacao",
    "resultados-2026-correspondencias-esperadas-e-efetivadas-1-turno",
    "resultados-2026-correspondencias-esperadas-e-efetivadas-2-turno",
    "resultados-2026-logs-do-sistema-de-preparacao-das-urnas-eletronicas-gedai-1-turno",
    "resultados-2026-logs-do-sistema-de-preparacao-das-urnas-eletronicas-gedai-2-turno",
    "comparecimento-e-abstencao-2026",
    "eleitorado-2026",
    "mesarias-mesarios-e-funcoes-especiais-2026",
    "resultados-2022",
    "resultados-2022-boletim-de-urna",
]
SAIDA = Path("data/inventario.csv")


def tamanho(cliente: httpx.Client, url: str) -> int | None:
    try:
        r = cliente.head(url, follow_redirects=True, timeout=30)
        n = r.headers.get("content-length")
        return int(n) if n else None
    except httpx.HTTPError:
        return None


def main() -> None:
    linhas = []
    with httpx.Client() as cliente:
        for nome in CONJUNTOS:
            r = cliente.get(f"{API}/package_show", params={"id": nome}, timeout=60)
            if r.status_code != 200:
                print(f"! conjunto não encontrado: {nome}", file=sys.stderr)
                continue
            for rec in r.json()["result"]["resources"]:
                linhas.append({
                    "conjunto": nome,
                    "recurso": rec.get("name") or "",
                    "formato": rec.get("format") or "",
                    "url": rec.get("url") or "",
                })
        with ThreadPoolExecutor(8) as pool:
            for linha, n in zip(linhas, pool.map(lambda l: tamanho(cliente, l["url"]), linhas)):
                linha["bytes"] = n if n is not None else ""
    SAIDA.parent.mkdir(exist_ok=True)
    with SAIDA.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["conjunto", "recurso", "formato", "bytes", "url"])
        w.writeheader()
        w.writerows(linhas)
    print(f"{len(linhas)} recursos -> {SAIDA}")


if __name__ == "__main__":
    main()
