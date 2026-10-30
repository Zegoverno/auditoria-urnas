"""Fase 2: Boletins de Urna (CSV do TSE) -> lake em Parquet.

Lê cada zip bweb_<turno>_<UF>_*.zip baixado (conferido no manifesto),
extrai o CSV para um diretório temporário, grava
data/lake/bu/turno=<t>/uf=<UF>/bu.parquet e apaga o temporário.
Colunas mantidas como o TSE publica (nomes originais), com tipos explícitos.
"""

import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import duckdb

RAW = Path("data/raw/buweb")
LAKE = Path("data/lake/bu")

TIPOS = {
    "ANO_ELEICAO": "INTEGER", "NR_TURNO": "INTEGER", "CD_MUNICIPIO": "INTEGER",
    "NR_ZONA": "INTEGER", "NR_SECAO": "INTEGER", "NR_LOCAL_VOTACAO": "INTEGER",
    "CD_CARGO_PERGUNTA": "INTEGER", "NR_PARTIDO": "INTEGER", "QT_APTOS": "INTEGER",
    "QT_COMPARECIMENTO": "INTEGER", "QT_ABSTENCOES": "INTEGER", "CD_TIPO_URNA": "INTEGER",
    "CD_TIPO_VOTAVEL": "INTEGER", "NR_VOTAVEL": "INTEGER", "QT_VOTOS": "INTEGER",
    "NR_URNA_EFETIVADA": "BIGINT", "QT_ELEI_BIOM_SEM_HABILITACAO": "INTEGER",
    "NR_JUNTA_APURADORA": "INTEGER", "NR_TURMA_APURADORA": "INTEGER",
}


def ingerir(zip_path: Path, con: duckdb.DuckDBPyConnection) -> str:
    m = re.match(r"bweb_(\d)t_([A-Z]{2})_", zip_path.name)
    turno, uf = m.group(1), m.group(2)
    saida = LAKE / f"turno={turno}" / f"uf={uf}" / "bu.parquet"
    saida.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir="data") as tmp:
        with zipfile.ZipFile(zip_path) as z:
            csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
            assert len(csvs) == 1, f"{zip_path}: esperado 1 CSV, achei {csvs}"
            z.extract(csvs[0], tmp)
        csv = Path(tmp) / csvs[0]
        tipos = ", ".join(f"'{k}': '{v}'" for k, v in TIPOS.items())
        con.execute(f"""
            copy (
              select *, '{zip_path.name}' as arquivo_origem
              from read_csv('{csv}', delim=';', quote='"', header=true,
                            encoding='latin-1', all_varchar=true, types={{{tipos}}})
            ) to '{saida}' (format parquet, compression zstd)
        """)
    linhas = con.execute(f"select count(*) from '{saida}'").fetchone()[0]
    return f"{turno}t {uf}: {linhas:,} linhas -> {saida}"


def main() -> None:
    con = duckdb.connect()
    for z in sorted(RAW.glob("bweb_*t_*_*2026*.zip")):
        print(ingerir(z, con))
    shutil.rmtree(Path("data/tmp"), ignore_errors=True)


if __name__ == "__main__":
    main()
