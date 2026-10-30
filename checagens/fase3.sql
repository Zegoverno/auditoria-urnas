-- Fase 3: arquivos das urnas. Rodar: duckdb < checagens/fase3.sql
-- Funciona para as UFs/turnos já processados em data/lake/urna_*.

create view sec  as select * from read_parquet('data/lake/urna_secao/*/*/*.parquet');
create view buv  as select * from read_parquet('data/lake/urna_bu_votos/*/*/*.parquet');
create view rdv  as select * from read_parquet('data/lake/urna_rdv_votos/*/*/*.parquet');
create view integ as select * from read_parquet('data/lake/urna_integridade/*/*/*.parquet', union_by_name=true);
create view lv   as select * from read_parquet('data/lake/urna_log_votos/*/*/*.parquet');
create view bucsv as select * from read_parquet('data/lake/bu/*/*/bu.parquet', hive_partitioning=true);
create view proc as select distinct turno, uf from sec;

-- U1. Integridade: cada arquivo publicado confere com o hash nas listas assinadas SW e HW
select 'U1 integridade (hash x listas assinadas)' checagem, turno, uf,
  count(*) filter (where presente) arquivos_conferidos,
  count(*) filter (where presente and not (ok_sw and ok_hw)) divergentes,
  count(*) filter (where presente and not (lista_sw_ok and lista_hw_ok)) listas_invalidas,
  count(*) filter (where erro is not null) erros,
  divergentes = 0 and listas_invalidas = 0 and erros = 0 ok
from integ group by all order by all;

-- U2. Recontagem: RDV (voto a voto) = BU binário, por seção x cargo x votável
-- No RDV, 'nuloAposSuspensao' (eleitor que não concluiu) entra no BU como nulo.
-- Voto de legenda: o RDV guarda o que foi digitado (ex.: 1103 = partido 11 + candidato
-- inexistente) e vale o partido, os 2 primeiros dígitos. Chaves sem número usam -1 (NULL nunca casa em junção).
with r as (select turno, uf, prefixo, id_eleicao, cargo,
                  case when tipo_voto like 'nulo%' then 'nulo' else tipo_voto end tipo,
                  case when tipo_voto = 'nominal' then try_cast(digitado as int)
                       when tipo_voto = 'legenda' then try_cast(left(digitado, 2) as int) else -1 end numero,
                  sum(votos) v
           from rdv group by all),
     b as (select turno, uf, prefixo, id_eleicao, cargo, tipo_voto tipo,
                  case when tipo_voto in ('nominal','legenda') then numero else -1 end numero, sum(votos) v
           from buv group by all)
select 'U2 RDV = BU por seção/cargo/votável' checagem, turno, uf, count(*) linhas,
  count(*) filter (where coalesce(r.v,0) <> coalesce(b.v,0)) divergencias, divergencias = 0 ok
from r full join b using (turno, uf, prefixo, id_eleicao, cargo, tipo, numero) group by all order by all;

-- U3. BU binário = BU publicado em CSV, por seção x votável (presidente)
with b as (select turno, uf, cd_municipio, zona, secao,
                  case tipo_voto when 'nominal' then numero when 'branco' then 95 when 'nulo' then 96 end nr,
                  sum(votos) v from buv where cargo = 'presidente' group by all),
     c as (select NR_TURNO turno, SG_UF uf, CD_MUNICIPIO cd_municipio, NR_ZONA zona, NR_SECAO secao,
                  NR_VOTAVEL nr, sum(QT_VOTOS) v
           from bucsv where DS_CARGO_PERGUNTA = 'Presidente' and (NR_TURNO, SG_UF) in (select (turno, uf) from proc)
           group by all)
select 'U3 BU binário = BU CSV (presidente)' checagem, turno, uf, count(*) linhas,
  count(*) filter (where coalesce(b.v,0) <> coalesce(c.v,0)) divergencias, divergencias = 0 ok
from b full join c using (turno, uf, cd_municipio, zona, secao, nr) group by all order by all;

-- U4. Log: votos computados no dia = comparecimento do BU, por seção
with l as (select turno, uf, prefixo, count(*) n from lv group by all)
select 'U4 log: votos computados = comparecimento' checagem, s.turno, s.uf, count(*) secoes,
  count(*) filter (where coalesce(l.n,0) <> s.comparecimento) divergentes,
  sum(s.comparecimento) comparecimento, sum(l.n) votos_log, divergentes = 0 ok
from sec s left join l using (turno, uf, prefixo) group by all order by all;

-- U5. Forma de habilitação soma o comparecimento
select 'U5 biometria + ano nasc. + sem biometria = comparecimento' checagem, turno, uf,
  sum(habilitados_biometria) biometria, sum(habilitados_biografia) ano_nascimento, sum(sem_biometria) sem_biometria,
  sum(comparecimento) comparecimento,
  count(*) filter (where habilitados_biometria + habilitados_biografia + sem_biometria <> comparecimento) secoes_divergentes,
  secoes_divergentes = 0 ok
from sec group by all order by all;

-- U6. Seções no zip das urnas x seções no BU CSV
with c as (select distinct NR_TURNO turno, SG_UF uf, CD_MUNICIPIO cd_municipio, NR_ZONA zona, NR_SECAO secao
           from bucsv where (NR_TURNO, SG_UF) in (select (turno, uf) from proc))
select 'U6 cobertura zip x CSV' checagem, coalesce(s.turno, c.turno) turno, coalesce(s.uf, c.uf) uf,
  count(*) filter (where s.prefixo is not null and c.secao is not null) ambos,
  count(*) filter (where c.secao is null) so_zip, count(*) filter (where s.prefixo is null) so_csv
from sec s full join c using (turno, uf, cd_municipio, zona, secao) group by all order by all;

-- U7. Forma de habilitação reconstruída do log (voto a voto) = detalhamento do BU, por seção
create view mes as select * from read_parquet('data/lake/urna_mesarios/*/*/*.parquet');
with l as (select turno, uf, prefixo,
                  count(*) filter (where habilitacao = 'biometria') bio,
                  count(*) filter (where habilitacao = 'manual_mesario') man,
                  count(*) filter (where habilitacao = 'sem_biometria') sem,
                  count(*) filter (where habilitacao = 'indeterminado') ind
           from lv group by all)
select 'U7 habilitação log = BU (biometria/manual/sem biometria)' checagem, s.turno, s.uf, count(*) secoes,
  count(*) filter (where coalesce(l.bio,0) <> s.habilitados_biometria or coalesce(l.man,0) <> s.habilitados_biografia
                      or coalesce(l.sem,0) <> s.sem_biometria) divergentes,
  sum(l.ind) indeterminados, divergentes = 0 ok
from sec s left join l using (turno, uf, prefixo) group by all order by all;
