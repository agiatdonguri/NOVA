"""Command line. The instant arranger always runs; --llm calls OpenRouter when a key is set."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from nova.arrange import arrange, arrange_model_score
from nova.behavior import behavior_text
from nova.llm import MissingKeyError, build_prompt, chosen_model, complete, extract_abc
from nova.pdf_encode import encode_instructions, is_document
from nova.pipeline import run as run_pipeline
from nova.preview import text_report, write_output
from nova.score import load, parse_abc, score_to_abc
from nova.theory import instrument_help

ROOT = Path(__file__).resolve().parents[1]
ODE = ROOT / "examples" / "ode_to_joy.abc"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nova",
        description="Arrange a score for the instruments that showed up.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    demo = sub.add_parser("demo", help="Arrange Ode to Joy and write a preview")
    _add_output(demo)
    demo.add_argument("-e", "--ensemble", default="2 violin + trumpet + piano")
    demo.add_argument("--llm", action="store_true", help="Ask OpenRouter, if OPENROUTER_API_KEY is set")
    demo.add_argument("--model", default=None)

    arrange_cmd = sub.add_parser("arrange", help="Arrange a score for an ensemble")
    arrange_cmd.add_argument("score")
    arrange_cmd.add_argument("-e", "--ensemble", required=True)
    arrange_cmd.add_argument("--llm", action="store_true")
    arrange_cmd.add_argument("--model", default=None)
    _add_output(arrange_cmd)

    preview = sub.add_parser("preview", help="Render a score without rearranging it")
    preview.add_argument("score")
    _add_output(preview)

    prompt = sub.add_parser("prompt", help="Print a prompt and do not call the API")
    prompt.add_argument("score")
    prompt.add_argument("-e", "--ensemble", default=None)

    pipe = sub.add_parser("pipeline", help="PDF, photo, ABC, or MusicXML → parts")
    pipe.add_argument("score")
    pipe.add_argument("-e", "--ensemble", required=True)
    pipe.add_argument("--rules", action="store_true", help="After reading the file, assign parts without the model")
    pipe.add_argument("--model", default=None)
    _add_output(pipe)

    sub.add_parser("instruments", help="List instruments, ranges, and transpositions")
    sub.add_parser("selftest", help="Run the offline checks")
    serve_cmd = sub.add_parser("serve", help="Open the local Nova desk")
    serve_cmd.add_argument("--port", type=int, default=8734)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "instruments":
            print(instrument_help())
            return 0
        if args.cmd == "selftest":
            from nova.selftest import run

            run()
            return 0
        if args.cmd == "serve":
            from nova.server import serve

            serve(port=args.port)
            return 0
        if args.cmd == "prompt":
            return _print_prompt(args.score, args.ensemble)
        if args.cmd == "preview":
            score = load(args.score)
            # Show each imported voice as its own part, without reassigning lines.
            from nova.arrange import Arrangement, Part, Stave, _number_vids, _slug

            parts = []
            used: set[str] = set()
            for voice in score.voices:
                parts.append(
                    Part(
                        id=_slug(voice.name, used),
                        title=voice.name,
                        instrument_id="voice",
                        reason="Imported voice, at the written pitch.",
                        wave="sine",
                        written_key=score.key,
                        role="import",
                        source=voice.name,
                        semitone_shift=0,
                        staves=[Stave("v", voice.name, voice.clef, voice.measures, voice.measures)],
                    )
                )
            _number_vids(parts)
            arrangement = Arrangement(
                title=score.title,
                ensemble_label="preview",
                tempo=score.tempo,
                key=score.key,
                meter=score.meter,
                unit=score.unit,
                parts=parts,
                engine="Preview",
                warnings=list(score.warnings),
            )
            _finish(arrangement, args.out, args.open, None, None)
            return 0
        if args.cmd == "demo":
            score_path = ODE
            ensemble = args.ensemble
            arrange_with_model = args.llm
        elif args.cmd == "pipeline":
            score_path = args.score
            ensemble = args.ensemble
            arrange_with_model = not args.rules
        else:
            score_path = args.score
            ensemble = args.ensemble
            arrange_with_model = args.llm
        if args.cmd == "pipeline" or is_document(score_path):
            result = run_pipeline(score_path, ensemble, args.model, arrange_with_model)
            preview = _finish(
                result.arrangement, args.out, args.open, result.arrange_prompt, result.arrange_raw, result.score,
                make_pdfs=True,
            )
            _write_stages(preview.parent, result)
            return 0
        score = load(score_path)
        arrangement, prompt_text, raw = _maybe_llm(score, ensemble, arrange_with_model, args.model)
        _finish(arrangement, args.out, args.open, prompt_text, raw, score)
        return 0
    except (ValueError, FileNotFoundError, MissingKeyError, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 2


def _add_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-o", "--out", default="out")
    parser.add_argument("--open", action="store_true", help="Open the preview in a browser")


def _maybe_llm(score, ensemble: str, use_llm: bool, model: str | None):
    if not use_llm:
        return arrange(score, ensemble), None, None
    prompt_text = build_prompt(score, ensemble)
    model_name = chosen_model(model)
    raw = None
    try:
        raw = complete(prompt_text, model_name)
        parsed = parse_abc(extract_abc(raw))
        arrangement = arrange_model_score(parsed, ensemble, model_name, source_title=score.title)
        return arrangement, prompt_text, raw
    except MissingKeyError as exc:
        print(exc, file=sys.stderr)
        print("Using the instant arranger.", file=sys.stderr)
        arrangement = arrange(score, ensemble)
        arrangement.note = str(exc)
        return arrangement, prompt_text, raw
    except Exception as exc:
        print(f"OpenRouter could not be used ({exc}). Using the instant arranger.", file=sys.stderr)
        arrangement = arrange(score, ensemble)
        arrangement.note = f"OpenRouter could not be used ({exc}). Showing the instant arranger instead."
        return arrangement, prompt_text, raw


def _print_prompt(score_path: str, ensemble: str | None) -> int:
    if is_document(score_path):
        print(encode_instructions())
        if ensemble:
            print("\n--- behavior for the arrange step ---\n")
            print(behavior_text(ensemble))
        return 0
    if not ensemble:
        print("ABC and MusicXML prompts need -e ENSEMBLE.", file=sys.stderr)
        return 2
    print(build_prompt(load(score_path), ensemble))
    return 0


def _write_stages(out: Path, result) -> None:
    (out / "encoded.abc").write_text(score_to_abc(result.score), encoding="utf-8")
    if result.encode_prompt:
        (out / "encode-prompt.txt").write_text(result.encode_prompt, encoding="utf-8")
    if result.encode_raw:
        (out / "encode-raw.txt").write_text(result.encode_raw, encoding="utf-8")


def _finish(arrangement, out: str, open_browser: bool, prompt_text, raw, source=None, make_pdfs: bool = False) -> Path:
    if source is not None:
        arrangement.source_label = ", ".join(voice.name for voice in source.voices)
    source_abc = score_to_abc(source) if source is not None else None
    path = write_output(arrangement, out, prompt_text, raw, source_abc, make_pdfs=make_pdfs)
    print(text_report(arrangement))
    print(f"Preview: {path.resolve()}")
    pdf_dir = path.parent / "pdf"
    if pdf_dir.exists():
        print("PDFs:")
        for pdf in sorted(pdf_dir.glob("*.pdf")):
            print(f"  {pdf.resolve()}")
    if open_browser:
        _open(path)
    return path


def _open(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform == "win32":
        os_startfile = getattr(__import__("os"), "startfile", None)
        if os_startfile:
            os_startfile(str(path))
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
