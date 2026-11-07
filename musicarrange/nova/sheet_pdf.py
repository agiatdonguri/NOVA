"""Engrave ABC into PDF files with the local sheet renderer."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ABCJS = Path(__file__).resolve().parents[1] / "web" / "vendor" / "abcjs-basic-min.js"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

_PRINT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>__TITLE__</title>
<style>
  @page { size: letter; margin: 0.6in; }
  body { margin: 0; background: white; color: black; }
  #paper { width: 720px; }
</style>
</head>
<body>
<div id="paper"></div>
<script src="__ABCJS__"></script>
<script id="abc" type="text/plain">__ABC__</script>
<script>
const abc = document.getElementById("abc").textContent;
function draw(source) {
  ABCJS.renderAbc("paper", source, {
    staffwidth: 700,
    wrap: { preferredMeasuresPerLine: 4, minSpacing: 1.4, maxSpacing: 2.2 },
    paddingbottom: 16
  });
}
try { draw(abc); } catch (err) { draw(abc.replace(/^%%score.*$/gm, "")); }
document.body.dataset.ready = document.querySelector("svg") ? "1" : "0";
</script>
</body>
</html>
"""


def chrome_available() -> bool:
    return CHROME.exists() or shutil.which("google-chrome") is not None or shutil.which("chromium") is not None


def write_sheet_pdf(abc: str, title: str, dest: Path) -> Path:
    """Write one engraved PDF. Raises if Chrome cannot print the page."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    html_path = dest.with_suffix(".html")
    html_path.write_text(_page(abc, title), encoding="utf-8")
    browser = _chrome()
    subprocess.run(
        [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            "--virtual-time-budget=12000",
            f"--print-to-pdf={dest.resolve()}",
            html_path.resolve().as_uri(),
        ],
        check=True,
        timeout=45,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not dest.exists() or dest.stat().st_size < 1000:
        raise RuntimeError(f"Chrome did not write {dest.name}")
    return dest


def write_score_pdfs(sheets: list[tuple[str, str, str]], dest_dir: Path) -> list[dict[str, str]]:
    """sheets are (file stem, title, abc). Returns relative links for the preview page."""
    written = []
    for stem, title, abc in sheets:
        path = dest_dir / f"{stem}.pdf"
        write_sheet_pdf(abc, title, path)
        written.append({"href": f"pdf/{path.name}", "label": title})
    return written


def _page(abc: str, title: str) -> str:
    safe_abc = abc.replace("</script>", "<\\/script>")
    return (
        _PRINT_HTML.replace("__TITLE__", _xml(title))
        .replace("__ABCJS__", ABCJS.resolve().as_uri())
        .replace("__ABC__", safe_abc)
    )


def _chrome() -> Path:
    if CHROME.exists():
        return CHROME
    found = shutil.which("google-chrome") or shutil.which("chromium")
    if not found:
        raise RuntimeError("Google Chrome is required to engrave PDF parts.")
    return Path(found)


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
