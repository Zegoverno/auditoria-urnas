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
  mesarios     por seção e mesário (apelido M1, M2... só válido dentro da seção):
               quantos eleitores liberou manualmente, se registrou, se encerrou

log_votos traz, para cada voto, a forma de habilitação do eleitor
(biometria / manual_mesario / sem_biometria) e o apelido do mesário que liberou.

Dados pessoais: números com 11+ dígitos (título de eleitor, CPF de mesários)
são substituídos por <ID> antes de qualquer gravação.

Uso: python -m auditoria_urnas.urnas <zip> [<zip> ...]
"""

import hashlib
import io
import re
import shutil
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


def hexa(v):
    return bytes(v).hex() if isinstance(v, (bytes, bytearray)) else v


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
        "serie_fv": hexa(urna.get("numeroSerieFV")),
        "serie_fc": hexa(carga["numeroSerieFC"]),
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
                        "cargo": nome_cargo(tc["codigoCargo"]),
                        "tipo_voto": vv["tipoVoto"],
                        "numero": iv.get("codigo"),
                        "partido": iv.get("partido"),
                        "votos": vv["quantidadeVotos"],
                    })
    return secao, votos


def nome_cargo(cod: tuple) -> str:
    """CHOICE cargoConstitucional (enum, já texto) ou numeroCargoConsultaLivre (inteiro 25..99,
    consultas/plebiscitos locais, ex.: MT 2t eleição 6288 cargo 26) -> 'consulta_26'."""
    tipo, valor = cod
    return valor if isinstance(valor, str) else f"consulta_{valor}"


# ---------- RDV ----------

def ler_rdv(dados: bytes, chave: dict) -> list[dict]:
    r = codec("rdv").decode("EntidadeResultadoRDV", dados)["rdv"]
    tipo, eleicoes = r["eleicoes"]
    cont = Counter()
    for el in eleicoes:
        for vc in el["votosCargos"]:
            for v in vc["votos"]:
                cont[(el["idEleicao"], nome_cargo(vc["idCargo"]), v["tipoVoto"], v.get("digitacao"))] += 1
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


def hora(s: str) -> datetime:
    # 'dd/mm/aaaa hh:mm:ss' (strptime é ~10x mais lento)
    return datetime(int(s[6:10]), int(s[3:5]), int(s[0:2]), int(s[11:13]), int(s[14:16]), int(s[17:19]))


MESARIO = re.compile(r"[Mm]esário (\d{11,})")


class Ciclo:
    """Estado da habilitação do eleitor corrente, por urna (o log é sequencial)."""
    __slots__ = ("manual", "biometrica", "ano_nasc", "mesario", "tentativas", "inicio")

    def __init__(self, inicio=None):
        self.manual = self.biometrica = self.ano_nasc = False
        self.mesario = None
        self.tentativas = 0
        self.inicio = inicio

    def tipo(self) -> str:
        if self.manual:
            return "manual_mesario"      # tinha biometria, digital falhou, mesário liberou (BU: biografia)
        if self.biometrica:
            return "biometria"
        if self.ano_nasc:
            return "sem_biometria"       # sem biometria cadastrada, conferido ano de nascimento
        return "indeterminado"


def ler_log(dados: bytes, chave: dict, turno: str):
    dia = DIA[turno]
    votos, alertas = [], []
    msgs = defaultdict(lambda: [0, None, None])
    vistas = set()  # a mesma linha pode aparecer em mais de um pacote: chave = urna + hora + autenticador
    pseud: dict[str, str] = {}  # título do mesário -> M1, M2... (só dentro da seção; o título nunca é gravado)
    mes = defaultdict(lambda: {"habilitou": 0, "registrado": False, "eleitor_da_secao": None,
                               "encerrou": False, "primeiro": None, "ultimo": None})
    ciclos: dict[str, Ciclo] = {}

    def apelido(titulo: str) -> str:
        if titulo not in pseud:
            pseud[titulo] = f"M{len(pseud) + 1}"
        return pseud[titulo]

    for membro, texto in membros_log(dados):
        i = texto.find(dia)
        if i < 0:
            continue
        for linha in texto[i:].splitlines():
            if not linha.startswith(dia):
                continue
            p = linha.split("\t")
            if len(p) < 6:
                continue
            k_linha = (p[2], p[0], p[5])
            if k_linha in vistas:
                continue
            vistas.add(k_linha)
            quando = hora(p[0])
            nivel, urna, app, msg = p[1], p[2], p[3], p[4]
            if app == "VOTA":
                c = ciclos.get(urna)
                if msg.startswith("Aguardando digitação do identificador") or c is None:
                    c = ciclos[urna] = Ciclo(quando)
                if msg.startswith("Tipo de habilitação do eleitor [biom"):
                    c.biometrica = True
                elif msg.startswith("Solicitação de dado pessoal do eleitor para habilitação manual"):
                    c.manual = True
                elif msg.startswith("Ano de nascimento digitado é igual"):
                    c.ano_nasc = True
                elif msg.startswith("Capturada a digital. Tentativa"):
                    c.tentativas += 1
                elif msg.startswith("Digital coletada bate com uma digital gravada após"):
                    c.mesario = "nao_associavel"
                elif msg.startswith("Não encontrou digital coletada"):
                    c.mesario = "digital_nova"
                m = MESARIO.search(msg)
                if m:
                    a = apelido(m[1])
                    d = mes[a]
                    d["primeiro"] = d["primeiro"] or quando
                    d["ultimo"] = quando
                    if "habilitou o eleitor" in msg:
                        d["habilitou"] += 1
                        c.mesario = a
                    elif msg.endswith("registrado"):
                        d["registrado"] = True
                    elif "não é eleitor da seção" in msg:
                        d["eleitor_da_secao"] = False
                    elif "é eleitor da seção" in msg:
                        d["eleitor_da_secao"] = True
                elif msg.startswith("Título digitado para encerramento"):
                    t = ID_LONGO.search(msg)
                    if t and t[0] in pseud:
                        mes[pseud[t[0]]]["encerrou"] = True
                if msg == "O voto do eleitor foi computado":
                    votos.append({**chave, "urna_log": urna, "membro": membro, "horario_local": quando,
                                  "habilitacao": c.tipo(), "mesario": c.mesario if c.manual else None,
                                  "tentativas_digital": c.tentativas,
                                  "segundos_ciclo": int((quando - c.inicio).total_seconds()) if c.inicio else None})
                    ciclos[urna] = Ciclo(None)
            k = (membro, urna, app, nivel, normaliza(msg))
            m2 = msgs[k]
            m2[0] += 1
            m2[1] = m2[1] or quando
            m2[2] = quando
            if nivel in ("ALERTA", "ERRO"):
                alertas.append({**chave, "membro": membro, "urna_log": urna, "app": app, "nivel": nivel,
                                "horario_local": quando, "mensagem": ID_LONGO.sub("<ID>", msg)})
    resumo = [{**chave, "membro": k[0], "urna_log": k[1], "app": k[2], "nivel": k[3], "mensagem": k[4],
               "n": v[0], "primeiro": v[1], "ultimo": v[2]} for k, v in msgs.items()]
    mesarios = [{**chave, "mesario": a, **d} for a, d in mes.items()]
    return votos, resumo, alertas, mesarios


# ---------- orquestração ----------

TABELAS = ("secao", "bu_votos", "rdv_votos", "integridade", "log_votos", "log_msgs", "log_alertas",
           "mesarios", "erros")
K = [("turno", pa.int64()), ("uf", pa.string()), ("cd_municipio", pa.int64()), ("zona", pa.int64()),
     ("secao", pa.int64()), ("prefixo", pa.string())]
S, I, B, T = pa.string(), pa.int64(), pa.bool_(), pa.timestamp("us")
SCHEMAS = {k: pa.schema(K + v) for k, v in {
    "secao": [("fase", S), ("tipo_urna", S), ("tipo_arquivo", S), ("versao_votacao", S), ("nr_urna", I),
              ("serie_fv", S), ("serie_fc", S), ("codigo_carga", S), ("data_carga", T), ("gerador_midia", S),
              ("ident_urna", S), ("n_cargas_historico", I), ("abertura", T), ("encerramento", T),
              ("emissao_bu", T), ("aptos", I), ("aptos_secao", I), ("aptos_tte", I), ("comparecimento", I),
              ("habilitados_biometria", I), ("habilitados_biografia", I), ("sem_biometria", I)],
    "bu_votos": [("id_eleicao", I), ("cargo", S), ("tipo_voto", S), ("numero", I), ("partido", I), ("votos", I)],
    "rdv_votos": [("origem", S), ("id_eleicao", I), ("cargo", S), ("tipo_voto", S), ("digitado", S), ("votos", I)],
    "integridade": [("arquivo", S), ("presente", B), ("ok_sw", B), ("ok_hw", B), ("lista_sw_ok", B),
                    ("lista_hw_ok", B), ("alg_sw", S), ("alg_hw", S), ("erro", S)],
    "log_votos": [("urna_log", S), ("membro", S), ("horario_local", T), ("habilitacao", S), ("mesario", S),
                  ("tentativas_digital", I), ("segundos_ciclo", I)],
    "log_msgs": [("membro", S), ("urna_log", S), ("app", S), ("nivel", S), ("mensagem", S), ("n", I),
                 ("primeiro", T), ("ultimo", T)],
    "log_alertas": [("membro", S), ("urna_log", S), ("app", S), ("nivel", S), ("horario_local", T), ("mensagem", S)],
    "mesarios": [("mesario", S), ("habilitou", I), ("registrado", B), ("eleitor_da_secao", B), ("encerrou", B),
                 ("primeiro", T), ("ultimo", T)],
    "erros": [("etapa", S), ("erro", S)],
}.items()}

_zip = None


def _abre(zip_path: str) -> None:
    global _zip
    _zip = zipfile.ZipFile(zip_path)


def processar_secao(args) -> dict:
    prefixo, nomes, turno = args
    m = re.match(r"o(\d{5})([a-z]{2})(\d{5})(\d{4})(\d{4})", prefixo)
    chave = {"turno": int(turno), "uf": m.group(2).upper(), "cd_municipio": int(m.group(3)),
             "zona": int(m.group(4)), "secao": int(m.group(5)), "prefixo": prefixo}
    out = {k: [] for k in TABELAS}

    def tenta(nome, f):
        try:
            return f()
        except Exception as e:  # registra e segue: um arquivo ruim não derruba a UF
            out["erros"].append({**chave, "etapa": nome, "erro": f"{type(e).__name__}: {e}"[:500]})
            return None

    arquivos = {}
    for n in nomes:
        d = tenta(f"zip:{n}", lambda: _zip.read(n))
        if d is not None:
            arquivos[n] = d
    # seções apuradas pelo Sistema de Apuração (cédula de papel) usam busa/logsa/sa.vsc
    ALIAS = {"busa.dat": "bu.dat", "logsa.jez": "log.jez", "sa.vsc": "vota.vsc"}
    por_tipo = {ALIAS.get(n.split("-", 1)[1], n.split("-", 1)[1]): d for n, d in arquivos.items()}
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
            out["mesarios"] += r[3]
    for t in ("bu.dat", "rdv.dat", "log.jez", "vota.vsc"):
        if t not in por_tipo:
            out["erros"].append({**chave, "etapa": "arquivos", "erro": f"ausente: {t}"})
    return out


def processar_zip(zip_path: Path, workers: int = 2, lote: int = 2000) -> dict[str, int]:
    m = re.search(r"_(\d)t_([A-Z]{2})\.zip$", zip_path.name)
    turno, uf = m.group(1), m.group(2)
    grupos = defaultdict(list)
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            mm = NOME.match(n)
            if mm:
                grupos[mm.group(1)].append(n)
    # grava em diretório temporário e troca no fim: uma falha no meio não deixa partição pela metade
    tmp = {k: LAKE.parent / "tmp_lake" / f"urna_{k}" / f"turno={turno}" / f"uf={uf}" for k in TABELAS}
    for d in tmp.values():
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    writers = {k: pq.ParquetWriter(tmp[k] / "part.parquet", SCHEMAS[k], compression="zstd") for k in TABELAS}
    contagem = {"secoes_no_zip": len(grupos), **{k: 0 for k in TABELAS}}
    buf = {k: [] for k in TABELAS}

    def descarrega():
        for k, linhas in buf.items():
            if linhas:
                writers[k].write_table(pa.Table.from_pylist(linhas, schema=SCHEMAS[k]))
                contagem[k] += len(linhas)
                linhas.clear()

    tarefas = [(p, ns, turno) for p, ns in sorted(grupos.items())]
    try:
        with ProcessPoolExecutor(workers, initializer=_abre, initargs=(str(zip_path),)) as pool:
            for i, r in enumerate(pool.map(processar_secao, tarefas, chunksize=16), 1):
                for k, linhas in r.items():
                    buf[k] += linhas
                if i % lote == 0:
                    descarrega()
                    print(f"  {uf} {turno}t: {i}/{len(tarefas)} seções", flush=True)
        descarrega()
    finally:
        for w in writers.values():
            w.close()
    for k in TABELAS:
        final = LAKE / f"urna_{k}" / f"turno={turno}" / f"uf={uf}"
        if final.exists():
            shutil.rmtree(final)
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp[k].rename(final)
    return contagem


def main() -> None:
    for z in sys.argv[1:]:
        print(Path(z).name, processar_zip(Path(z)), flush=True)


if __name__ == "__main__":
    main()
