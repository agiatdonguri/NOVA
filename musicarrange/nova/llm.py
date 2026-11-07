"""Ask OpenRouter for a concert-pitch arrangement. Parts are still written locally."""

from __future__ import annotations

import os
import re
from pathlib import Path

from nova.arrange import slots_for
from nova.behavior import behavior_text
from nova.score import Score, score_to_abc
from nova.theory import INSTRUMENTS, parse_ensemble, pitch_label

DEFAULT_MODEL = "~openai/gpt-sol-latest"
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _load_env_file() -> None:
    if os.getenv("OPENROUTER_API_KEY") or not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name and name not in os.environ:
            os.environ[name] = value.strip().strip('"').strip("'")


class MissingKeyError(RuntimeError):
    """Raised when --llm is asked for and OPENROUTER_API_KEY is unset."""


def chosen_model(model: str | None = None) -> str:
    _load_env_file()
    return model or os.getenv("OPENROUTER_MODEL") or DEFAULT_MODEL


def build_prompt(score: Score, ensemble: str) -> str:
    instruments = [INSTRUMENTS[item] for item in parse_ensemble(ensemble)]
    slots = slots_for(instruments)
    roster = []
    for number, slot in enumerate(slots, start=1):
        if slot.hand:
            span = "full piano range, this hand only"
        else:
            inst = slot.instrument
            span = (
                f"sounding {pitch_label(inst.low)}–{pitch_label(inst.high)} "
                f"(MIDI {inst.low}–{inst.high}), clef {slot.clef}"
            )
        roster.append(f'{number}. Voice name "{slot.voice_title}". {span}.')
    bars = len(score.voices[0].measures) if score.voices else 0
    source_voices = len(score.voices)
    return f"""You are arranging a score for a client.
The source has {source_voices} voice{"s" if source_voices != 1 else ""}. The client asked for {len(parse_ensemble(ensemble))} instrument{"s" if len(parse_ensemble(ensemble)) != 1 else ""}. Those two counts are independent: any number of printed parts can become any number of players.
If the source has more voices than the room, keep the tune and the bass and fold the inner voices into the players you have.
If the room has more players than the source has voices, every requested player still gets a part: split the harmony, double at the octave, or write a countermelody. Do not drop a requested player, and do not add a player who was not requested.

{behavior_text(ensemble)}

Write exactly these voices, in this order, and no others:
{chr(10).join(roster)}

Output rules:
- Reply with one ABC document inside a single ```abc fence and no other text.
- Write every voice at concert pitch. Do not transpose trumpet, clarinet, sax, or horn. Local code writes those parts.
- Keep the melody recognizable. Keep the harmony. You may change octaves and which voice plays which line.
- Stay inside the sounding ranges above.
- Keep M:{score.meter[0]}/{score.meter[1]}, L:{score.unit.numerator}/{score.unit.denominator}, and K:{score.key}.
- Write {bars} bars in every voice.
- Use only headers, V: lines, [V:id] music, notes, rests (z), chords like [CEG], and durations like A, A2, A/2, A3/2.
- No tuplets, lyrics, repeats, or text between the notes.
- The voice name= fields must be exactly the names listed above.

Source score, concert pitch:
{score_to_abc(score)}"""


def extract_abc(text: str) -> str:
    for block in re.findall(r"```(?:abc|ABC)?\s*([\s\S]*?)```", text):
        if re.search(r"(?m)^(K:|V:|\[V:)", block):
            return block.strip()
    lines = []
    started = False
    for line in text.splitlines():
        stripped = line.strip()
        if not started:
            if re.match(r"^(X:|T:|M:|L:|Q:|K:|V:|%)", stripped):
                started = True
                lines.append(line)
            continue
        if not stripped:
            lines.append(line)
            continue
        if re.match(r"^(X:|T:|M:|L:|Q:|K:|V:|w:|%%|%)", stripped) or re.search(
            r"[A-Ga-gzZ|\[\]]", stripped
        ):
            lines.append(line)
            continue
        break
    abc = "\n".join(lines).strip()
    if not re.search(r"(?m)^(K:|V:|\[V:)", abc):
        raise ValueError("model did not return ABC")
    return abc


def complete(
    prompt: str | list,
    model: str | None = None,
    *,
    temperature: float = 0.4,
    max_output_tokens: int = 4000,
    timeout_ms: int = 90000,
) -> str:
    """One OpenRouter Responses call. `prompt` is a string or a message list (for a PDF)."""
    _load_env_file()
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise MissingKeyError(
            "OPENROUTER_API_KEY is not set. When the hackathon credits are ready:\n"
            "  export OPENROUTER_API_KEY=...\n"
            '  python -m nova pipeline examples/ode_to_joy.pdf -e "2 violin + trumpet + piano"'
        )
    try:
        from openrouter import OpenRouter
    except ImportError as exc:
        raise RuntimeError("Install the SDK with: pip install 'openrouter>=1.3,<2'") from exc

    with OpenRouter(api_key=api_key) as client:
        response = client.responses.send(
            model=chosen_model(model),
            input=prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            timeout_ms=timeout_ms,
            x_open_router_title="NOVA musicarrange",
        )
    if response.status != "completed":
        message = response.error.message if response.error else f"Response ended as {response.status}"
        raise RuntimeError(message)
    if getattr(response, "output_text", None):
        return response.output_text
    message = next(item for item in response.output if getattr(item, "type", None) == "message")
    return next(part.text for part in message.content if getattr(part, "type", None) == "output_text")
