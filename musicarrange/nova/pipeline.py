"""PDF or photo → ABC → arrangement → written parts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nova.arrange import Arrangement, arrange, arrange_model_score
from nova.llm import build_prompt, chosen_model, complete, extract_abc
from nova.pdf_encode import encode_document, is_document
from nova.score import Score, load, parse_abc
from nova.theory import parse_ensemble


@dataclass
class PipelineResult:
    score: Score
    arrangement: Arrangement
    source_kind: str
    encode_prompt: str | None = None
    encode_raw: str | None = None
    arrange_prompt: str | None = None
    arrange_raw: str | None = None


def run(path: str | Path, ensemble: str, model: str | None = None, arrange_with_model: bool = True) -> PipelineResult:
    """Service entry: original score in, client ensemble out, sheet music at the end.

    The upload can be engraved sheet music (PDF or photo) with any number of staves,
    or ABC / MusicXML that is already the encoding. The ensemble string is whatever
    set of instruments the client asked for, any size. Local code writes the new
    sheet music: concert score plus one written part per player.
    """
    file_path = Path(path)
    # Reject a bad ensemble before paying for a PDF transcription.
    parse_ensemble(ensemble)
    model_name = chosen_model(model)
    encode_prompt = None
    encode_raw = None
    if is_document(file_path):
        score, encode_prompt, encode_raw = encode_document(file_path, model_name)
        source_kind = "pdf" if file_path.suffix.lower() == ".pdf" else "image"
    else:
        score = load(file_path)
        source_kind = file_path.suffix.lower().lstrip(".") or "score"

    if not arrange_with_model:
        arrangement = arrange(score, ensemble)
        arrangement.engine = "Instant arranger" if encode_raw is None else "Instant arranger after transcription"
        if encode_raw is not None:
            arrangement.note = "The file was transcribed to ABC. Parts were assigned by the instant arranger."
        return PipelineResult(score, arrangement, source_kind, encode_prompt, encode_raw)

    prompt = build_prompt(score, ensemble)
    raw = None
    try:
        raw = complete(prompt, model_name)
        parsed = parse_abc(extract_abc(raw))
        arrangement = arrange_model_score(parsed, ensemble, model_name, source_title=score.title)
    except Exception as exc:
        from nova.llm import MissingKeyError

        if isinstance(exc, MissingKeyError):
            raise
        arrangement = arrange(score, ensemble)
        arrangement.note = (
            f"The arrangement model could not be used ({exc}). "
            "Parts were assigned by the instant arranger."
        )
        if encode_raw is not None:
            arrangement.note = "The file was transcribed to ABC. " + arrangement.note
        return PipelineResult(
            score, arrangement, source_kind, encode_prompt, encode_raw, prompt, raw
        )
    if encode_raw is not None:
        arrangement.note = (
            "Transcribed the file to ABC, then arranged that encoding. "
            "Transposed parts were written locally. " + arrangement.note
        )
    return PipelineResult(
        score,
        arrangement,
        source_kind,
        encode_prompt,
        encode_raw,
        prompt,
        raw,
    )
