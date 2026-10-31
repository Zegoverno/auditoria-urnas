"""Fase 3, país inteiro: fila de UF x turno (arquivos das urnas, 219 GB).

Para cada zip: baixa (com retomada), registra no manifesto, processa para o
lake, roda as checagens U1–U7 só daquela UF/turno, guarda à parte os arquivos
das seções com problema e apaga o zip. Baixa o próximo enquanto processa o atual.

Falhas de execução (rede, zip corrompido, exceção) voltam para o fim da fila,
até MAX_TENTATIVAS. Divergência nas checagens não é falha de execução: é
achado, fica registrada e a fila segue.

Estado em data/fase3_status.csv (retomável: o que está ok é pulado).
Uso: python -m auditoria_urnas.pais [--assinatura] [--so UF_T ...]
"""

import csv
import hashlib
import re
import sys
import threading
import time
import traceback
import zipfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import httpx

from auditoria_urnas import baixar, urnas

STATUS = Path("data/fase3_status.csv")
GUARDA = Path("data/raw/arqurnatot_problemas")
SQL = Path("checagens/fase3.sql")
MAX_TENTATIVAS = 4
CAMPOS = ["uf", "turno", "estado", "tentativa", "inicio", "fim", "segundos", "bytes", "secoes",
          "checagens", "detalhe"]


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[{agora()}] {msg}", flush=True)


def fila_inicial() -> list[dict]:
    q = """select url, bytes from 'data/inventario.csv'
           where url like '%arqurnatot/bu_imgbu_logjez_rdv_vscmr_2026_%.zip' order by bytes"""
    itens = []
    for url, tamanho in conectar().execute(q).fetchall():
        m = re.search(r"_(\d)t_([A-Z]{2})\.zip$", url)
        itens.append({"url": url, "bytes": int(tamanho), "turno": m[1], "uf": m[2], "tentativa": 0})
    return itens


def ja_ok() -> set[tuple[str, str]]:
    if not STATUS.exists():
        return set()
    with STATUS.open() as f:
        return {(l["uf"], l["turno"]) for l in csv.DictReader(f) if l["estado"] in ("ok", "divergencia")}


def registrar(linha: dict) -> None:
    novo = not STATUS.exists()
    with STATUS.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if novo:
            w.writeheader()
        w.writerow(linha)


# ---------- download com retomada ----------

def baixar_zip(item: dict) -> Path:
    url = item["url"]
    destino = baixar.destino(url)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists() and destino.stat().st_size == item["bytes"]:
        return destino
    tmp = destino.with_suffix(".zip.part")
    with httpx.Client(timeout=httpx.Timeout(120, read=300)) as c:
        for tentativa in range(20):
            feito = tmp.stat().st_size if tmp.exists() else 0
            if feito >= item["bytes"]:
                break
            try:
                cab = {"Range": f"bytes={feito}-"} if feito else {}
                with c.stream("GET", url, headers=cab, follow_redirects=True) as r:
                    if feito and r.status_code != 206:
                        tmp.unlink()
                        continue
                    r.raise_for_status()
                    with tmp.open("ab") as f:
                        for bloco in r.iter_bytes(1 << 22):
                            f.write(bloco)
            except (httpx.HTTPError, OSError) as e:
                log(f"  download {item['uf']} {item['turno']}t interrompido ({type(e).__name__}); retomando")
                time.sleep(min(60, 5 * (tentativa + 1)))
    if not tmp.exists() or tmp.stat().st_size != item["bytes"]:
        raise RuntimeError(f"download incompleto: {tmp.stat().st_size if tmp.exists() else 0} de {item['bytes']}")
    h = hashlib.sha512()
    with tmp.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 22), b""):
            h.update(bloco)
    try:
        with zipfile.ZipFile(tmp) as z:  # diretório central legível
            z.namelist()
    except zipfile.BadZipFile:
        tmp.unlink()  # a próxima tentativa baixa do zero
        raise
    tmp.rename(destino)
    baixar.registrar({"url": url, "arquivo": str(destino), "bytes": destino.stat().st_size,
                      "sha512": h.hexdigest(), "sha512_tse": "", "confere": "sem_hash_tse",
                      "baixado_em": agora()})
    return destino


class Prefetch(threading.Thread):
    def __init__(self, item):
        super().__init__(daemon=True)
        self.item, self.caminho, self.erro = item, None, None
        self.start()

    def run(self):
        try:
            self.caminho = baixar_zip(self.item)
        except Exception as e:
            self.erro = e


# ---------- checagens só da UF/turno ----------

# Sem limite, o DuckDB usa até 80% da RAM; nas UFs grandes (SP 1t: BU CSV de 742 MB + ~30 mi de
# votos do log) isso derrubou a VM em 2026-10-30. Limite fixo e transbordo em disco.
DUCK_MEMORIA = "2GB"
DUCK_TMP = Path("data/tmp/duckdb")


def conectar() -> duckdb.DuckDBPyConnection:
    DUCK_TMP.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(config={"memory_limit": DUCK_MEMORIA, "threads": 2,
                                  "temp_directory": str(DUCK_TMP), "preserve_insertion_order": False})


def checar(turno: str, uf: str) -> tuple[bool, str, list[str]]:
    sql = SQL.read_text()
    sql = re.sub(r"data/lake/(urna_\w+)/\*/\*/\*\.parquet", rf"data/lake/\1/turno={turno}/uf={uf}/*.parquet", sql)
    sql = sql.replace("data/lake/bu/*/*/bu.parquet", f"data/lake/bu/turno={turno}/uf={uf}/bu.parquet")
    con = conectar()
    resumo, ok, prefixos = [], True, []
    sql = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    for corpo in [s.strip() for s in sql.split(";")]:
        if not corpo:
            continue
        cur = con.execute(corpo)
        if not corpo.lower().startswith(("select", "with")):
            continue
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            nome = d["checagem"].split()[0]
            if "ok" in d:
                ok = ok and bool(d["ok"])
                resumo.append(f"{nome}={'ok' if d['ok'] else 'FALHA'}")
            else:
                extra = {k: v for k, v in d.items() if k not in ("checagem", "turno", "uf")}
                resumo.append(f"{nome}={extra}")
    # seções com divergência ou erro: para guardar os arquivos brutos
    base = f"data/lake/{{}}/turno={turno}/uf={uf}/*.parquet"
    q = f"""
      select prefixo from read_parquet('{base.format('urna_erros')}')
      union select prefixo from read_parquet('{base.format('urna_integridade')}')
            where presente and not (ok_sw and ok_hw and lista_sw_ok and lista_hw_ok)
      union select s.prefixo from read_parquet('{base.format('urna_secao')}') s
            left join (select prefixo, count(*) n from read_parquet('{base.format('urna_log_votos')}') group by 1) l
            using (prefixo) where coalesce(l.n,0) <> s.comparecimento"""
    try:
        prefixos = [r[0] for r in con.execute(q).fetchall()]
    except duckdb.Error:
        prefixos = []
    return ok, "; ".join(resumo), prefixos


def guardar(zip_path: Path, prefixos: list[str], uf: str, turno: str) -> None:
    if not prefixos:
        return
    alvo = GUARDA / f"{turno}t_{uf}"
    alvo.mkdir(parents=True, exist_ok=True)
    conj = set(prefixos[:500])
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if n.split("-", 1)[0] in conj:
                (alvo / n).write_bytes(z.read(n))


# ---------- laço ----------

def main() -> None:
    global STATUS, checar
    proc = urnas.processar_zip
    if "--assinatura" in sys.argv:  # Fase 3b: só a assinatura de hardware (assinatura.py)
        from auditoria_urnas import assinatura
        STATUS, checar, proc = Path("data/fase3b_status.csv"), assinatura.checar, assinatura.processar_zip
        sys.argv.remove("--assinatura")
    so = set(sys.argv[sys.argv.index("--so") + 1:]) if "--so" in sys.argv else None
    feitos = ja_ok()
    fila = deque(i for i in fila_inicial()
                 if (i["uf"], i["turno"]) not in feitos and (so is None or f"{i['uf']}_{i['turno']}" in so))
    log(f"fila: {len(fila)} arquivos, {sum(i['bytes'] for i in fila)/1e9:.1f} GB")
    pre = Prefetch(fila[0]) if fila else None
    while fila:
        item = fila.popleft()
        item["tentativa"] += 1
        uf, turno = item["uf"], item["turno"]
        inicio, t0 = agora(), time.time()
        linha = {"uf": uf, "turno": turno, "tentativa": item["tentativa"], "inicio": inicio, "bytes": item["bytes"]}
        try:
            log(f"{uf} {turno}t: aguardando download ({item['bytes']/1e9:.1f} GB, tentativa {item['tentativa']})")
            if pre is None or pre.item is not item:
                pre = Prefetch(item)
            pre.join()
            if pre.erro:
                raise pre.erro
            caminho = pre.caminho
            pre = Prefetch(fila[0]) if fila else None  # baixa o próximo enquanto processa este
            log(f"{uf} {turno}t: processando")
            cont = proc(caminho)
            if cont["secao"] == 0:
                raise RuntimeError(f"nenhuma seção lida: {cont}")
            ok, resumo, prefixos = checar(turno, uf)
            guardar(caminho, prefixos, uf, turno)
            caminho.unlink()
            linha.update(estado="ok" if ok else "divergencia", secoes=cont["secao"], checagens=resumo,
                         detalhe=f"erros={cont['erros']} secoes_guardadas={len(prefixos)}")
            log(f"{uf} {turno}t: {linha['estado'].upper()} {cont['secao']} seções em {time.time()-t0:.0f}s | {resumo}")
        except Exception as e:
            det = f"{type(e).__name__}: {e}"[:400]
            log(f"{uf} {turno}t: FALHOU ({det})\n{traceback.format_exc()}")
            if item["tentativa"] < MAX_TENTATIVAS:
                fila.append(item)
                estado = "falhou_reenfileirado"
            else:
                estado = "falhou_definitivo"
            linha.update(estado=estado, detalhe=det)
            if pre is None and fila:
                pre = Prefetch(fila[0])
        linha.update(fim=agora(), segundos=round(time.time() - t0))
        registrar(linha)
    log("fila vazia: fim")


if __name__ == "__main__":
    main()
