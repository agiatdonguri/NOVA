"""ABC subset and a small MusicXML reader. Both become the same Score."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

from nova.theory import FIFTHS_TO_KEY, abc_pitch, canonical_key, note_to_abc

NOTE_RE = re.compile(
    r"(?P<acc>\^\^|\^|__|=|_)?"
    r"(?P<let>[A-Ga-gzZxX])"
    r"(?P<oct>[,']*)"
    r"(?P<dur>\d+/\d+|/\d+|\d+)?"
)


@dataclass(frozen=True)
class Event:
    pitches: tuple[int, ...]  # empty tuple is a rest
    dur: Fraction  # whole-note lengths


@dataclass
class Voice:
    vid: str
    name: str
    clef: str
    measures: list[list[Event]] = field(default_factory=list)


@dataclass
class Score:
    title: str
    meter: tuple[int, int]
    unit: Fraction
    key: str
    tempo: int
    voices: list[Voice]
    warnings: list[str] = field(default_factory=list)


def load(path: str | Path) -> Score:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".mxl":
        return parse_musicxml(_read_mxl(path))
    if suffix in {".xml", ".musicxml"}:
        return parse_musicxml(path.read_text(encoding="utf-8"))
    return parse_abc(path.read_text(encoding="utf-8"))


def _read_mxl(path: Path) -> str:
    with zipfile.ZipFile(path) as bundle:
        name = "score.xml"
        if "META-INF/container.xml" in bundle.namelist():
            container = bundle.read("META-INF/container.xml")
            match = re.search(br'full-path="([^"]+)"', container)
            if match:
                name = match.group(1).decode()
        return bundle.read(name).decode("utf-8")


def parse_abc(text: str) -> Score:
    title = "Untitled"
    meter = (4, 4)
    unit = Fraction(1, 8)  # ABC default when L: is omitted
    key = "C"
    tempo = 120
    saw_unit = False
    voices: dict[str, Voice] = {}
    order: list[str] = []
    warnings: list[str] = []
    current: str | None = None
    in_music = False
    layer_bars: dict[str, list[list[list[Event]]]] = {}

    def touch(vid: str, name: str | None = None, clef: str | None = None) -> Voice:
        if vid not in voices:
            voices[vid] = Voice(vid, name or vid, clef or "treble")
            order.append(vid)
        else:
            if name:
                voices[vid].name = name
            if clef:
                voices[vid].clef = clef
        return voices[vid]

    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if not in_music and re.match(r"^[A-Za-z]:", line) and not line.startswith("V:"):
            field_name, value = line.split(":", 1)
            value = value.strip()
            if field_name == "T" and title == "Untitled":
                title = value
            elif field_name == "M":
                meter = _parse_meter(value)
            elif field_name == "L":
                unit = _parse_fraction(value)
                saw_unit = True
            elif field_name == "Q":
                tempo = _parse_tempo(value, tempo)
            elif field_name == "K":
                key = canonical_key(value)
            continue
        if re.match(r"^V:", line) and not _looks_like_music(line):
            vid, name, clef = _parse_voice_header(line[2:])
            touch(vid, name, clef)
            current = vid
            continue
        in_music = True
        if not saw_unit:
            # Files we write always set L:. A bare tune still follows the ABC default.
            pass
        for vid, music in _voice_segments(line):
            if vid:
                touch(vid)
                current = vid
            if current is None:
                current = "V1"
                touch(current)
            for bar_text in _split_bars(music):
                layers = [_parse_bar(piece, unit, key) for piece in _overlay_pieces(bar_text)]
                layers = [layer for layer in layers if layer]
                if not layers:
                    continue
                layer_bars.setdefault(current, []).append(layers)
                want = Fraction(meter[0], meter[1])
                for index, events in enumerate(layers):
                    got = sum((event.dur for event in events), Fraction(0))
                    if got != want:
                        label = voices[current].name if index == 0 else f"{voices[current].name} layer {index + 1}"
                        warnings.append(
                            f"{label} bar {len(layer_bars[current])} lasts {got} instead of {want}"
                        )

    if not order:
        raise ValueError("no music in that file")
    return Score(title, meter, unit, key, tempo, _expand_layers(voices, order, layer_bars, meter), warnings)


def parse_musicxml(text: str) -> Score:
    root = _xml_root(text)
    if root.tag != "score-partwise":
        raise ValueError(
            "Need partwise MusicXML. In MuseScore use File → Export → MusicXML, "
            "or pass a .mxl and this reader will unzip it."
        )
    title = (
        root.findtext("./work/work-title")
        or root.findtext("./movement-title")
        or "Imported score"
    )
    names = {
        part.get("id") or "": part.findtext("part-name") or part.get("id") or "Part"
        for part in root.findall("./part-list/score-part")
    }
    meter = (4, 4)
    key = "C"
    tempo = 120
    saw_meter = False
    saw_key = False
    warnings: list[str] = []
    voices: list[Voice] = []

    for part in root.findall("part"):
        part_name = names.get(part.get("id") or "", "Part")
        divisions = 1
        beats, beat_type = meter
        cursor = Fraction(0)
        clefs = {"1": "treble"}
        chord_anchor: dict[str, Fraction] = {}
        pending: dict[str, list[dict]] = {}
        bounds: list[tuple[Fraction, Fraction]] = []
        for measure in part.findall("measure"):
            origin = cursor
            for child in list(measure):
                if child.tag == "attributes":
                    if child.findtext("divisions"):
                        divisions = int(child.findtext("divisions") or 1) or 1
                    if child.find("time") is not None:
                        beats = int(child.findtext("./time/beats") or beats)
                        beat_type = int(child.findtext("./time/beat-type") or beat_type)
                        if not saw_meter:
                            meter = (beats, beat_type)
                            saw_meter = True
                    if child.find("key") is not None and not saw_key:
                        fifths = int(child.findtext("./key/fifths") or 0)
                        key = FIFTHS_TO_KEY.get(fifths, "C")
                        saw_key = True
                    for clef in child.findall("clef"):
                        staff = clef.get("number") or "1"
                        clefs[staff] = _map_clef(
                            clef.findtext("sign") or "G", clef.findtext("line") or "2"
                        )
                elif child.tag == "sound" and child.get("tempo"):
                    tempo = int(float(child.get("tempo") or tempo))
                elif child.tag == "direction":
                    sound = child.find("sound")
                    if sound is not None and sound.get("tempo"):
                        tempo = int(float(sound.get("tempo") or tempo))
                elif child.tag == "backup":
                    cursor -= _divs_to_whole(child.findtext("duration") or "0", divisions)
                elif child.tag == "forward":
                    cursor += _divs_to_whole(child.findtext("duration") or "0", divisions)
                elif child.tag == "note":
                    if child.find("grace") is not None:
                        continue
                    dur = _divs_to_whole(child.findtext("duration") or "0", divisions)
                    staff = child.findtext("staff") or "1"
                    voice_id = child.findtext("voice") or "1"
                    key_id = f"s{staff}v{voice_id}"
                    is_chord = child.find("chord") is not None
                    start = chord_anchor.get(key_id, cursor) if is_chord else cursor
                    if not is_chord:
                        chord_anchor[key_id] = cursor
                        cursor += dur
                    midi = _note_midi(child)
                    stop = _tie(child, "stop")
                    start_tie = _tie(child, "start")
                    bucket = pending.setdefault(key_id, [])
                    if stop and midi is not None and _extend_tie(bucket, midi, start, dur, start_tie):
                        continue
                    bucket.append(
                        {
                            "start": start,
                            "dur": dur,
                            "midi": midi,
                            "staff": staff,
                            "tie_start": start_tie,
                        }
                    )
            measure_len = Fraction(beats, beat_type)
            bounds.append((origin, origin + measure_len))
            cursor = origin + measure_len
        voices.extend(_materialize(part_name, pending, bounds, clefs, warnings))

    if not voices:
        raise ValueError("MusicXML file has no notes")
    return Score(title, meter, Fraction(1, 4), key, tempo, voices, warnings)


def _materialize(part_name, pending, bounds, clefs, warnings) -> list[Voice]:
    voices: list[Voice] = []
    for index, key_id in enumerate(sorted(pending), start=1):
        notes = pending[key_id]
        staff = notes[0]["staff"] if notes else "1"
        clef = clefs.get(staff, "treble")
        measures: list[list[Event]] = []
        for start, end in bounds:
            inside = [note for note in notes if start <= note["start"] < end]
            inside.sort(key=lambda note: (note["start"], note["midi"] if note["midi"] is not None else -1))
            events: list[Event] = []
            cursor = start
            i = 0
            while i < len(inside):
                note = inside[i]
                if note["start"] > cursor:
                    events.append(Event((), note["start"] - cursor))
                    cursor = note["start"]
                pitches = []
                dur = note["dur"]
                j = i
                while j < len(inside) and inside[j]["start"] == note["start"]:
                    if inside[j]["midi"] is not None:
                        pitches.append(inside[j]["midi"])
                    dur = max(dur, inside[j]["dur"])
                    j += 1
                events.append(Event(tuple(sorted(set(pitches))), dur))
                cursor = note["start"] + dur
                i = j
            if cursor < end:
                events.append(Event((), end - cursor))
            measures.append(events or [Event((), end - start)])
        name = part_name if len(pending) == 1 else f"{part_name} {index}"
        voices.append(Voice(f"p{index}", name, clef, measures))
        if bounds and any(note["start"] < bounds[0][0] or note["start"] >= bounds[-1][1] for note in notes):
            warnings.append(f"{name} has a note outside the measures")
    if len(voices) == 2 and {voice.clef for voice in voices} == {"treble", "bass"}:
        for voice in voices:
            hand = "RH" if voice.clef == "treble" else "LH"
            voice.name = f"{part_name} {hand}"
    return voices


def emit_tune(
    title: str,
    meter: tuple[int, int],
    key: str,
    tempo: int,
    voices: list[tuple[str, str, str, list[list[Event]]]],
    unit: Fraction = Fraction(1, 4),
    score_directive: str | None = None,
) -> str:
    lines = [
        "X:1",
        f"T:{title}",
        f"M:{meter[0]}/{meter[1]}",
        f"L:{unit.numerator}/{unit.denominator}",
        f"Q:1/4={tempo}",
        f"K:{key}",
    ]
    if score_directive:
        lines.append(score_directive)
    for vid, name, clef, _measures in voices:
        safe = name.replace('"', "'")
        lines.append(f'V:{vid} name="{safe}" clef={clef}')
    for vid, _name, _clef, measures in voices:
        body = _emit_measures(measures, key, unit)
        lines.append(f"[V:{vid}] {body}")
    return "\n".join(lines) + "\n"


def score_to_abc(score: Score) -> str:
    voices = [(voice.vid, voice.name, voice.clef, voice.measures) for voice in score.voices]
    return emit_tune(score.title, score.meter, score.key, score.tempo, voices, score.unit)


def emit_event(event: Event, key: str, unit: Fraction) -> str:
    dur = _format_dur(event.dur, unit)
    if not event.pitches:
        return "z" + dur
    notes = [note_to_abc(pitch, key) for pitch in sorted(event.pitches)]
    if len(notes) == 1:
        return notes[0] + dur
    return "[" + "".join(notes) + "]" + dur


def _emit_measures(measures: list[list[Event]], key: str, unit: Fraction) -> str:
    bars = []
    for bar in measures:
        if not bar:
            bars.append("z")
        else:
            bars.append(" ".join(emit_event(event, key, unit) for event in bar))
    return " | ".join(bars) + " |"


def _format_dur(dur: Fraction, unit: Fraction) -> str:
    ratio = Fraction(dur) / Fraction(unit)
    if ratio == 1:
        return ""
    if ratio.denominator == 1:
        return str(ratio.numerator)
    if ratio.numerator == 1:
        return f"/{ratio.denominator}"
    return f"{ratio.numerator}/{ratio.denominator}"


def _parse_meter(value: str) -> tuple[int, int]:
    value = value.strip()
    if value in {"C", "C|"}:
        return (4, 4) if value == "C" else (2, 2)
    if "/" not in value:
        raise ValueError(f"bad meter: {value}")
    num, den = value.split("/", 1)
    return int(num), int(den)


def _parse_fraction(value: str) -> Fraction:
    num, den = value.split("/", 1)
    return Fraction(int(num), int(den))


def _parse_tempo(value: str, default: int) -> int:
    match = re.search(r"(\d+)\s*$", value)
    return int(match.group(1)) if match else default


def _parse_voice_header(rest: str) -> tuple[str, str | None, str | None]:
    match = re.match(r"(\S+)\s*(.*)", rest.strip())
    if not match:
        return "V1", None, None
    vid, extra = match.group(1), match.group(2)
    name_match = re.search(r'name="([^"]*)"', extra) or re.search(r"name=(\S+)", extra)
    clef_match = re.search(r"clef=(\w+)", extra)
    name = name_match.group(1) if name_match else None
    clef = clef_match.group(1) if clef_match else None
    return vid, name, clef


def _looks_like_music(line: str) -> bool:
    if line.startswith("[V:"):
        return True
    if re.match(r"^[A-Za-z]:", line):
        return False
    return bool(re.search(r"[A-Ga-gzZ|]", line))


def _voice_segments(line: str):
    parts = re.split(r"\[V:([^\]]+)\]", line)
    if len(parts) == 1:
        yield None, line
        return
    if parts[0].strip():
        yield None, parts[0]
    iterator = iter(parts[1:])
    for vid, music in zip(iterator, iterator):
        yield vid.split()[0], music


def _split_bars(music: str) -> list[str]:
    music = music.strip()
    if not music:
        return []
    return [chunk for chunk in re.split(r"\|+", music) if chunk.strip()]


def _parse_bar(text: str, unit: Fraction, key: str) -> list[Event]:
    source = text.replace(":", "")
    source = re.sub(r"^\d+\s*", "", source.strip())
    events: list[Event] = []
    i = 0
    n = len(source)
    tuplet_left = 0
    tuplet_factor = Fraction(1)
    tie = False
    while i < n:
        char = source[i]
        if char.isspace() or char in "\\<>":
            i += 1
            continue
        if char == "-":
            tie = True
            i += 1
            continue
        if char == '"':
            j = source.find('"', i + 1)
            i = n if j < 0 else j + 1
            continue
        if char == "!":
            j = source.find("!", i + 1)
            i = n if j < 0 else j + 1
            continue
        if char == "(":
            match = re.match(r"\((\d+)", source[i:])
            if match:
                count = int(match.group(1))
                tuplet_left = count
                tuplet_factor = Fraction(2, count) if count else Fraction(1)
                i += match.end()
                continue
            i += 1
            continue
        if char == "[":
            j = source.find("]", i + 1)
            if j < 0:
                break
            inner = source[i + 1 : j]
            i = j + 1
            if re.match(r"^[A-Za-z]:", inner):
                continue
            dur, i = _read_dur(source, i, unit)
            if tuplet_left:
                dur *= tuplet_factor
                tuplet_left -= 1
            tie = _add_event(events, tuple(_pitches_in(inner, key)), dur, tie)
            continue
        match = NOTE_RE.match(source, i)
        if not match:
            i += 1
            continue
        i = match.end()
        dur, i = _apply_dots(source, i, _duration_token(match.group("dur") or "", unit))
        if tuplet_left:
            dur *= tuplet_factor
            tuplet_left -= 1
        letter = match.group("let")
        if letter.lower() in "zx":
            tie = _add_event(events, (), dur, tie)
        else:
            midi = abc_pitch(letter, match.group("oct") or "", match.group("acc"), key)
            tie = _add_event(events, (midi,), dur, tie)
    return events


def _add_event(events: list[Event], pitches: tuple[int, ...], dur: Fraction, tie: bool) -> bool:
    if tie and events and events[-1].pitches == pitches and pitches:
        previous = events[-1]
        events[-1] = Event(previous.pitches, previous.dur + dur)
    else:
        events.append(Event(pitches, dur))
    return False


def _overlay_pieces(bar: str) -> list[str]:
    pieces: list[str] = []
    buf: list[str] = []
    depth = 0
    quoted = False
    for char in bar:
        if char == '"':
            quoted = not quoted
        elif char == "[" and not quoted:
            depth += 1
        elif char == "]" and not quoted and depth:
            depth -= 1
        if char == "&" and not quoted and depth == 0:
            pieces.append("".join(buf))
            buf = []
            continue
        buf.append(char)
    pieces.append("".join(buf))
    return [piece for piece in pieces if piece.strip()] or [bar]


def _expand_layers(voices, order, layer_bars, meter) -> list[Voice]:
    bar_len = Fraction(meter[0], meter[1])
    expanded: list[Voice] = []
    for vid in order:
        base = voices[vid]
        bars = layer_bars.get(vid, [])
        depth = max((len(bar) for bar in bars), default=1)
        for layer in range(depth):
            measures = []
            for bar in bars:
                if layer < len(bar):
                    measures.append(bar[layer])
                else:
                    measures.append([Event((), bar_len)])
            name = base.name if layer == 0 else f"{base.name}, layer {layer + 1}"
            voice_id = base.vid if layer == 0 else f"{base.vid}L{layer + 1}"
            expanded.append(Voice(voice_id, name, base.clef, measures))
    return expanded


def _pitches_in(inner: str, key: str) -> list[int]:
    pitches = []
    for match in NOTE_RE.finditer(inner):
        letter = match.group("let")
        if letter.lower() in "zx":
            continue
        pitches.append(abc_pitch(letter, match.group("oct") or "", match.group("acc"), key))
    return pitches


def _duration_token(token: str, unit: Fraction) -> Fraction:
    if not token:
        return unit
    if token.startswith("/"):
        return unit / int(token[1:])
    if "/" in token:
        num, den = token.split("/")
        return unit * Fraction(int(num), int(den))
    return unit * int(token)


def _read_dur(source: str, i: int, unit: Fraction) -> tuple[Fraction, int]:
    match = re.match(r"(\d+/\d+|/\d+|\d+)?", source[i:])
    token = match.group(1) or ""
    i += match.end()
    return _apply_dots(source, i, _duration_token(token, unit))


def _apply_dots(source: str, i: int, dur: Fraction) -> tuple[Fraction, int]:
    while i < len(source) and source[i] == ".":
        dur *= Fraction(3, 2)
        i += 1
    return dur, i


def _strip_comment(line: str) -> str:
    if line.strip().startswith("%"):
        return ""
    out = []
    quoted = False
    for char in line:
        if char == '"':
            quoted = not quoted
        if char == "%" and not quoted:
            break
        out.append(char)
    return "".join(out).rstrip()


def _xml_root(text: str) -> ET.Element:
    text = text.lstrip("\ufeff")
    if text.startswith("<?xml"):
        text = re.sub(r"<\?xml[^>]*\?>", "", text, count=1)
    root = ET.fromstring(text)
    for element in root.iter():
        if isinstance(element.tag, str) and "}" in element.tag:
            element.tag = element.tag.split("}", 1)[1]
    return root


def _divs_to_whole(duration: str, divisions: int) -> Fraction:
    return Fraction(int(float(duration)), divisions) / 4


def _map_clef(sign: str, line: str) -> str:
    if sign == "F":
        return "bass"
    if sign == "C":
        return "alto" if line == "3" else "tenor"
    return "treble"


def _tie(note: ET.Element, kind: str) -> bool:
    for element in note.iter():
        if element.tag in {"tie", "tied"} and element.get("type") == kind:
            return True
    return False


def _note_midi(note: ET.Element) -> int | None:
    if note.find("rest") is not None or note.find("unpitched") is not None:
        return None
    pitch = note.find("pitch")
    if pitch is None:
        return None
    step = pitch.findtext("step") or "C"
    octave = int(pitch.findtext("octave") or 4)
    alter = int(float(pitch.findtext("alter") or 0))
    pcs = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    return 12 * (octave + 1) + pcs[step] + alter


def _extend_tie(bucket: list[dict], midi: int, start: Fraction, dur: Fraction, start_tie: bool) -> bool:
    for prev in reversed(bucket):
        if prev["midi"] == midi and prev["start"] + prev["dur"] == start:
            prev["dur"] += dur
            prev["tie_start"] = prev["tie_start"] or start_tie
            return True
    return False
