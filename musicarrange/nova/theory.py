"""Keys, spelling, and the instruments a jam session might bring."""

from __future__ import annotations

import re
from dataclasses import dataclass

NATURAL = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Letter -> semitone offset from natural, for each major key signature.
SIG: dict[str, dict[str, int]] = {
    "C": {},
    "G": {"F": 1},
    "D": {"F": 1, "C": 1},
    "A": {"F": 1, "C": 1, "G": 1},
    "E": {"F": 1, "C": 1, "G": 1, "D": 1},
    "B": {"F": 1, "C": 1, "G": 1, "D": 1, "A": 1},
    "F#": {"F": 1, "C": 1, "G": 1, "D": 1, "A": 1, "E": 1},
    "C#": {"F": 1, "C": 1, "G": 1, "D": 1, "A": 1, "E": 1, "B": 1},
    "F": {"B": -1},
    "Bb": {"B": -1, "E": -1},
    "Eb": {"B": -1, "E": -1, "A": -1},
    "Ab": {"B": -1, "E": -1, "A": -1, "D": -1},
    "Db": {"B": -1, "E": -1, "A": -1, "D": -1, "G": -1},
    "Gb": {"B": -1, "E": -1, "A": -1, "D": -1, "G": -1, "C": -1},
}

SHARP_KEYS = {"C", "G", "D", "A", "E", "B", "F#", "C#"}
FLAT_KEYS = {"F", "Bb", "Eb", "Ab", "Db", "Gb"}

ENHARMONIC: dict[int, list[str]] = {
    0: ["C"],
    1: ["C#", "Db"],
    2: ["D"],
    3: ["Eb"],
    4: ["E"],
    5: ["F"],
    6: ["F#", "Gb"],
    7: ["G"],
    8: ["Ab"],
    9: ["A"],
    10: ["Bb"],
    11: ["B"],
}

RELATIVE_MAJOR = {
    "A": "C",
    "E": "G",
    "B": "D",
    "F#": "A",
    "C#": "E",
    "G#": "B",
    "D#": "F#",
    "D": "F",
    "G": "Bb",
    "C": "Eb",
    "F": "Ab",
    "Bb": "Db",
    "Eb": "Gb",
    "Ab": "B",
}

FIFTHS_TO_KEY = {
    0: "C",
    1: "G",
    2: "D",
    3: "A",
    4: "E",
    5: "B",
    6: "F#",
    7: "C#",
    -1: "F",
    -2: "Bb",
    -3: "Eb",
    -4: "Ab",
    -5: "Db",
    -6: "Gb",
    -7: "B",
}

KEY_PC = {
    name: (NATURAL[name[0]] + (1 if "#" in name else -1 if "b" in name else 0)) % 12
    for name in SIG
}

_PITCH_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def pitch_label(midi: int) -> str:
    return f"{_PITCH_NAMES[midi % 12]}{midi // 12 - 1}"


def canonical_key(raw: str) -> str:
    token = raw.strip().split()[0]
    match = re.match(r"^([A-G])([#b]?)(m|min|minor|maj|major)?$", token)
    if not match:
        raise ValueError(f"unsupported key signature: {raw}")
    name = match.group(1) + (match.group(2) or "")
    minor = (match.group(3) or "").startswith("m")
    alias = {"A#": "Bb", "D#": "Eb", "G#": "Ab", "Cb": "B"}
    if minor:
        base = alias.get(name, name)
        if base not in RELATIVE_MAJOR:
            raise ValueError(f"unsupported key signature: {raw}")
        return RELATIVE_MAJOR[base]
    name = alias.get(name, name)
    if name not in SIG:
        raise ValueError(f"unsupported key signature: {raw}")
    return name


def transpose_key(key: str, semitones: int) -> str:
    key = canonical_key(key)
    semitones %= 12
    if semitones == 0:
        return key
    pc = (KEY_PC[key] + semitones) % 12
    options = [name for name in ENHARMONIC[pc] if name in SIG]
    if key in SHARP_KEYS:
        for name in options:
            if name in SHARP_KEYS or name == "C":
                return name
    if key in FLAT_KEYS:
        for name in options:
            if name in FLAT_KEYS or name == "C":
                return name
    return options[0]


def spell(pc: int, key: str) -> tuple[str, str, int]:
    """Return (abc accidental, letter, delta from the natural letter)."""
    signature = SIG[key]
    for letter in "CDEFGAB":
        delta = signature.get(letter, 0)
        if (NATURAL[letter] + delta) % 12 == pc:
            return "", letter, delta
    sharpish = key in SHARP_KEYS
    candidates: list[tuple] = []
    for letter in "CDEFGAB":
        natural = NATURAL[letter]
        for delta in (pc - natural, pc - natural - 12, pc - natural + 12):
            if delta not in (-2, -1, 0, 1, 2):
                continue
            if delta == signature.get(letter, 0):
                continue
            accidental = {0: "=", 1: "^", 2: "^^", -1: "_", -2: "__"}[delta]
            prefer = delta == 0 or (sharpish and delta > 0) or ((not sharpish) and delta < 0)
            candidates.append((abs(delta), 0 if prefer else 1, letter, accidental, delta))
    if not candidates:
        raise ValueError(f"cannot spell pitch class {pc} in {key}")
    candidates.sort()
    _, _, letter, accidental, delta = candidates[0]
    return accidental, letter, delta


def format_letter(letter: str, octave: int, accidental: str) -> str:
    if octave >= 5:
        body = letter.lower() + "'" * (octave - 5)
    elif octave <= 3:
        body = letter.upper() + "," * (4 - octave)
    else:
        body = letter.upper()
    return accidental + body


def note_to_abc(midi: int, key: str) -> str:
    accidental, letter, delta = spell(midi % 12, key)
    for octave in range(0, 9):
        base = 12 * (octave + 1) + NATURAL[letter]
        if base + delta == midi:
            return format_letter(letter, octave, accidental)
    raise ValueError(f"cannot format MIDI {midi} in {key}")


def abc_pitch(letter: str, octaves: str, accidental: str | None, key: str) -> int:
    octave = (5 if letter.islower() else 4) + octaves.count("'") - octaves.count(",")
    if accidental is None:
        delta = SIG[key].get(letter.upper(), 0)
    else:
        delta = {"=": 0, "^": 1, "^^": 2, "_": -1, "__": -2}[accidental]
    return 12 * (octave + 1) + NATURAL[letter.upper()] + delta


@dataclass(frozen=True)
class Instrument:
    id: str
    name: str
    clef: str
    written_offset: int  # written MIDI = sounding MIDI + offset
    low: int
    high: int
    prefer: str  # melody, inner, bass, keyboard
    wave: str

    @property
    def keyboard(self) -> bool:
        return self.prefer == "keyboard"


def _inst(
    id_: str,
    name: str,
    clef: str,
    offset: int,
    low: int,
    high: int,
    prefer: str,
    wave: str,
) -> Instrument:
    return Instrument(id_, name, clef, offset, low, high, prefer, wave)


# Comfortable ranges, not the absolute extremes. Sounding pitch.
INSTRUMENTS: dict[str, Instrument] = {
    inst.id: inst
    for inst in [
        _inst("violin", "Violin", "treble", 0, 55, 93, "melody", "sawtooth"),
        _inst("viola", "Viola", "alto", 0, 48, 81, "inner", "triangle"),
        _inst("cello", "Cello", "bass", 0, 36, 72, "bass", "triangle"),
        _inst("bass", "Double bass", "bass", 12, 28, 55, "bass", "sine"),
        _inst("flute", "Flute", "treble", 0, 60, 96, "melody", "sine"),
        _inst("oboe", "Oboe", "treble", 0, 58, 88, "melody", "sawtooth"),
        _inst("clarinet", "Clarinet in Bb", "treble", 2, 50, 86, "melody", "square"),
        _inst("alto-sax", "Alto sax in Eb", "treble", 9, 49, 80, "melody", "square"),
        _inst("tenor-sax", "Tenor sax in Bb", "treble", 14, 44, 72, "melody", "square"),
        _inst("trumpet", "Trumpet in Bb", "treble", 2, 58, 82, "melody", "square"),
        _inst("horn", "Horn in F", "treble", 7, 41, 77, "inner", "sawtooth"),
        _inst("trombone", "Trombone", "bass", 0, 40, 72, "bass", "triangle"),
        _inst("guitar", "Guitar", "treble", 12, 40, 76, "inner", "triangle"),
        _inst("piano", "Piano", "grand", 0, 21, 108, "keyboard", "sine"),
        _inst("voice", "Voice", "treble", 0, 53, 81, "melody", "sine"),
    ]
}

ALIASES = {
    "violin": "violin",
    "violins": "violin",
    "vln": "violin",
    "viola": "viola",
    "vla": "viola",
    "cello": "cello",
    "violoncello": "cello",
    "vc": "cello",
    "bass": "bass",
    "double bass": "bass",
    "doublebass": "bass",
    "contrabass": "bass",
    "upright": "bass",
    "flute": "flute",
    "fl": "flute",
    "oboe": "oboe",
    "clarinet": "clarinet",
    "clarinet in bb": "clarinet",
    "cl": "clarinet",
    "sax": "alto-sax",
    "alto sax": "alto-sax",
    "alto saxophone": "alto-sax",
    "saxophone": "alto-sax",
    "tenor sax": "tenor-sax",
    "tenor saxophone": "tenor-sax",
    "trumpet": "trumpet",
    "trumpet in bb": "trumpet",
    "bb trumpet": "trumpet",
    "tpt": "trumpet",
    "horn": "horn",
    "horn in f": "horn",
    "french horn": "horn",
    "hn": "horn",
    "trombone": "trombone",
    "tbn": "trombone",
    "guitar": "guitar",
    "gtr": "guitar",
    "piano": "piano",
    "pno": "piano",
    "keys": "piano",
    "keyboard": "piano",
    "voice": "voice",
    "singer": "voice",
    "vocal": "voice",
    "vox": "voice",
}


def instrument_help() -> str:
    rows = []
    for inst in INSTRUMENTS.values():
        rows.append(
            f"  {inst.id:12} {inst.name:18} {pitch_label(inst.low):>4}–{pitch_label(inst.high):<4}"
            f"  written {inst.written_offset:+d} semitones"
        )
    return "\n".join(rows)


def parse_ensemble(text: str) -> list[str]:
    """'2 violin + trumpet + piano' or 'violin x 2 + viola' -> instrument ids."""
    raw = text.lower().replace("+", ",").replace("&", ",").replace("×", "x")
    raw = re.sub(r"(\d+)\s*x\s*", r"\1 ", raw)
    found: list[str] = []
    for chunk in re.split(r",|;|/|\band\b", raw):
        chunk = chunk.strip()
        if not chunk:
            continue
        count = 1
        lead = re.match(r"^(\d+)\s+(.+)$", chunk)
        trail = re.match(r"^(.+?)\s+x\s*(\d+)$", chunk)
        if lead:
            count = int(lead.group(1))
            chunk = lead.group(2).strip()
        elif trail:
            count = int(trail.group(2))
            chunk = trail.group(1).strip()
        key = ALIASES.get(chunk)
        if key is None:
            known = ", ".join(INSTRUMENTS)
            raise ValueError(f"unknown instrument {chunk!r}. Known ids: {known}")
        found.extend([key] * count)
    if not found:
        raise ValueError("name at least one instrument")
    return found
