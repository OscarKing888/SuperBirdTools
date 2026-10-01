from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading

import pytest

from SuperBirdUpdater.common import UpdateError
from SuperBirdUpdater.download import prepare
from SuperBirdUpdater.sources import HttpSource, RangeUnsupported


@pytest.fixture
def server(versions):
    assets, manifest = versions[2], versions[4]
    state = {"mode": "range", "requests": []}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            state["requests"].append((self.path, self.headers.get("Range")))
            if self.path == "/manifest":
                data = json.dumps(manifest).encode()
                self.send_response(200)
            elif self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/" + next(iter(manifest["assets"])))
                self.end_headers()
                return
            else:
                path = assets / self.path.lstrip("/")
                if not path.exists():
                    self.send_error(404)
                    return
                data = path.read_bytes()
                total = len(data)
                match = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
                if match and state["mode"] != "full":
                    start, end = map(int, match.groups())
                    data = data[start:end+1]
                    self.send_response(206)
                    offset = start + 1 if state["mode"] == "wrong-range" else start
                    self.send_header("Content-Range", f"bytes {offset}-{end}/{total}")
                    if state["mode"] == "corrupt":
                        data = b"x" * len(data)
                    if state["mode"] == "short":
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data[:2])
                        self.close_connection = True
                        return
                else:
                    self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{http.server_port}"
    source = HttpSource(base + "/manifest", {name: base + "/" + name for name in manifest["assets"]})
    yield source, state, base
    http.shutdown()
    http.server_close()
    worker.join()


def test_range_downloads_only_changed_files(versions, tmp_path, server):
    source, state, _ = server
    candidate = source.latest()
    changes = prepare(versions[0], candidate, source, tmp_path / "cache")
    ranges = [r for r in state["requests"] if r[1]]
    assert len(ranges) == len([e for e in changes if e["kind"] == "file"]) == 2


def test_full_download_requires_explicit_fallback(versions, tmp_path, server):
    source, state, _ = server
    state["mode"] = "full"
    with pytest.raises(RangeUnsupported):
        prepare(versions[0], versions[4], source, tmp_path / "cache")
    assert all(header for _, header in state["requests"])
    prepare(versions[0], versions[4], source, tmp_path / "cache", allow_full=True)
    assert len([r for r in state["requests"] if r[1] is None]) == 1


@pytest.mark.parametrize("mode", ["wrong-range", "corrupt", "short"])
def test_bad_responses_fail_without_changing_app(versions, tmp_path, server, mode):
    source, state, _ = server
    state["mode"] = mode
    old = (versions[0] / "SuperViewer/program.bin").read_bytes()
    with pytest.raises(UpdateError):
        prepare(versions[0], versions[4], source, tmp_path / "cache")
    assert (versions[0] / "SuperViewer/program.bin").read_bytes() == old
    assert len(state["requests"]) == 3


def test_redirect_and_missing_assets(versions, tmp_path, server):
    source, _, base = server
    source.asset_urls = {name: base + "/redirect" for name in versions[4]["assets"]}
    prepare(versions[0], versions[4], source, tmp_path / "cache")
    source.asset_urls = {}
    with pytest.raises(UpdateError, match="未上传完整"):
        source.latest()
