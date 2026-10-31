"""Fuso de cada município em relação a Brasília, inferido das próprias urnas.

As urnas gravam hora LOCAL (BU, log). A votação abre às 08:00 de Brasília no país
inteiro, então a hora local de abertura mais comum no município dá o fuso:
  fuso_horas = moda(hora local de abertura) - 8   (AC: -2, MT/MS/RO/RR/AM: -1, Noronha: +1)
horario_brasilia = horario_local - fuso_horas.
Exterior (ZZ) vota das 8h às 17h na hora local de cada cidade: fuso fica nulo.

Grava data/lake/municipio_fuso/fuso.parquet (uf, cd_municipio, fuso_horas, secoes,
secoes_fora: seções cuja abertura não bate com o fuso do município, isto é, abertura atrasada).
Uso: python -m auditoria_urnas.fuso
"""

from pathlib import Path

import duckdb

SAIDA = Path("data/lake/municipio_fuso/fuso.parquet")


def main() -> None:
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"""
      copy (
        with s as (select uf, cd_municipio, hour(abertura) h
                   from read_parquet('data/lake/urna_secao/*/*/*.parquet', hive_partitioning=true)
                   where abertura is not null),
        m as (select uf, cd_municipio, mode(h) h, count(*) secoes from s group by all)
        select m.uf, m.cd_municipio,
               case when m.uf = 'ZZ' then null else m.h - 8 end fuso_horas,
               m.secoes, count(*) filter (where s.h <> m.h) secoes_fora
        from m join s using (uf, cd_municipio) group by all order by all
      ) to '{SAIDA}' (format parquet)""")
    print(con.execute(f"""select fuso_horas, count(*) municipios, sum(secoes) secoes, sum(secoes_fora) fora
                          from '{SAIDA}' group by all order by all""").fetchall())


if __name__ == "__main__":
    main()
