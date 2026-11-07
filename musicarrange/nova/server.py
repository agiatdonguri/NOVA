"""Local desk: a front page, an empty recruitment tab, and the arrangement service."""

from __future__ import annotations

import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from nova.pipeline import run as run_pipeline
from nova.preview import text_report, write_output
from nova.score import score_to_abc

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
JOBS = ROOT / "jobs"
PAGES = {
    "/": WEB / "index.html",
    "/recruitment": WEB / "recruitment.html",
    "/arrange": WEB / "arrange.html",
}


class DeskHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in PAGES:
            self._file(PAGES[path])
            return
        status = re.match(r"^/api/jobs/([a-f0-9]{12})$", path)
        if status:
            self._job_status(status.group(1))
            return
        if path.startswith("/jobs/"):
            self._job_file(path)
            return
        candidate = (WEB / path.lstrip("/")).resolve()
        if candidate.is_file() and WEB in candidate.parents:
            self._file(candidate)
            return
        self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/arrange":
            self._json(404, {"error": "Not found"})
            return
        try:
            fields, upload = _read_form(self)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        ensemble = fields.get("ensemble", "").strip()
        if not ensemble or upload is None:
            self._json(400, {"error": "Choose a score and name the instruments."})
            return
        job_id = uuid4().hex[:12]
        folder = JOBS / job_id
        folder.mkdir(parents=True, exist_ok=True)
        source = folder / _safe_name(upload[0])
        source.write_bytes(upload[1])
        (folder / "status.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
        threading.Thread(
            target=_arrange,
            args=(folder, source, ensemble, fields.get("rules") == "1"),
            daemon=True,
        ).start()
        self._json(202, {"id": job_id})

    def _job_status(self, job_id: str) -> None:
        status_path = JOBS / job_id / "status.json"
        if not status_path.exists():
            self._json(404, {"error": "Unknown job"})
            return
        self._json(200, json.loads(status_path.read_text(encoding="utf-8")))

    def _job_file(self, path: str) -> None:
        match = re.match(r"^/jobs/([a-f0-9]{12})/(.+)$", path)
        if not match:
            self._json(404, {"error": "Not found"})
            return
        folder = (JOBS / match.group(1)).resolve()
        target = (folder / match.group(2)).resolve()
        if folder not in target.parents or not target.is_file():
            self._json(404, {"error": "Not found"})
            return
        self._file(target)

    def _file(self, path: Path) -> None:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            mime = "text/javascript"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[nova] {self.address_string()} {fmt % args}")


def _arrange(folder: Path, source: Path, ensemble: str, rules: bool) -> None:
    try:
        result = run_pipeline(source, ensemble, arrange_with_model=not rules)
        result.arrangement.source_label = ", ".join(voice.name for voice in result.score.voices)
        write_output(
            result.arrangement,
            folder,
            result.arrange_prompt,
            result.arrange_raw,
            score_to_abc(result.score),
            make_pdfs=True,
        )
        pdfs = _pdf_links(folder)
        (folder / "status.json").write_text(
            json.dumps(
                {
                    "status": "done",
                    "subtitle": f"From {result.arrangement.source_label}  →  {ensemble}",
                    "preview": f"/jobs/{folder.name}/preview.html",
                    "pdfs": pdfs,
                    "report": text_report(result.arrangement),
                }
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        (folder / "status.json").write_text(
            json.dumps({"status": "error", "error": str(exc)}),
            encoding="utf-8",
        )


def _pdf_links(folder: Path) -> list[dict[str, str]]:
    pdf_dir = folder / "pdf"
    if not pdf_dir.exists():
        return []
    preferred = ["original.pdf", "score.pdf"]
    files = sorted(
        pdf_dir.glob("*.pdf"),
        key=lambda path: (preferred.index(path.name) if path.name in preferred else 9, path.name),
    )
    links = []
    for path in files:
        if path.stem == "original":
            label = "Original score"
        elif path.stem == "score":
            label = "Full score"
        else:
            label = path.stem.replace("-", " ").title()
        links.append({"label": label, "href": f"/jobs/{folder.name}/pdf/{path.name}"})
    return links


def _read_form(handler: BaseHTTPRequestHandler) -> tuple[dict[str, str], tuple[str, bytes] | None]:
    content_type = handler.headers.get("Content-Type", "")
    if "boundary=" not in content_type:
        raise ValueError("Upload the score as a form.")
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0 or length > 30_000_000:
        raise ValueError("The score file is empty or larger than 30 MB.")
    body = handler.rfile.read(length)
    boundary = content_type.split("boundary=", 1)[1].strip().strip('"').encode()
    fields: dict[str, str] = {}
    upload = None
    for chunk in body.split(b"--" + boundary):
        if b"Content-Disposition" not in chunk:
            continue
        header, _, data = chunk.partition(b"\r\n\r\n")
        data = data.removesuffix(b"\r\n")
        disposition = header.decode("utf-8", errors="replace")
        name = _field(disposition, "name")
        filename = _field(disposition, "filename")
        if not name:
            continue
        if filename:
            upload = (filename, data)
        else:
            fields[name] = data.decode("utf-8", errors="replace")
    return fields, upload


def _field(header: str, key: str) -> str:
    match = re.search(rf'{key}="([^"]*)"', header)
    return match.group(1) if match else ""


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).name).strip("-")
    return cleaned or "score"


def serve(host: str = "127.0.0.1", port: int = 8734) -> None:
    JOBS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), DeskHandler)
    print(f"Nova is at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
