"""Servidor estático do dashboard com gzip (arquivos .gz pré-comprimidos ao lado). Uso: python dash/servir.py [porta]"""
import gzip
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).parent / "public"


class H(SimpleHTTPRequestHandler):
    def send_head(self):
        caminho = Path(self.translate_path(self.path))
        gz = caminho.with_name(caminho.name + ".gz")
        if caminho.suffix == ".json" and gz.exists() and "gzip" in self.headers.get("Accept-Encoding", ""):
            f = gz.open("rb")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(gz.stat().st_size))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return f
        return super().send_head()

    def end_headers(self):
        if not self.path.endswith(".json"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    for p in RAIZ.rglob("*.json"):  # (re)comprime o que mudou
        gz = p.with_name(p.name + ".gz")
        if not gz.exists() or gz.stat().st_mtime < p.stat().st_mtime:
            gz.write_bytes(gzip.compress(p.read_bytes(), 6))
    porta = int(sys.argv[1]) if len(sys.argv) > 1 else 4500
    ThreadingHTTPServer(("0.0.0.0", porta), partial(H, directory=str(RAIZ))).serve_forever()
