"""Local HTTP(S) test servers with controllable behaviour."""
import http.server
import os
import ssl
import subprocess
import tempfile
import threading
import time


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "LocalSite"
    state: dict = {}

    def log_message(self, *a):  # silence
        pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        st = self.server.state  # type: ignore[attr-defined]
        if path == "/":
            mode = st.get("mode", "ok")
            if mode == "ok":
                return self._send(200, b"<html><title>Home</title>ok</html>")
            if mode == "slow":
                time.sleep(st.get("delay", 0.5))
                return self._send(200, b"slow")
            if mode == "500":
                return self._send(500, b"boom")
            if mode == "503":
                return self._send(503, b"unavailable")
            if mode == "404":
                return self._send(404, b"missing")
        if path == "/ok":
            return self._send(200, b"ok")
        if path == "/hang":
            time.sleep(5)
            return self._send(200, b"late")
        if path == "/redirect":
            return self._redirect("/ok")
        if path == "/loop-a":
            return self._redirect("/loop-b")
        if path == "/loop-b":
            return self._redirect("/loop-a")
        if path.startswith("/chain/"):
            n = int(path.rsplit("/", 1)[1])
            return self._redirect(f"/chain/{n + 1}") if n < 10 else self._send(200, b"end")
        if path == "/to-metadata":
            return self._redirect("http://169.254.169.254/latest/meta-data/")
        if path == "/to-private":
            return self._redirect("http://10.0.0.1/")
        if path == "/to-file":
            return self._redirect("file:///etc/passwd")
        return self._send(404, b"not found")

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, loc):
        self.send_response(302)
        self.send_header("Location", loc)
        self.send_header("Content-Length", "0")
        self.end_headers()


class LocalSite:
    def __init__(self, tls: bool = False):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.state = {}
        self.httpd.daemon_threads = True
        self.tls = tls
        if tls:
            self._certdir = tempfile.mkdtemp()
            cert, key = os.path.join(self._certdir, "c.pem"), os.path.join(self._certdir, "k.pem")
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert,
                            "-days", "2", "-subj", "/CN=127.0.0.1"], check=True, capture_output=True)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def state(self) -> dict:
        return self.httpd.state

    @property
    def base(self) -> str:
        return f"{'https' if self.tls else 'http'}://127.0.0.1:{self.httpd.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()
