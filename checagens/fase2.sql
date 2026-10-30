-- Fase 2: totais oficiais. Rodar: duckdb < checagens/fase2.sql
-- Cada checagem retorna ok=true/false e os números que a sustentam.

create view bu  as select * from read_parquet('data/lake/bu/*/*/bu.parquet', hive_partitioning=true);
create view tot as select * from 'data/lake/totalizacao/presidente_munzona_2026.parquet';
create view det as select * from 'data/lake/detalhe_secao/presidente_2026.parquet';
create view ceft as select * from read_parquet('data/lake/corresp_ceft/*/*/*.parquet', hive_partitioning=true);

-- C1. Soma dos BUs = votos proclamados pelo TSE no 2º turno
select 'C1 BU 2t = proclamado' checagem,
  sum(QT_VOTOS) filter (where NR_VOTAVEL = 13) lula,
  sum(QT_VOTOS) filter (where NR_VOTAVEL = 22) flavio,
  lula = 64256232 and flavio = 59826041 ok
from bu where NR_TURNO = 2 and DS_CARGO_PERGUNTA = 'Presidente';

-- C2. Soma dos BUs = totalização oficial por candidato, nos dois turnos
--     (exceto nº 28, que renunciou após a carga das urnas: votos anulados na totalização)
with b as (select NR_TURNO t, NR_VOTAVEL nr, sum(QT_VOTOS) v from bu
           where DS_CARGO_PERGUNTA = 'Presidente' and CD_TIPO_VOTAVEL = 1 group by all),
     o as (select NR_TURNO::int t, NR_CANDIDATO::int nr, sum(QT_VOTOS_NOMINAIS::bigint) v from tot
           where DS_CARGO = 'Presidente' group by all)
select 'C2 BU = totalização por candidato' checagem,
  count(*) filter (where b.v is distinct from o.v and b.nr <> 28) divergencias,
  sum(b.v) filter (where b.nr = 28) votos_nr28_anulados,
  divergencias = 0 ok
from b full join o using (t, nr);

-- C3. Mesma checagem no nível município x zona (onde a totalização é publicada)
with b as (select NR_TURNO t, CD_MUNICIPIO m, NR_ZONA z, NR_VOTAVEL nr, sum(QT_VOTOS) v from bu
           where DS_CARGO_PERGUNTA = 'Presidente' and CD_TIPO_VOTAVEL = 1 and NR_VOTAVEL <> 28 group by all),
     o as (select NR_TURNO::int t, CD_MUNICIPIO::int m, NR_ZONA::int z, NR_CANDIDATO::int nr,
                  sum(QT_VOTOS_NOMINAIS::bigint) v from tot where DS_CARGO = 'Presidente' group by all)
-- (o BU omite candidatos com 0 votos na seção; a totalização lista com 0: ausência = 0)
select 'C3 BU = totalização por município/zona/candidato' checagem,
  count(*) linhas, count(*) filter (where coalesce(b.v, 0) <> coalesce(o.v, 0)) divergencias, divergencias = 0 ok
from b full join o using (t, m, z, nr);

-- C4. Cobertura: seções do detalhe x seções com BU
select 'C4 cobertura de seções' checagem, NR_TURNO,
  count(*) total, count(*) filter (where ST_SECAO_INSTALADA = 'Não') nao_instaladas,
  sum(QT_APTOS::int) filter (where ST_SECAO_INSTALADA = 'Não') aptos_nao_instaladas
from det group by all order by 2;

-- C5. Correspondência urna esperada x efetivada: divergências (D) e urnas anuladas (O)
select 'C5 correspondências' checagem, turno,
  count(*) filter (where TP_DIVERGENCIA = 'D') divergencia_D,
  count(*) filter (where TP_DIVERGENCIA = 'O') anulada_O,
  count(*) filter (where TP_DIVERGENCIA = 'C') contingencia_C,
  divergencia_D = 0 and anulada_O = 0 ok
from ceft group by all order by 2;
