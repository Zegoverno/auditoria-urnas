"""Fase 3: arquivos transmitidos pelas urnas (bu, rdv, imgbu, log.jez, vota.vsc).

Para um zip de UF/turno, lê cada seção direto do zip (sem extrair) e grava em
data/lake/urna_<tabela>/turno=<t>/uf=<UF>/part.parquet:

  secao        uma linha por seção: urna, carga, versão, abertura/encerramento,
               aptos, comparecimento e forma de habilitação (BU binário)
  bu_votos     votos por seção x eleição x cargo x votável (BU binário)
  rdv_votos    recontagem do RDV: seção x eleição x cargo x tipo x número digitado
  integridade  hash SHA-512 de cada arquivo x listas assinadas SW e HW do .vsc
  log_votos    horário (local) de cada "O voto do eleitor foi computado" no dia
  log_msgs     contagem por seção de cada mensagem do log no dia (normalizada)
  log_alertas  linhas ALERTA/ERRO do dia, texto com números longos removidos

Dados pessoais: números com 11+ dígitos (título de eleitor, CPF de mesários)
são substituídos por <ID> antes de qualquer gravação.

Uso: python -m auditoria_urnas.urnas <zip> [<zip> ...]
"""

import hashlib
import io
import re
import sys
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import asn1tools
import pyarrow as pa
import pyarrow.parquet as pq

SPEC = Path(__file__).resolve().parents[2] / "spec/2026"
LAKE = Path("data/lake")
DIA = {"1": "04/10/2026", "2": "25/10/2026"}
NOME = re.compile(r"^(o\d{5}[a-z]{2}\d{13,})-(bu\.dat|rdv\.dat|imgbu\.dat|log\.jez|vota\.vsc|.*)$")
ID_LONGO = re.compile(r"\d{11,}")

_codecs = {}


def codec(nome: str):
    if nome not in _codecs:
        _codecs[nome] = asn1tools.compile_files(str(SPEC / f"{nome}.asn1"), codec="ber")
    return _codecs[nome]


def ts(s: str) -> datetime | None:
    return datetime.strptime(s, "%Y%m%dT%H%M%S") if s else None


def normaliza(msg: str) -> str:
    """Troca números por # para agrupar mensagens; preserva 'Tentativa [i] de [n]'."""
    msg = ID_LONGO.sub("<ID>", msg)
    t = re.search(r"Tentativa \[(\d)\] de \[(\d)\]", msg)
    s = re.sub(r"\d+(?:[.,]\d+)?", "#", msg)
    if t:
        s = s.replace("Tentativa [#] de [#]", f"Tentativa [{t[1]}] de [{t[2]}]")
    return s


# ---------- BU ----------

def ler_bu(dados: bytes, chave: dict) -> tuple[dict, list[dict]]:
    c = codec("bu")
    env = c.decode("EntidadeEnvelopeGenerico", dados)
    b = c.decode("EntidadeBoletimUrna", env["conteudo"])
    urna = b["urna"]
    carga = urna["correspondenciaResultado"]["carga"]
    ident_urna = urna["correspondenciaResultado"]["identificacao"]
    det = b.get("detalhamentoComparecimento") or {}
    dados_sa = b.get("dadosSecaoSA")
    abertura = encerramento = None
    if dados_sa and dados_sa[0] == "dadosSecao":
        abertura = ts(dados_sa[1].get("dataHoraAbertura"))
        encerramento = ts(dados_sa[1].get("dataHoraEncerramento"))
    r0 = b["resultadosVotacaoPorEleicao"][0]
    secao = {
        **chave,
        "fase": b["fase"],
        "tipo_urna": urna["tipoUrna"],
        "tipo_arquivo": urna["tipoArquivo"],
        "versao_votacao": urna.get("versaoVotacao"),
        "nr_urna": carga["numeroInternoUrna"],
        "serie_fv": urna.get("numeroSerieFV"),
        "serie_fc": carga["numeroSerieFC"],
        "codigo_carga": carga["codigoCarga"],
        "data_carga": ts(carga["dataHoraCarga"]),
        "gerador_midia": carga["identificadorGeradorMidia"]["nome"],
        "ident_urna": ident_urna[0],
        "n_cargas_historico": len(b.get("historicoCodigosCarga") or []),
        "abertura": abertura,
        "encerramento": encerramento,
        "emissao_bu": ts(b.get("dataHoraEmissao")),
        "aptos": r0["qtdEleitoresAptos"],
        "aptos_secao": r0.get("qtdEleitoresAptosSecao"),
        "aptos_tte": r0.get("qtdEleitoresAptosTTE"),
        "comparecimento": b["qtdEleitoresCompareceram"],
        "habilitados_biometria": det.get("qtdEleitoresHabilitadosPorBiometria"),
        "habilitados_biografia": det.get("qtdEleitoresHabilitadosPorBiografia"),
        "sem_biometria": det.get("qtdEleitoresCompareceramSemBiometria"),
    }
    votos = []
    for el in b["resultadosVotacaoPorEleicao"]:
        for rv in el["resultadosVotacao"]:
            for tc in rv["totaisVotosCargo"]:
                for vv in tc["votosVotaveis"]:
                    iv = vv.get("identificacaoVotavel") or {}
                    votos.append({
                        **chave,
                        "id_eleicao": el["idEleicao"],
                        "cargo": tc["codigoCargo"][1],
                        "tipo_voto": vv["tipoVoto"],
                        "numero": iv.get("codigo"),
                        "partido": iv.get("partido"),
                        "votos": vv["quantidadeVotos"],
                    })
    return secao, votos


# ---------- RDV ----------

def ler_rdv(dados: bytes, chave: dict) -> list[dict]:
    r = codec("rdv").decode("EntidadeResultadoRDV", dados)["rdv"]
    tipo, eleicoes = r["eleicoes"]
    cont = Counter()
    for el in eleicoes:
        for vc in el["votosCargos"]:
            for v in vc["votos"]:
                cont[(el["idEleicao"], vc["idCargo"][1], v["tipoVoto"], v.get("digitacao"))] += 1
    return [
        {**chave, "origem": tipo, "id_eleicao": e, "cargo": c, "tipo_voto": t, "digitado": d, "votos": n}
        for (e, c, t, d), n in cont.items()
    ]


# ---------- assinatura ----------

def ler_vsc(dados: bytes, arquivos: dict[str, bytes], chave: dict) -> list[dict]:
    c = codec("assinatura")
    for tipo in ("EntidadeAssinaturaEcourna", "EnvelopeCompat"):
        try:
            env, n = c.decode_with_length(tipo, dados)
            if n == len(dados):
                break
        except Exception:
            env = None
    if env is None:
        return [{**chave, "arquivo": "vota.vsc", "presente": True, "ok_sw": None, "ok_hw": None,
                 "erro": "vsc não decodificado"}]
    listas, extra = {}, {}
    for lado in ("assinaturaSW", "assinaturaHW"):
        s = env[lado]
        auto = s["autoAssinado"]
        conteudo = s["conteudoAutoAssinado"]
        extra[lado] = {
            "auto_ok": hashlib.sha512(conteudo).digest() == auto["assinatura"]["hash"],
            "alg": auto["algoritmoAssinatura"]["algoritmo"],
            "usuario": auto["usuario"]["nomeUsuario"],
        }
        listas[lado] = {f["nomeArquivo"]: f["assinatura"]["hash"]
                        for f in c.decode("Assinatura", conteudo)["arquivosAssinados"]}
    linhas = []
    for nome in sorted(set(listas["assinaturaSW"]) | set(listas["assinaturaHW"])):
        presente = nome in arquivos
        h = hashlib.sha512(arquivos[nome]).digest() if presente else None
        linhas.append({
            **chave,
            "arquivo": nome.split("-", 1)[-1],
            "presente": presente,
            "ok_sw": (h == listas["assinaturaSW"].get(nome)) if presente else None,
            "ok_hw": (h == listas["assinaturaHW"].get(nome)) if presente else None,
            "lista_sw_ok": extra["assinaturaSW"]["auto_ok"],
            "lista_hw_ok": extra["assinaturaHW"]["auto_ok"],
            "alg_sw": extra["assinaturaSW"]["alg"],
            "alg_hw": extra["assinaturaHW"]["alg"],
            "erro": None,
        })
    # arquivos publicados que não constam de nenhuma lista assinada
    for nome in arquivos:
        if nome.endswith(".vsc") or nome in listas["assinaturaSW"] or nome in listas["assinaturaHW"]:
            continue
        linhas.append({**chave, "arquivo": nome.split("-", 1)[-1], "presente": True, "ok_sw": False,
                       "ok_hw": False, "erro": "arquivo fora das listas assinadas"})
    return linhas


# ---------- log ----------

def membros_log(dados: bytes, caminho: str = ""):
    """Percorre o .jez recursivamente: urnas de contingência trazem, aninhados,
    os logs da urna original e logs arquivados (<urna><data>-NN.jez)."""
    with zipfile.ZipFile(io.BytesIO(dados)) as z:
        for nome in z.namelist():
            conteudo = z.read(nome)
            if conteudo[:2] == b"PK":
                yield from membros_log(conteudo, f"{caminho}{nome}/")
            else:
                yield f"{caminho}{nome}", conteudo.decode("latin-1")


def ler_log(dados: bytes, chave: dict, turno: str):
    dia = DIA[turno]
    votos, alertas = [], []
    msgs = defaultdict(lambda: [0, None, None])
    vistas = set()  # a mesma linha pode aparecer em mais de um pacote: chave = urna + autenticador
    for membro, texto in membros_log(dados):
        for linha in texto.splitlines():
            p = linha.split("\t")
            if len(p) < 6 or not p[0].startswith(dia):
                continue
            k_linha = (p[2], p[0], p[5])
            if k_linha in vistas:
                continue
            vistas.add(k_linha)
            quando = datetime.strptime(p[0], "%d/%m/%Y %H:%M:%S")
            nivel, urna, app, msg = p[1], p[2], p[3], p[4]
            if msg == "O voto do eleitor foi computado":
                votos.append({**chave, "urna_log": urna, "membro": membro, "horario_local": quando})
            k = (membro, urna, app, nivel, normaliza(msg))
            m = msgs[k]
            m[0] += 1
            m[1] = m[1] or quando
            m[2] = quando
            if nivel in ("ALERTA", "ERRO"):
                alertas.append({**chave, "membro": membro, "urna_log": urna, "app": app, "nivel": nivel,
                                "horario_local": quando, "mensagem": ID_LONGO.sub("<ID>", msg)})
    resumo = [{**chave, "membro": k[0], "urna_log": k[1], "app": k[2], "nivel": k[3], "mensagem": k[4],
               "n": v[0], "primeiro": v[1], "ultimo": v[2]} for k, v in msgs.items()]
    return votos, resumo, alertas


# ---------- orquestração ----------

def processar_secao(zip_path: str, prefixo: str, nomes: list[str], turno: str) -> dict:
    m = re.match(r"o(\d{5})([a-z]{2})(\d{5})(\d{4})(\d{4})", prefixo)
    chave = {"turno": int(turno), "uf": m.group(2).upper(), "cd_municipio": int(m.group(3)),
             "zona": int(m.group(4)), "secao": int(m.group(5)), "prefixo": prefixo}
    out = {k: [] for k in ("secao", "bu_votos", "rdv_votos", "integridade", "log_votos", "log_msgs",
                           "log_alertas", "erros")}
    with zipfile.ZipFile(zip_path) as z:
        arquivos = {n: z.read(n) for n in nomes}
    def tenta(nome, f):
        try:
            return f()
        except Exception as e:  # registra e segue: um arquivo ruim não derruba a UF
            out["erros"].append({**chave, "etapa": nome, "erro": f"{type(e).__name__}: {e}"[:500]})
            return None
    por_tipo = {n.split("-", 1)[1]: d for n, d in arquivos.items()}
    if "bu.dat" in por_tipo:
        r = tenta("bu", lambda: ler_bu(por_tipo["bu.dat"], chave))
        if r:
            out["secao"].append(r[0])
            out["bu_votos"] += r[1]
    if "rdv.dat" in por_tipo:
        out["rdv_votos"] += tenta("rdv", lambda: ler_rdv(por_tipo["rdv.dat"], chave)) or []
    if "vota.vsc" in por_tipo:
        out["integridade"] += tenta("vsc", lambda: ler_vsc(por_tipo["vota.vsc"], arquivos, chave)) or []
    if "log.jez" in por_tipo:
        r = tenta("log", lambda: ler_log(por_tipo["log.jez"], chave, turno))
        if r:
            out["log_votos"] += r[0]
            out["log_msgs"] += r[1]
            out["log_alertas"] += r[2]
    for t in ("bu.dat", "rdv.dat", "log.jez", "vota.vsc"):
        if t not in por_tipo:
            out["erros"].append({**chave, "etapa": "arquivos", "erro": f"ausente: {t}"})
    return out


def processar_zip(zip_path: Path, workers: int = 2) -> dict[str, int]:
    m = re.search(r"_(\d)t_([A-Z]{2})\.zip$", zip_path.name)
    turno, uf = m.group(1), m.group(2)
    grupos = defaultdict(list)
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            mm = NOME.match(n)
            if mm:
                grupos[mm.group(1)].append(n)
    tabelas = defaultdict(list)
    with ProcessPoolExecutor(workers) as pool:
        futs = [pool.submit(processar_secao, str(zip_path), p, ns, turno) for p, ns in sorted(grupos.items())]
        for i, f in enumerate(futs, 1):
            for k, linhas in f.result().items():
                tabelas[k] += linhas
            if i % 2000 == 0:
                print(f"  {uf} {turno}t: {i}/{len(futs)} seções", flush=True)
    contagem = {"secoes_no_zip": len(grupos)}
    for k, linhas in tabelas.items():
        saida = LAKE / f"urna_{k}" / f"turno={turno}" / f"uf={uf}" / "part.parquet"
        saida.parent.mkdir(parents=True, exist_ok=True)
        if linhas:
            pq.write_table(pa.Table.from_pylist(linhas), saida, compression="zstd")
        elif saida.exists():
            saida.unlink()
        contagem[k] = len(linhas)
    return contagem


def main() -> None:
    for z in sys.argv[1:]:
        print(Path(z).name, processar_zip(Path(z)), flush=True)


if __name__ == "__main__":
    main()
