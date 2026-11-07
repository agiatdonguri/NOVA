"""Write ABC parts and a one-page preview with sheet music and playback."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

from nova.arrange import Arrangement
from nova.score import emit_event, emit_tune
from nova.theory import pitch_label

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>__TITLE__</title>
<style>
  body { margin: 0; font: 17px/1.45 Georgia, "Iowan Old Style", Palatino, serif; background: #f4f1ea; color: #1c1915; }
  main { max-width: 980px; margin: 0 auto; padding: 28px 18px 72px; }
  h1 { font-size: 1.8rem; margin: 0 0 4px; }
  h2 { font-size: 1.25rem; margin: 0 0 6px; }
  .sub, .reason { color: #5c564c; margin: 0 0 10px; }
  .engine { font: 13px/1.4 ui-sans-serif, system-ui, sans-serif; letter-spacing: 0.02em; text-transform: uppercase; color: #7a7368; margin: 0 0 14px; }
  .note { background: #fff6d8; border: 1px solid #e2c56a; padding: 10px 12px; border-radius: 10px; margin: 0 0 14px; white-space: pre-wrap; }
  .bar { display: flex; flex-wrap: wrap; gap: 10px 16px; align-items: center; background: white; border: 1px solid #e2d9c8; padding: 12px 14px; border-radius: 12px; }
  button { font: 15px/1 ui-sans-serif, system-ui, sans-serif; background: #1c1915; color: white; border: 0; border-radius: 999px; padding: 9px 16px; cursor: pointer; }
  a.pdf { font: 14px/1 ui-sans-serif, system-ui, sans-serif; color: #1c1915; border: 1px solid #c9c0b0; border-radius: 999px; padding: 8px 14px; text-decoration: none; background: white; }
  label.mix { display: flex; gap: 6px; align-items: center; font: 14px/1.2 ui-sans-serif, system-ui, sans-serif; }
  section { margin-top: 28px; }
  .paper { background: white; border: 1px solid #e2d9c8; border-radius: 12px; padding: 8px 8px 16px; overflow-x: auto; }
  details { margin-top: 8px; }
  pre { white-space: pre-wrap; font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; }
  .warn { display: none; background: #fff6d8; border: 1px solid #e2c56a; padding: 10px 12px; border-radius: 10px; margin-top: 12px; }
</style>
</head>
<body>
<main>
  <p class="engine" id="engine"></p>
  <h1 id="title"></h1>
  <p class="sub" id="sub"></p>
  <p class="note" id="note" hidden></p>
  <div class="bar">
    <button id="play" type="button">Play concert pitch</button>
    <button id="stop" class="secondary" type="button">Stop</button>
    <span id="mix"></span>
  </div>
  <p class="warn" id="render-warn">The sheet-music renderer did not load. The ABC is still under each part, and playback still works.</p>
  <div id="sheets"></div>
</main>
<script id="payload" type="application/json">__DATA__</script>
<script src="https://cdn.jsdelivr.net/npm/abcjs@6.4.4/dist/abcjs-basic-min.js"></script>
<script>
const data = JSON.parse(document.getElementById("payload").textContent);
document.title = data.title;
document.getElementById("title").textContent = data.title;
document.getElementById("sub").textContent = data.subtitle;
document.getElementById("engine").textContent = data.engine;
if (data.note) {
  const note = document.getElementById("note");
  note.hidden = false;
  note.textContent = data.note;
}
const mix = document.getElementById("mix");
for (const part of data.parts) {
  const label = document.createElement("label");
  label.className = "mix";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.className = "mixbox";
  box.value = part.id;
  box.checked = true;
  label.append(box, document.createTextNode(part.title));
  mix.append(label);
}
if (data.pdfs && data.pdfs.length) {
  const downloads = document.createElement("div");
  downloads.className = "bar";
  for (const pdf of data.pdfs) {
    const link = document.createElement("a");
    link.className = "pdf";
    link.href = pdf.href;
    link.download = "";
    link.textContent = "PDF · " + pdf.label;
    downloads.append(link);
  }
  document.querySelector("main").insertBefore(downloads, document.getElementById("sheets"));
}
const sheets = document.getElementById("sheets");
let rendered = 0;
for (const sheet of data.sheets) {
  const section = document.createElement("section");
  const heading = document.createElement("h2");
  heading.textContent = sheet.heading;
  const reason = document.createElement("p");
  reason.className = "reason";
  reason.textContent = sheet.reason;
  const paper = document.createElement("div");
  paper.className = "paper";
  const target = document.createElement("div");
  paper.append(target);
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "ABC";
  const pre = document.createElement("pre");
  pre.textContent = sheet.abc;
  details.append(summary, pre);
  section.append(heading, reason, paper, details);
  sheets.append(section);
  if (window.ABCJS && draw(target, sheet.abc)) rendered += 1;
}
if (!window.ABCJS || rendered === 0) document.getElementById("render-warn").style.display = "block";

function draw(el, abc) {
  const opts = { responsive: "resize", staffwidth: 740, wrap: { preferredMeasuresPerLine: 4, minSpacing: 1.5, maxSpacing: 2.4 } };
  try { ABCJS.renderAbc(el, abc, opts); } catch (err) { el.innerHTML = ""; }
  if (!el.querySelector("svg")) {
    try { ABCJS.renderAbc(el, abc.replace(/^%%score.*$/gm, ""), opts); } catch (err) { return false; }
  }
  return !!el.querySelector("svg");
}

let audio;
document.getElementById("play").addEventListener("click", () => {
  stop();
  const active = new Set([...document.querySelectorAll("input.mixbox:checked")].map((el) => el.value));
  audio = new AudioContext();
  const beat = 60 / data.tempo;
  const now = audio.currentTime + 0.08;
  for (const note of data.notes) {
    if (!active.has(note.part)) continue;
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.type = note.wave;
    osc.frequency.value = 440 * Math.pow(2, (note.midi - 69) / 12);
    const start = now + note.start * beat;
    const end = start + Math.max(0.06, note.dur * beat * 0.96);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(0.03, start + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, end);
    osc.connect(gain).connect(audio.destination);
    osc.start(start);
    osc.stop(end + 0.02);
  }
});
document.getElementById("stop").addEventListener("click", stop);
function stop() { if (audio) { audio.close(); audio = null; } }
</script>
</body>
</html>
"""


def write_output(
    arr: Arrangement,
    out_dir: str | Path,
    prompt: str | None = None,
    raw: str | None = None,
    source_abc: str | None = None,
    make_pdfs: bool = False,
) -> Path:
    out = Path(out_dir)
    parts = out / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    concert = concert_abc(arr)
    (out / "concert.abc").write_text(concert, encoding="utf-8")
    (out / "report.txt").write_text(text_report(arr), encoding="utf-8")
    if prompt:
        (out / "prompt.txt").write_text(prompt, encoding="utf-8")
    if raw:
        (out / "llm-raw.txt").write_text(raw, encoding="utf-8")
    pdf_items: list[tuple[str, str, str]] = []
    sheets: list[dict] = []
    if source_abc:
        sheets.append(
            {
                "heading": f"Original — {arr.source_label}",
                "reason": "The score before arranging. The parts below are the new instrumentation.",
                "abc": source_abc,
            }
        )
        pdf_items.append(("original", f"{arr.title} — original", source_abc))
    sheets.append(
        {
            "heading": "Arranged concert score",
            "reason": "Sounding pitch for the new ensemble. Transposing instruments have a separate written part below.",
            "abc": concert,
        }
    )
    pdf_items.append(("score", f"{arr.title} — score", concert))
    for part in arr.parts:
        abc = part_abc(arr, part)
        (parts / f"{part.id}.abc").write_text(abc, encoding="utf-8")
        sheets.append({"heading": part.title, "reason": part.reason, "abc": abc})
        pdf_items.append((part.id, part.title, abc))
    pdfs: list[dict[str, str]] = []
    if make_pdfs:
        from nova.sheet_pdf import chrome_available, write_score_pdfs

        if chrome_available():
            try:
                pdfs = write_score_pdfs(pdf_items, out / "pdf")
            except Exception as exc:
                extra = f"PDF engraving failed: {exc}"
                arr.note = f"{arr.note}\n{extra}" if arr.note else extra
    payload = {
        "title": arr.title,
        "subtitle": _from_to(arr),
        "engine": arr.engine,
        "note": arr.note,
        "tempo": arr.tempo,
        "parts": [{"id": part.id, "title": part.title} for part in arr.parts],
        "notes": sounding_notes(arr),
        "pdfs": pdfs,
        "sheets": sheets,
    }
    data = json.dumps(payload).replace("<", "\\u003c")
    html = HTML.replace("__TITLE__", _escape(arr.title)).replace("__DATA__", data)
    path = out / "preview.html"
    path.write_text(html, encoding="utf-8")
    return path


def text_report(arr: Arrangement) -> str:
    lines = [arr.title, _from_to(arr), arr.engine]
    if arr.note:
        lines.extend(["", arr.note])
    if arr.warnings:
        lines.extend(["", "Warnings: " + "; ".join(arr.warnings)])
    lines.append("")
    for part in arr.parts:
        lines.append(part.title)
        lines.append(f"  {part.reason}")
        for stave in part.staves:
            sounding = _pitch_bar(stave.sounding)
            written = " ".join(
                emit_event(event, part.written_key, arr.unit) for event in (stave.written[0] if stave.written else [])
            )
            label = stave.name if len(part.staves) > 1 else "part"
            lines.append(f"  {label} sounds {sounding}")
            lines.append(f"  {label} ABC    {written}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def concert_abc(arr: Arrangement) -> str:
    voices = []
    for part in arr.parts:
        for stave in part.staves:
            name = stave.name if part.written_key == arr.key else f"{stave.name} (concert)"
            voices.append((stave.vid, name, stave.clef, stave.sounding))
    return emit_tune(
        f"{arr.title} (concert)",
        arr.meter,
        arr.key,
        arr.tempo,
        voices,
        arr.unit,
        _score_directive(arr),
    )


def part_abc(arr: Arrangement, part) -> str:
    voices = [(stave.vid, stave.name, stave.clef, stave.written) for stave in part.staves]
    directive = None
    if len(part.staves) == 2:
        directive = "%%score { " + " ".join(stave.vid for stave in part.staves) + " }"
    title = part.title if part.written_key == arr.key else f"{part.title} (written {part.written_key})"
    return emit_tune(title, arr.meter, part.written_key, arr.tempo, voices, arr.unit, directive)


def sounding_notes(arr: Arrangement) -> list[dict]:
    notes = []
    for part in arr.parts:
        for stave in part.staves:
            start = Fraction(0)
            for bar in stave.sounding:
                for event in bar:
                    for midi in event.pitches:
                        notes.append(
                            {
                                "part": part.id,
                                "midi": midi,
                                "start": float(start * 4),
                                "dur": float(event.dur * 4),
                                "wave": part.wave,
                            }
                        )
                    start += event.dur
    return notes


def _from_to(arr: Arrangement) -> str:
    if arr.source_label:
        return f"From {arr.source_label}  →  {arr.ensemble_label}"
    return arr.ensemble_label


def _pitch_bar(measures) -> str:
    if not measures:
        return ""
    names = []
    for event in measures[0]:
        if not event.pitches:
            names.append("rest")
        else:
            names.append("+".join(pitch_label(pitch) for pitch in event.pitches))
    return " ".join(names)


def _score_directive(arr: Arrangement) -> str:
    groups = []
    for part in arr.parts:
        ids = [stave.vid for stave in part.staves]
        if len(ids) == 2:
            groups.append("{" + " ".join(ids) + "}")
        else:
            groups.append(ids[0])
    return "%%score [ " + " ".join(groups) + " ]"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
