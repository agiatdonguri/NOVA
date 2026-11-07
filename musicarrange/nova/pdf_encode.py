"""Turn a PDF or a photo of sheet music into concert-pitch ABC.

This module only transcribes. It does not arrange and it does not transpose.
The arrange step is a separate prompt with a separate job.
"""

from __future__ import annotations

import base64
from pathlib import Path

from nova.llm import complete, extract_abc
from nova.score import Score, parse_abc

DOCUMENT_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}

ENCODE_INSTRUCTIONS = """You are transcribing original engraved sheet music into ABC notation.
The file is a score or a part: staves, clefs, key signatures, noteheads, beams, and barlines.
It may be a scan or a photo of that page. It may contain any number of parts, systems, and pages.
Transcribe only. Do not arrange, do not reduce the ensemble, and do not drop a staff.
Write concert pitch. If a part is printed for a transposing instrument, convert it to sounding pitch
and say so in the voice name, for example name="Trumpet (concert)".

One ABC voice for each independent line. A piano grand staff usually has two staves, and each staff
often has two layers: stems up and stems down. Write those as separate voices, for example
"Right hand, melody", "Right hand, accompaniment", "Left hand, upper", "Left hand, bass".
Do not use the & overlay symbol. Do not place the accompaniment after the melody in the same voice.
Use a chord like [CEG] only when those notes start together and last the same time.
Every bar in every voice must add up to the full bar. Keep ties with a hyphen, as in C2- C2.
Transcribe every notehead you can see. Do not reduce a bar of sixteenths to one chord.

Reply with one ABC document inside a single ```abc fence and no other text.
Use headers T, M, L, Q, K, then V: lines, then [V:id] music.
Notes, rests (z), chords like [CEG], and durations like A, A2, A/2, A3/2.
No lyrics, no tuplets, no repeats.
"""


def encode_instructions() -> str:
    return ENCODE_INSTRUCTIONS.strip()


def is_document(path: str | Path) -> bool:
    return Path(path).suffix.lower() in DOCUMENT_SUFFIXES


def document_input(path: str | Path) -> list[dict]:
    """OpenRouter Responses payload: page images plus the transcription instructions."""
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        pages = _pdf_pngs(file_path)
        content: list[dict] = []
        for png in pages:
            encoded = base64.b64encode(png).decode("ascii")
            content.append(
                {
                    "type": "input_image",
                    "detail": "high",
                    "image_url": f"data:image/png;base64,{encoded}",
                }
            )
        note = encode_instructions()
        if len(pages) > 1:
            note += f"\n\nThe images are pages 1 through {len(pages)}, in order. Join them into one score."
        content.append({"type": "input_text", "text": note})
    else:
        encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[suffix]
        content = [
            {
                "type": "input_image",
                "detail": "high",
                "image_url": f"data:{mime};base64,{encoded}",
            },
            {"type": "input_text", "text": encode_instructions()},
        ]
    return [{"type": "message", "role": "user", "content": content}]


def _pdf_pngs(path: Path) -> list[bytes]:
    import pymupdf

    document = pymupdf.open(path)
    if document.page_count == 0:
        raise ValueError(f"{path.name} has no pages")
    return [page.get_pixmap(dpi=200).tobytes("png") for page in document]


def encode_document(path: str | Path, model: str | None = None) -> tuple[Score, str, str]:
    """Send the file to OpenRouter and parse the ABC it returns."""
    instructions = encode_instructions()
    raw = complete(
        document_input(path),
        model,
        temperature=0.1,
        max_output_tokens=16000,
        timeout_ms=180000,
    )
    abc = extract_abc(raw)
    score = parse_abc(abc)
    if not score.title or score.title == "Untitled":
        score.title = Path(path).stem
    return score, instructions, raw


def text_pdf(text: str) -> bytes:
    """A one-page PDF with the given text. Used for a readable sample score."""
    lines = text.splitlines() or [""]
    commands = ["BT", "/F1 10 Tf", "48 760 Td", "13 TL"]
    for index, line in enumerate(lines[:52]):
        safe = (
            line.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .encode("latin-1", errors="replace")
            .decode("latin-1")
        )
        commands.append(f"({safe[:110]}) Tj")
        if index != len(lines[:52]) - 1:
            commands.append("T*")
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 5 0 R /Resources << /Font << /F1 4 0 R >> >> >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)
