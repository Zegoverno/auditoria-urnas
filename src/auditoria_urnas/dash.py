"""Gera os dados do dashboard (dash/public/data/) a partir do lake auditado.

Tudo sai das tabelas da Fase 3 (BU binário das urnas, já conferido com RDV, CSV e
assinaturas), por seção e turno; município, UF e Brasil são somas das seções.

Arquivos:
  meta.json              candidatos, totais do Brasil, fluxo Brasil, data de geração
  uf.json                métricas por UF (1º e 2º turno)
  mun/<UF>.json          métricas por município (código TSE e IBGE, centroide)
  secoes/<cd_tse>.json   locais de votação (lat/lon) com as seções e suas métricas
  fluxo.json             fluxo estimado 1º→2º turno por Brasil, UF e município (≥30 seções)
  geo/uf.json, geo/mun_<UF>.json   polígonos simplificados (IBGE via tbrugz/geodata-br)

Fluxo entre turnos: o voto é secreto, então não existe dado de "quem votou em quem" nos
dois turnos. O fluxo é uma ESTIMATIVA por regressão ecológica (Goodman): em cada unidade,
acha a matriz de transição (linhas somam 1, não negativa) que melhor explica o 2º turno
de cada seção a partir do 1º turno da mesma seção, ponderada pelos aptos; depois ajusta
(IPF) para bater com os totais reais dos dois turnos.

Uso: python -m auditoria_urnas.dash
"""

import json
import math
import unicodedata
from pathlib import Path

import duckdb
import numpy as np
from scipy.optimize import lsq_linear
from shapely.geometry import mapping, shape

OUT = Path("dash/public/data")
GEO = Path("data/raw/geo")
C1 = ["13", "22", "70", "14", "55", "30", "outros"]  # 1º turno: principais + outros
FLUXO_1 = ["Lula", "Flávio", "Outros", "Branco/Nulo", "Abstenção"]
FLUXO_2 = ["Lula", "Flávio", "Branco/Nulo", "Abstenção"]


def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    c.execute("set memory_limit='2GB'; set threads=2; set preserve_insertion_order=false")
    c.execute("""
      create view sec as select * from read_parquet('data/lake/urna_secao/*/*/*.parquet', hive_partitioning=true);
      create view buv as select * from read_parquet('data/lake/urna_bu_votos/*/*/*.parquet', hive_partitioning=true)
                         where cargo = 'presidente';
      create view lv  as select * from read_parquet('data/lake/urna_log_votos/*/*/*.parquet', hive_partitioning=true);
      create view fuso as select * from 'data/lake/municipio_fuso/fuso.parquet';
    """)
    return c


def base_secoes(c) -> None:
    """Uma linha por seção e turno com todas as métricas (tabela temporária m)."""
    print("seções…", flush=True)
    c.execute("""
      create temp table votos as
      select turno, prefixo,
        sum(votos) filter (where tipo_voto = 'nominal' and numero = 13) v13,
        sum(votos) filter (where tipo_voto = 'nominal' and numero = 22) v22,
        sum(votos) filter (where tipo_voto = 'nominal' and numero = 70) v70,
        sum(votos) filter (where tipo_voto = 'nominal' and numero = 14) v14,
        sum(votos) filter (where tipo_voto = 'nominal' and numero = 55) v55,
        sum(votos) filter (where tipo_voto = 'nominal' and numero = 30) v30,
        sum(votos) filter (where tipo_voto = 'nominal' and numero not in (13, 22, 70, 14, 55, 30)) vout,
        sum(votos) filter (where tipo_voto = 'branco') vbr,
        sum(votos) filter (where tipo_voto = 'nulo') vnu
      from buv group by all""")
    print("log…", flush=True)
    c.execute("""
      create temp table logs as
      select l.turno, l.prefixo, count(*) n_log,
        count(*) filter (where f.fuso_horas is not null
                           and hour(l.horario_local - to_hours(f.fuso_horas)) >= 17) apos17
      from lv l left join fuso f using (uf, cd_municipio) group by all""")
    c.execute("""
      create temp table m as
      select s.turno, s.uf, s.cd_municipio, s.zona, s.secao, s.tipo_urna,
        coalesce(s.aptos, 0) aptos, s.comparecimento comp,
        coalesce(v.v13,0) v13, coalesce(v.v22,0) v22, coalesce(v.v70,0) v70, coalesce(v.v14,0) v14,
        coalesce(v.v55,0) v55, coalesce(v.v30,0) v30, coalesce(v.vout,0) vout,
        coalesce(v.vbr,0) vbr, coalesce(v.vnu,0) vnu,
        coalesce(s.habilitados_biometria,0) bio, coalesce(s.habilitados_biografia,0) man,
        coalesce(s.sem_biometria,0) sem,
        coalesce(l.n_log,0) n_log, coalesce(l.apos17,0) apos17
      from sec s left join votos v using (turno, prefixo) left join logs l using (turno, prefixo)""")
    n = c.execute("select turno, count(*), sum(v13), sum(v22) from m group by 1 order by 1").fetchall()
    print("  ", n, flush=True)


SOMAS = ("sum(aptos) aptos, sum(comp) comp, sum(v13) v13, sum(v22) v22, sum(v70) v70, sum(v14) v14, "
         "sum(v55) v55, sum(v30) v30, sum(vout) vout, sum(vbr) vbr, sum(vnu) vnu, sum(bio) bio, "
         "sum(man) man, sum(sem) sem, sum(apos17) apos17, count(*) secoes, "
         "count(*) filter (where n_log <> comp) log_div, "
         "count(*) filter (where tipo_urna <> 'secao') contingencia")
CAMPOS = ["aptos", "comp", "v13", "v22", "v70", "v14", "v55", "v30", "vout", "vbr", "vnu", "bio", "man", "sem",
          "apos17", "secoes", "log_div", "contingencia"]


def por_turno(linhas, chave_n: int) -> dict:
    """linhas: (chave..., turno, CAMPOS...) -> {chave: {"t1": {...}, "t2": {...}}}"""
    out: dict = {}
    for r in linhas:
        k = r[:chave_n] if chave_n > 1 else r[0]
        t = f"t{r[chave_n]}"
        out.setdefault(k, {})[t] = {c: int(v or 0) for c, v in zip(CAMPOS, r[chave_n + 1:])}
    return out


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()
    return "".join(ch for ch in s if ch.isalnum())


# ---------- fluxo (regressão ecológica) ----------

def fluxo(X: np.ndarray, Y: np.ndarray, w: np.ndarray, tot1: np.ndarray, tot2: np.ndarray) -> list[list[float]]:
    """X: seções × 5 (proporções do 1º turno), Y: seções × 4 (2º turno), w: pesos (aptos)."""
    k1, k2 = X.shape[1], Y.shape[1]
    A = (X * w[:, None]).T @ X                   # 5×5
    Cm = (X * w[:, None]).T @ Y                  # 5×4
    L = np.linalg.cholesky(A + 1e-9 * np.eye(k1))
    # min Σ_j ||Lᵀ b_j − L⁻¹ c_j||², b ≥ 0, linhas de B somam 1 (restrição com peso alto)
    rows, rhs = [], []
    Linv = np.linalg.inv(L)
    for j in range(k2):
        blk = np.zeros((k1, k1 * k2))
        blk[:, j * k1:(j + 1) * k1] = L.T
        rows.append(blk)
        rhs.append(Linv @ Cm[:, j])
    peso = 1e3 * math.sqrt(np.trace(A))
    for i in range(k1):
        r = np.zeros(k1 * k2)
        r[[j * k1 + i for j in range(k2)]] = peso
        rows.append(r[None, :])
        rhs.append(np.array([peso]))
    sol = lsq_linear(np.vstack(rows), np.concatenate(rhs), bounds=(0, 1)).x
    B = sol.reshape(k2, k1).T                    # 5×4
    F = B * tot1[:, None]
    # IPF: bate as margens com os totais reais (2º turno reescalado ao total do 1º)
    alvo2 = tot2 * tot1.sum() / tot2.sum()
    for _ in range(200):
        F *= (tot1 / np.maximum(F.sum(1), 1e-9))[:, None]
        F *= (alvo2 / np.maximum(F.sum(0), 1e-9))[None, :]
    return [[round(float(v)) for v in linha] for linha in F]


def fluxos(c) -> dict:
    print("fluxo…", flush=True)
    df = c.execute("""
      select a.uf, a.cd_municipio, a.aptos w,
        a.v13, a.v22, a.v70 + a.v14 + a.v55 + a.v30 + a.vout outros, a.vbr + a.vnu bn, a.aptos - a.comp abst,
        b.v13 l2, b.v22 f2, b.vbr + b.vnu bn2, b.aptos - b.comp abst2, b.aptos aptos2
      from m a join m b on a.turno = 1 and b.turno = 2 and a.uf = b.uf and a.cd_municipio = b.cd_municipio
                          and a.zona = b.zona and a.secao = b.secao
      where a.aptos > 0 and b.aptos > 0""").fetchnumpy()
    uf, mun = df["uf"], df["cd_municipio"]
    T1 = np.column_stack([df["v13"], df["v22"], df["outros"], df["bn"], df["abst"]]).astype(float)
    T2 = np.column_stack([df["l2"], df["f2"], df["bn2"], df["abst2"]]).astype(float)
    X = T1 / df["w"][:, None]
    Y = T2 / df["aptos2"][:, None]
    w = df["w"].astype(float)

    def calc(idx):
        return {"secoes": int(idx.sum()), "f": fluxo(X[idx], Y[idx], w[idx], T1[idx].sum(0), T2[idx].sum(0))}

    out = {"rotulos_1": FLUXO_1, "rotulos_2": FLUXO_2, "BR": calc(np.ones(len(w), bool)), "uf": {}, "mun": {}}
    for u in np.unique(uf):
        out["uf"][u] = calc(uf == u)
    chaves = uf.astype(object) + ":" + mun.astype(str)
    ordem = np.argsort(chaves, kind="stable")
    cs = chaves[ordem]
    cortes = np.flatnonzero(cs[1:] != cs[:-1]) + 1
    for grupo in np.split(ordem, cortes):
        if len(grupo) >= 30:
            idx = np.zeros(len(w), bool)
            idx[grupo] = True
            out["mun"][str(mun[grupo[0]])] = calc(idx)
    print(f"   BR + {len(out['uf'])} UFs + {len(out['mun'])} municípios", flush=True)
    return out


# ---------- geografia ----------

def geo(mapa_ibge: dict) -> dict:
    """Simplifica os polígonos e devolve o centroide de cada município (código TSE)."""
    print("geo…", flush=True)
    (OUT / "geo").mkdir(parents=True, exist_ok=True)
    ufs = json.loads((GEO / "br_uf.json").read_text())
    feats = []
    for f in ufs["features"]:
        g = shape(f["geometry"]).simplify(0.02, preserve_topology=True)
        feats.append({"type": "Feature", "properties": {"uf": f["id"]}, "geometry": arred(mapping(g))})
    (OUT / "geo" / "uf.json").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    ibge_tse = {v: k for k, v in mapa_ibge.items()}
    mun = json.loads((GEO / "br_mun.json").read_text())
    por_uf: dict = {}
    centro = {}
    for f in mun["features"]:
        ibge = int(f["properties"]["id"])
        tse = ibge_tse.get(ibge)
        g = shape(f["geometry"])
        if tse is None:
            continue
        centro[tse] = (round(g.representative_point().x, 4), round(g.representative_point().y, 4))
        g = g.simplify(0.004, preserve_topology=True)
        por_uf.setdefault(UF_IBGE[str(ibge)[:2]], []).append(
            {"type": "Feature", "properties": {"tse": tse, "ibge": ibge}, "geometry": arred(mapping(g))})
    for uf, fs in por_uf.items():
        (OUT / "geo" / f"mun_{uf}.json").write_text(
            json.dumps({"type": "FeatureCollection", "features": fs}, separators=(",", ":")))
    return centro


UF_IBGE = {"11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO", "21": "MA",
           "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL", "28": "SE", "29": "BA",
           "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS",
           "51": "MT", "52": "GO", "53": "DF"}


def arred(g):
    def r(x):
        return [r(y) for y in x] if isinstance(x, (list, tuple)) and x and isinstance(x[0], (list, tuple)) \
            else [round(x[0], 4), round(x[1], 4)]
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


# ---------- locais de votação ----------

def locais(c) -> None:
    print("locais…", flush=True)
    c.execute("""
      create temp table loc as
      select uf, cd_municipio, zona, secao, nr_local, nm_local, bairro,
             case when lat between -35 and 6 then lat end lat, case when lon between -75 and -28 then lon end lon
      from 'data/lake/locais/locais.parquet' where turno = 2""")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    c = con()
    import csv
    mapa = {}
    nomes = {}
    with (GEO / "tse_ibge.csv").open() as f:
        for l in csv.DictReader(f):
            mapa[int(l["codigo_tse"])] = int(l["codigo_ibge"])
            nomes[int(l["codigo_tse"])] = (l["nome_municipio"], l["uf"], l["capital"] == "1")
    base_secoes(c)
    centro = geo(mapa)

    # candidatos
    cand = c.execute("""select NR_VOTAVEL, any_value(NM_VOTAVEL), any_value(SG_PARTIDO)
                        from read_parquet('data/lake/bu/turno=1/*/bu.parquet')
                        where DS_CARGO_PERGUNTA = 'Presidente' and NR_VOTAVEL not in (95, 96) group by 1""").fetchall()
    candidatos = {str(n): {"nome": nm.title(), "partido": p} for n, nm, p in cand}

    # nomes de município do TSE (para o exterior e para os que faltarem no mapa)
    for cd, nm, uf in c.execute("""select distinct CD_MUNICIPIO, NM_MUNICIPIO, SG_UF
                                   from read_parquet('data/lake/bu/turno=2/*/bu.parquet')""").fetchall():
        nomes.setdefault(int(cd), (nm, uf, False))

    br = por_turno(c.execute(f"select 'BR', turno, {SOMAS} from m group by all").fetchall(), 1)["BR"]
    ufs = por_turno(c.execute(f"select uf, turno, {SOMAS} from m group by all").fetchall(), 1)
    (OUT / "uf.json").write_text(json.dumps(ufs, separators=(",", ":")))
    muns = por_turno(c.execute(f"select uf, cd_municipio, turno, {SOMAS} from m group by all").fetchall(), 2)
    (OUT / "mun").mkdir(exist_ok=True)
    por_uf: dict = {}
    for (uf, cd), v in muns.items():
        nm = nomes.get(cd, (str(cd), uf, False))
        por_uf.setdefault(uf, {})[cd] = {"nome": nm[0].title(), "capital": nm[2], "ibge": mapa.get(cd),
                                         "c": centro.get(cd), **v}
    for uf, d in por_uf.items():
        (OUT / "mun" / f"{uf}.json").write_text(json.dumps(d, separators=(",", ":")))

    # seções agrupadas por local de votação
    locais(c)
    (OUT / "secoes").mkdir(exist_ok=True)
    linhas = c.execute(f"""
      select m.uf, m.cd_municipio, coalesce(l.nr_local, -1), any_value(l.nm_local), any_value(l.bairro),
             any_value(l.lat), any_value(l.lon), m.zona, m.secao, m.turno,
             {', '.join(CAMPOS[:-3])}, n_log, tipo_urna
      from m left join loc l using (uf, cd_municipio, zona, secao)
      group by all order by 1, 2, 3, 8, 9, 10""").fetchall()
    cur, buf = None, {}

    def grava():
        if cur:
            (OUT / "secoes" / f"{cur[1]}.json").write_text(json.dumps(list(buf.values()), separators=(",", ":")))

    for r in linhas:
        uf, cd, nr, nm, bairro, lat, lon, zona, secao, turno = r[:10]
        vals = r[10:]
        if (uf, cd) != cur:
            grava()
            cur, buf = (uf, cd), {}
        loc = buf.setdefault(nr, {"nr": nr, "nome": (nm or "").title(), "bairro": (bairro or "").title(),
                                  "lat": lat, "lon": lon, "secoes": {}})
        s = loc["secoes"].setdefault(f"{zona}/{secao}", {"zona": zona, "secao": secao})
        d = {k: int(v or 0) for k, v in zip(CAMPOS[:-3], vals[:-2])}
        d["log_ok"] = int(vals[-2]) == d["comp"]
        d["contingencia"] = vals[-1] != "secao"
        s[f"t{turno}"] = d
    grava()
    for f in (OUT / "secoes").glob("*.json"):  # dict de seções -> lista
        locs = json.loads(f.read_text())
        for l in locs:
            l["secoes"] = list(l["secoes"].values())
        f.write_text(json.dumps(locs, separators=(",", ":")))

    fl = fluxos(c)
    (OUT / "fluxo.json").write_text(json.dumps(fl, separators=(",", ":")))
    from datetime import datetime, timezone
    meta = {"gerado": datetime.now(timezone.utc).isoformat(timespec="minutes"), "candidatos": candidatos,
            "c1": C1, "BR": br, "nomes_uf": {}}
    (OUT / "meta.json").write_text(json.dumps(meta, separators=(",", ":"), ensure_ascii=False))
    print("ok", {p.name: p.stat().st_size for p in OUT.glob("*.json")}, flush=True)


if __name__ == "__main__":
    main()
