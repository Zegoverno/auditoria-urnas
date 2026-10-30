"""Fase 2: Correspondências esperadas (csec = seção, ccont = contingência) e
efetivadas (ceft) -> data/lake/corresp_<tipo>/turno=<t>/uf=<UF>/<arquivo>.parquet

Todas as versões publicadas são mantidas (ex.: esperada de 24/10 e de 25/10);
a coluna arquivo_origem identifica a versão.
"""

import re
import tempfile
import zipfile
from pathlib import Path

import duckdb

RAW = [Path("data/raw/correspesp"), Path("data/raw/correspefet")]
LAKE = Path("data/lake")


def main() -> None:
    con = duckdb.connect()
    for pasta in RAW:
        for zp in sorted(pasta.glob("*.zip")):
            with tempfile.TemporaryDirectory(dir="data") as tmp, zipfile.ZipFile(zp) as z:
                for nome in z.namelist():
                    m = re.match(r"(csec|ccont|ceft)_(\d)t_([A-Z]{2})_(\d+)\.csv$", nome)
                    if not m:
                        continue
                    tipo, turno, uf, carimbo = m.groups()
                    z.extract(nome, tmp)
                    saida = LAKE / f"corresp_{tipo}" / f"turno={turno}" / f"uf={uf}" / f"{carimbo}.parquet"
                    saida.parent.mkdir(parents=True, exist_ok=True)
                    con.execute(f"""
                        copy (select *, '{nome}' as arquivo_origem
                              from read_csv('{Path(tmp) / nome}', delim=';', quote='"', header=true,
                                            encoding='latin-1', all_varchar=true))
                        to '{saida}' (format parquet, compression zstd)""")
                    n = con.execute(f"select count(*) from '{saida}'").fetchone()[0]
                    print(f"{tipo} {turno}t {uf} {carimbo}: {n:,}", flush=True)


if __name__ == "__main__":
    main()
