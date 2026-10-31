"""Verificação criptográfica da assinatura de hardware (HW) das urnas.

O .vsc traz duas assinaturas: SW (CEPESC, algoritmo sigiloso do governo, não
verificável) e HW, feita pelo módulo de segurança da urna:
  - urnas até UE2015: ECDSA P-521 (secp521r1), certificado X.509 emitido pela "AC URNA";
  - UE2020/UE2022: EdDSA na curva E-521 (OID 1.3.6.1.4.1.44588.2.1, norma ITI
    DOC-ICP-01.01), certificado emitido pela "AC UE2020"/"AC UE2022".

Mensagem assinada, para cada arquivo: SHA-512(h), onde h = SHA-512(arquivo) é o hash
gravado na lista (hash duplo). Isso vale para os dois algoritmos. EdDSA "puro" com
H = SHAKE256("SigEd521" 00 00 || R || A || M, 132 bytes), como em
github.com/epicleet/var-ue (de onde também vêm os certificados das ACs, em spec/certs/,
originalmente do pacote publicado pelo TSE; não temos a raiz "AC RAIZ UE" para fechar a cadeia).

Uso: python -m auditoria_urnas.assinatura <arquivo.vsc> (com os arquivos da seção ao lado)
"""

import hashlib
import sys
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

OID_ED521 = "1.3.6.1.4.1.44588.2.1"
CERTS = Path(__file__).resolve().parents[2] / "spec" / "certs"

# ---------- E-521 (x² + y² = 1 + d·x²·y², p = 2^521 − 1) ----------
P = 2**521 - 1
D = -376014
N = int("1716199415032652428745475199770348304317358825035826352348615864796385795849413675475876651663657849636693659065234142604319282948702542317993421293670108523")
G = (int("1571054894184995387535939749894317568645297350402905821437625181152304994381188529632591196067604100772673927915114267193389905003276673749012051148356041324"), 12)


def _add(p, q):  # coordenadas estendidas (X, Y, Z, T): sem inversão por soma
    X1, Y1, Z1, T1 = p
    X2, Y2, Z2, T2 = q
    A, B = X1 * X2 % P, Y1 * Y2 % P
    C, DD = D * T1 * T2 % P, Z1 * Z2 % P
    E = ((X1 + Y1) * (X2 + Y2) - A - B) % P
    F, Gg, H = (DD - C) % P, (DD + C) % P, (B - A) % P
    return (E * F % P, Gg * H % P, F * Gg % P, E * H % P)


def _mul(k: int, p):
    r = (0, 1, 1, 0)
    while k:
        if k & 1:
            r = _add(r, p)
        p = _add(p, p)
        k >>= 1
    return r


_TABELAS: dict = {}


def _mul_fixo(k: int, chave, p):
    """k·p com tabela de p·2^i pré-calculada (só somas): para G e chaves das ACs, que se repetem."""
    t = _TABELAS.get(chave)
    if t is None:
        t, q = [], p
        for _ in range(530):
            t.append(q)
            q = _add(q, q)
        _TABELAS[chave] = t
    r, i = (0, 1, 1, 0), 0
    while k:
        if k & 1:
            r = _add(r, t[i])
        k >>= 1
        i += 1
    return r


def _ext(xy):
    return (xy[0], xy[1], 1, xy[0] * xy[1] % P)


def _iguais(p, q) -> bool:
    return (p[0] * q[2] - q[0] * p[2]) % P == 0 and (p[1] * q[2] - q[1] * p[2]) % P == 0


def _decodifica(b: bytes):
    if len(b) != 66:
        return None
    sinal = b[-1] >> 7
    y = int.from_bytes(b[:-1] + bytes([b[-1] & 0x7F]), "little")
    if y >= P:
        return None
    x2 = (1 - y * y) * pow(1 - D * y * y, -1, P) % P
    x = pow(x2, (P + 1) // 4, P)
    if x * x % P != x2:
        return None
    if x & 1 != sinal:
        x = P - x
    return (x, y)


def ed521_verifica(pub: bytes, msg: bytes, sig: bytes, pub_fixa: bool = False) -> bool:
    if len(sig) != 132:
        return False
    A, R = _decodifica(pub), _decodifica(sig[:66])
    s = int.from_bytes(sig[66:], "little")
    if A is None or R is None or s >= N:
        return False
    k = int.from_bytes(hashlib.shake_256(b"SigEd521\x00\x00" + sig[:66] + pub + msg).digest(132), "little") % N
    kA = _mul_fixo(4 * k % (8 * N), pub, _ext(A)) if pub_fixa else _mul(4 * k, _ext(A))
    return _iguais(_mul_fixo(4 * s, "G", _ext(G)), _add(_mul(4, _ext(R)), kA))


# ---------- certificados ----------

def _spki_ed521(tbs: bytes) -> bytes:
    marca = bytes.fromhex("300c060a2b0601040182dc2c0201034300")  # AlgorithmIdentifier E-521 + BIT STRING(67)
    i = tbs.find(marca)
    if i < 0:
        raise ValueError("chave E-521 não encontrada no certificado")
    return tbs[i + len(marca): i + len(marca) + 66]


class Chave:
    def __init__(self, cert: x509.Certificate):
        self.cert = cert
        self.cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
        try:  # P-521 a biblioteca entende; E-521 não
            self.pub, self.ed521 = cert.public_key(), False
        except Exception:
            self.pub, self.ed521 = _spki_ed521(cert.tbs_certificate_bytes), True

    fixa = False  # chave de AC: usa tabela pré-calculada

    def verifica(self, msg: bytes, sig: bytes, ja_hash: bool = True) -> bool:
        """msg = SHA-512(h). No ECDSA ela já é o digest assinado (Prehashed); no EdDSA é a mensagem."""
        if self.ed521:
            return ed521_verifica(self.pub, msg, sig, self.fixa)
        alg = utils.Prehashed(hashes.SHA512()) if ja_hash else hashes.SHA512()
        try:
            self.pub.verify(sig, msg, ec.ECDSA(alg))
            return True
        except InvalidSignature:
            return False


def carrega_cert(dados: bytes) -> x509.Certificate:
    dados = bytes(dados)
    if dados[:1] == b"\x30" and dados[1] == 0x82:  # DER com bytes de sobra no fim: corta pelo tamanho
        dados = dados[: 4 + int.from_bytes(dados[2:4], "big")]
    return x509.load_pem_x509_certificate(dados) if dados.startswith(b"-----") else x509.load_der_x509_certificate(dados)


_ACS: dict[str, Chave] = {}


def acs() -> dict[str, Chave]:
    if not _ACS:
        for f in sorted(CERTS.glob("*.der")):
            k = Chave(x509.load_der_x509_certificate(f.read_bytes()))
            k.fixa = True
            _ACS[k.cn] = k
    return _ACS


def cert_ok(urna: Chave) -> bool | None:
    """A AC conhecida assinou o certificado da urna? None se a AC não está em spec/certs."""
    emissor = urna.cert.issuer.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    ac = acs().get(emissor)
    if ac is None:
        return None
    tbs, sig = urna.cert.tbs_certificate_bytes, urna.cert.signature
    # ECDSA: o tipo ecdsa-with-SHA512 já faz o hash; EdDSA: assina o TBS inteiro
    return ac.verifica(tbs, sig, ja_hash=False)


def verifica_hw(env_hw: dict, conteudo: dict, arquivos: dict[str, bytes], por_arquivo: bool = False) -> dict:
    """env_hw: assinaturaHW decodificada; conteudo: 'Assinatura' decodificada do conteudoAutoAssinado."""
    urna = Chave(carrega_cert(env_hw["certificadoDigital"]))
    auto = env_hw["autoAssinado"]["assinatura"]
    res = {
        "certificado_cn": urna.cn,
        "algoritmo": "ed521" if urna.ed521 else "ecdsa_p521",
        "cert_ok": cert_ok(urna),
        "lista_ok": urna.verifica(hashlib.sha512(bytes(auto["hash"])).digest(), bytes(auto["assinatura"]))
        and hashlib.sha512(bytes(env_hw["conteudoAutoAssinado"])).digest() == bytes(auto["hash"]),
        "arquivos": {},
    }
    for item in conteudo["arquivosAssinados"]:
        nome, a = item["nomeArquivo"], item["assinatura"]
        if nome not in arquivos:
            continue
        h = hashlib.sha512(arquivos[nome]).digest()
        ok = h == bytes(a["hash"])  # a lista assinada já contém o hash: basta conferir contra ela
        if por_arquivo:  # assinatura individual do arquivo (redundante, mas independente)
            ok = ok and urna.verifica(hashlib.sha512(h).digest(), bytes(a["assinatura"]))
        res["arquivos"][nome] = ok
    return res


# ---------- país: um zip de UF/turno -> data/lake/urna_assinatura ----------

def _secao(args) -> list[dict]:
    from auditoria_urnas.urnas import codec
    import re
    import zipfile
    zip_path, prefixo, nomes, turno = args
    m = re.match(r"o(\d{5})([a-z]{2})(\d{5})(\d{4})(\d{4})", prefixo)
    base = {"turno": int(turno), "uf": m[2].upper(), "cd_municipio": int(m[3]), "zona": int(m[4]),
            "secao": int(m[5]), "prefixo": prefixo}
    global _Z
    if _Z is None or _Z.filename != zip_path:
        _Z = zipfile.ZipFile(zip_path)
    c = codec("assinatura")
    linhas = []
    for vsc in [n for n in nomes if n.endswith(".vsc")]:
        linha = {**base, "vsc": vsc.split("-", 1)[1], "algoritmo": None, "certificado_cn": None, "emissor": None,
                 "cert_ok": None, "lista_ok": None, "arquivos": 0, "arquivos_ok": 0, "erro": None}
        try:
            env = c.decode("EnvelopeCompat", _Z.read(vsc))
            hw = env["assinaturaHW"]
            conteudo = c.decode("Assinatura", hw["conteudoAutoAssinado"])
            arquivos = {n: _Z.read(n) for n in nomes if n != vsc and not n.endswith(".vsc")}
            r = verifica_hw(hw, conteudo, arquivos)
            cert = carrega_cert(hw["certificadoDigital"])
            linha.update(algoritmo=r["algoritmo"], certificado_cn=r["certificado_cn"],
                         emissor=cert.issuer.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value,
                         cert_ok=r["cert_ok"], lista_ok=r["lista_ok"], arquivos=len(r["arquivos"]),
                         arquivos_ok=sum(r["arquivos"].values()))
        except Exception as e:
            linha["erro"] = f"{type(e).__name__}: {e}"[:300]
        linhas.append(linha)
    return linhas


_Z = None


def processar_zip(zip_path: Path, workers: int = 2) -> dict:
    import re
    import shutil
    import zipfile
    from collections import defaultdict
    from concurrent.futures import ProcessPoolExecutor
    import pyarrow as pa
    import pyarrow.parquet as pq
    from auditoria_urnas.urnas import NOME, LAKE
    mm = re.search(r"_(\d)t_([A-Z]{2})\.zip$", zip_path.name)
    turno, uf = mm[1], mm[2]
    grupos = defaultdict(list)
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            m = NOME.match(n)
            if m:
                grupos[m[1]].append(n)
    with ProcessPoolExecutor(workers) as pool:
        linhas = [l for r in pool.map(_secao, [(str(zip_path), p, ns, turno) for p, ns in sorted(grupos.items())],
                                       chunksize=64) for l in r]
    final = LAKE / "urna_assinatura" / f"turno={turno}" / f"uf={uf}"
    if final.exists():
        shutil.rmtree(final)
    final.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(linhas), final / "part.parquet", compression="zstd")
    return {"secoes_no_zip": len(grupos), "secao": len({l["prefixo"] for l in linhas}),
            "erros": sum(1 for l in linhas if l["erro"])}


def checar(turno: str, uf: str) -> tuple[bool, str, list[str]]:
    import duckdb
    t = f"data/lake/urna_assinatura/turno={turno}/uf={uf}/*.parquet"
    ok_expr = "erro is null and cert_ok and lista_ok and arquivos_ok = arquivos"
    r = duckdb.execute(f"""select count(*), count(*) filter (where {ok_expr}),
        count(*) filter (where algoritmo = 'ed521'), count(*) filter (where algoritmo = 'ecdsa_p521'),
        count(*) filter (where cert_ok is null and erro is null) from read_parquet('{t}')""").fetchone()
    ruins = [x[0] for x in duckdb.execute(f"select distinct prefixo from read_parquet('{t}') where not coalesce({ok_expr}, false)").fetchall()]
    resumo = f"A1 urnas={r[0]} ok={r[1]} ed521={r[2]} ecdsa={r[3]} ac_desconhecida={r[4]}"
    return r[0] == r[1], resumo, ruins


def main() -> None:
    from auditoria_urnas.urnas import codec
    c = codec("assinatura")
    for f in map(Path, sys.argv[1:]):
        env = c.decode("EnvelopeCompat", f.read_bytes())
        hw = env["assinaturaHW"]
        conteudo = c.decode("Assinatura", hw["conteudoAutoAssinado"])
        arquivos = {p.name: p.read_bytes() for p in f.parent.glob(f.name.split("-")[0] + "-*") if p != f}
        print(f.name, verifica_hw(hw, conteudo, arquivos, por_arquivo=True))


if __name__ == "__main__":
    main()
