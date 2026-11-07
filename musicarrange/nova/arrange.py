"""Assign lines to the instruments in the room, then fit them into range."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction

from nova.score import Event, Score, Voice
from nova.theory import (
    KEY_PC,
    INSTRUMENTS,
    Instrument,
    parse_ensemble,
    transpose_key,
)

# Knobs for the instant arranger. Coverage dominates; the rest only breaks ties.
ORDER_WEIGHT = 0.12
SHIFT_PENALTY = 0.05
CENTER_WEIGHT = 0.05
MIN_COVER = 0.62
ROLE_BONUS = {
    "melody": {"melody": 0.30, "inner": 0.05, "bass": -0.25},
    "inner": {"melody": 0.00, "inner": 0.22, "bass": 0.05},
    "bass": {"melody": -0.20, "inner": 0.05, "bass": 0.34},
}
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}
# Scale degree of the bar's first note -> bass root, in semitones above the tonic.
BASS_ROOT = {0: 0, 1: 0, 2: 7, 3: 5, 4: 0, 5: 5, 6: 7, 7: 7, 8: 0, 9: 9, 10: 0, 11: 7}


@dataclass
class Line:
    role: str
    name: str
    measures: list[list[Event]]


@dataclass
class Stave:
    vid: str
    name: str
    clef: str
    sounding: list[list[Event]]
    written: list[list[Event]]


@dataclass
class Part:
    id: str
    title: str
    instrument_id: str
    reason: str
    wave: str
    written_key: str
    role: str
    source: str
    semitone_shift: int
    staves: list[Stave]


@dataclass
class Arrangement:
    title: str
    ensemble_label: str
    tempo: int
    key: str
    meter: tuple[int, int]
    unit: Fraction
    parts: list[Part]
    engine: str
    source_label: str = ""
    note: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Slot:
    part_title: str
    voice_title: str
    instrument: Instrument
    hand: str | None
    clef: str


def arrange(score: Score, ensemble: str) -> Arrangement:
    ids = parse_ensemble(ensemble)
    instruments = [INSTRUMENTS[item] for item in ids]
    return arrange_instruments(score, instruments, ensemble)


def arrange_instruments(score: Score, instruments: list[Instrument], ensemble: str) -> Arrangement:
    lines = lines_from_score(score)
    titles = titles_for(instruments)
    mono = [(index, inst) for index, inst in enumerate(instruments) if not inst.keyboard]
    assigned: dict[int, tuple[Line, int, float, int]] = {}
    if mono and lines:
        assigned = _assign_mono(mono, lines)
    hands = _piano_hands(lines)
    parts: list[Part] = []
    used_ids: set[str] = set()
    vid = 1
    for index, inst in enumerate(instruments):
        if inst.keyboard:
            rh, lh, blurb = hands
            rh_s, n1 = _place(rh, 0, inst.low, inst.high)
            lh_s, n2 = _place(lh, 0, inst.low, inst.high)
            reason = blurb
            if n1 + n2:
                reason += f" {n1 + n2} notes nudged into range."
            part = _part(
                inst,
                titles[index],
                reason,
                "keyboard",
                "reduction",
                0,
                score.key,
                [
                    ("rh", f"{titles[index]} RH", "treble", rh_s),
                    ("lh", f"{titles[index]} LH", "bass", lh_s),
                ],
                used_ids,
                vid,
            )
        else:
            line, shift, cover, nudged = assigned[index]
            sounding, extra = _place(line.measures, shift, inst.low, inst.high)
            nudged += extra
            written_key = transpose_key(score.key, inst.written_offset)
            reason = _reason(inst, line.name, shift, cover, nudged, written_key)
            part = _part(
                inst,
                titles[index],
                reason,
                line.role if shift % 12 == 0 or line.role != "melody" else "double",
                line.name,
                shift,
                written_key,
                [( "v", titles[index], inst.clef, sounding )],
                used_ids,
                vid,
            )
            if line.role == "melody" and shift % 12 != 0 and abs(shift) not in {12, 24, 36}:
                part.role = "double"
        parts.append(part)
    _number_vids(parts)
    return Arrangement(
        title=score.title,
        ensemble_label=ensemble,
        tempo=score.tempo,
        key=score.key,
        meter=score.meter,
        unit=Fraction(1, 4),
        parts=parts,
        engine="Instant arranger",
        source_label=_source_label(score),
        warnings=list(score.warnings),
    )


def arrange_model_score(
    score: Score,
    ensemble: str,
    model: str,
    source_title: str | None = None,
) -> Arrangement:
    """Turn concert-pitch voices from the model into written parts."""
    if not any(event.pitches for voice in score.voices for bar in voice.measures for event in bar):
        raise ValueError("model ABC had no notes")
    instruments = [INSTRUMENTS[item] for item in parse_ensemble(ensemble)]
    slots = slots_for(instruments)
    aligned, how = align_voices(score.voices, slots)
    parts: list[Part] = []
    used_ids: set[str] = set()
    number = 1
    index = 0
    while index < len(slots):
        slot = slots[index]
        if slot.hand == "rh":
            rh_voice = aligned[index]
            lh_voice = aligned[index + 1]
            rh, rh_shift, rh_cover, rh_nudge = _fit_voice(rh_voice.measures, slot.instrument)
            lh, lh_shift, lh_cover, lh_nudge = _fit_voice(lh_voice.measures, slot.instrument)
            reason = (
                f"Model wrote {rh_voice.name} and {lh_voice.name}. "
                f"Right hand { _shift_phrase(rh_shift) }, left hand { _shift_phrase(lh_shift) }."
            )
            if rh_nudge + lh_nudge:
                reason += f" {rh_nudge + lh_nudge} notes nudged into range."
            part = _part(
                slot.instrument,
                slot.part_title,
                reason,
                "keyboard",
                "model",
                0,
                score.key,
                [
                    ("rh", f"{slot.part_title} RH", "treble", rh),
                    ("lh", f"{slot.part_title} LH", "bass", lh),
                ],
                used_ids,
                number,
            )
            index += 2
        else:
            voice = aligned[index]
            sounding, shift, cover, nudged = _fit_voice(voice.measures, slot.instrument)
            written_key = transpose_key(score.key, slot.instrument.written_offset)
            where = describe_shift(shift, f'Model line "{voice.name}"')
            reason = f"{where}. {cover:.0%} of those notes sit in a comfortable range."
            if nudged:
                reason += f" {nudged} notes nudged into range."
            if slot.instrument.written_offset:
                reason += f" Written part is in {written_key} for {slot.instrument.name}."
            part = _part(
                slot.instrument,
                slot.part_title,
                reason,
                "model",
                voice.name,
                shift,
                written_key,
                [("v", slot.part_title, slot.clef, sounding)],
                used_ids,
                number,
            )
            index += 1
        parts.append(part)
    _number_vids(parts)
    note = "Concert pitch came back from the model. Transposed parts were written locally."
    if how == "order":
        note += " Voice names were ambiguous, so parts were taken in score order."
    return Arrangement(
        title=source_title or score.title,
        ensemble_label=ensemble,
        tempo=score.tempo,
        key=score.key,
        meter=score.meter,
        unit=Fraction(1, 4),
        parts=parts,
        engine=f"OpenRouter ({model})",
        source_label=_source_label(score),
        note=note,
        warnings=list(score.warnings),
    )


def _source_label(score: Score) -> str:
    return ", ".join(voice.name for voice in score.voices)


def slots_for(instruments: list[Instrument]) -> list[Slot]:
    slots: list[Slot] = []
    for inst, title in zip(instruments, titles_for(instruments)):
        if inst.keyboard:
            slots.append(Slot(title, f"{title} RH", inst, "rh", "treble"))
            slots.append(Slot(title, f"{title} LH", inst, "lh", "bass"))
        else:
            slots.append(Slot(title, title, inst, None, inst.clef))
    return slots


def titles_for(instruments: list[Instrument]) -> list[str]:
    totals = Counter(inst.id for inst in instruments)
    seen: Counter[str] = Counter()
    titles = []
    for inst in instruments:
        seen[inst.id] += 1
        if totals[inst.id] > 1:
            titles.append(f"{inst.name} {ROMAN.get(seen[inst.id], seen[inst.id])}")
        else:
            titles.append(inst.name)
    return titles


def align_voices(voices: list[Voice], slots: list[Slot]) -> tuple[list[Voice], str]:
    pairs = []
    for slot_index, slot in enumerate(slots):
        for voice_index, voice in enumerate(voices):
            strength = _name_score(voice, slot)
            if strength >= 4:
                pairs.append((strength, slot_index, voice_index))
    pairs.sort(key=lambda item: (-item[0], item[1], item[2]))
    chosen: dict[int, Voice] = {}
    used: set[int] = set()
    for _strength, slot_index, voice_index in pairs:
        if slot_index in chosen or voice_index in used:
            continue
        chosen[slot_index] = voices[voice_index]
        used.add(voice_index)
    if len(chosen) == len(slots):
        return [chosen[index] for index in range(len(slots))], "name"
    if len(voices) == len(slots):
        return list(voices), "order"
    found = ", ".join(voice.name for voice in voices) or "none"
    wanted = ", ".join(slot.voice_title for slot in slots)
    raise ValueError(f"could not match voices ({found}) to parts ({wanted})")


def lines_from_score(score: Score) -> list[Line]:
    if not score.voices:
        raise ValueError("score has no voices")
    if len(score.voices) == 1:
        voice = score.voices[0]
        return [
            Line("melody", voice.name, voice.measures),
            Line("bass", "sketched bass", _sketch_bass(voice, score.key)),
        ]
    ranked = sorted(score.voices, key=lambda voice: _mean(voice.measures), reverse=True)
    lines = []
    for index, voice in enumerate(ranked):
        if index == 0:
            role = "melody"
        elif index == len(ranked) - 1:
            role = "bass"
        else:
            role = "inner"
        lines.append(Line(role, voice.name, voice.measures))
    return lines


def describe_shift(semitones: int, source: str) -> str:
    if semitones == 0:
        return f"{source}, at pitch"
    if semitones % 12 == 0:
        count = abs(semitones) // 12
        way = "up" if semitones > 0 else "down"
        unit = "octave" if count == 1 else "octaves"
        return f"{source}, {way} {count} {unit}"
    return f"{source}, shifted {semitones:+d} semitones"


def _assign_mono(mono: list[tuple[int, Instrument]], lines: list[Line]) -> dict[int, tuple]:
    candidates = []
    n_parts = len(mono)
    for part_index, (_index, inst) in enumerate(mono):
        for line_index, line in enumerate(lines):
            pitches = _pitches(line.measures)
            if not pitches:
                continue
            octave, cover = _choose_octave(pitches, inst.low, inst.high)
            if cover < MIN_COVER:
                continue
            mean = sum(pitches) / len(pitches) + 12 * octave
            center = (inst.low + inst.high) / 2
            order = ORDER_WEIGHT if n_parts == 1 else ORDER_WEIGHT * (1 - part_index / (n_parts - 1))
            bonus = ROLE_BONUS[inst.prefer][line.role]
            center_pen = CENTER_WEIGHT * min(1.5, abs(mean - center) / 24)
            score = cover + bonus + order - center_pen - SHIFT_PENALTY * abs(octave)
            candidates.append((score, part_index, line_index, 12 * octave, cover))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    assigned: dict[int, tuple] = {}
    used_lines: set[int] = set()
    for _score, part_index, line_index, shift, cover in candidates:
        if part_index in assigned or line_index in used_lines:
            continue
        assigned[part_index] = (lines[line_index], shift, cover, 0)
        used_lines.add(line_index)
    melody = lines[0]
    taken: set[tuple[int, int]] = set()
    for part_index, (line, shift, _cover, _nudged) in assigned.items():
        taken.add((id(line), shift))
    for part_index, (_index, inst) in enumerate(mono):
        if part_index in assigned:
            continue
        choice = None
        for line in lines:
            forbidden = {shift for line_id, shift in taken if line_id == id(line)}
            shift, cover = _best_double(inst, line.measures, forbidden)
            fresh = (id(line), shift) not in taken
            rank = (1 if fresh else 0, round(cover, 4), 0 if line is melody else 1, -abs(shift))
            if choice is None or rank > choice[0]:
                choice = (rank, line, shift, cover)
        assert choice is not None
        _rank, line, shift, cover = choice
        assigned[part_index] = (line, shift, cover, 0)
        taken.add((id(line), shift))
    return {index: assigned[part_index] for part_index, (index, _inst) in enumerate(mono)}


def _best_double(
    inst: Instrument,
    measures: list[list[Event]],
    forbidden: set[int] | None = None,
) -> tuple[int, float]:
    base = _pitches(measures)
    blocked = forbidden or set()
    best_free = None
    best_any = None
    for interval in (0, -12, 12, -3, -4, 7, -5, 3, -7):
        if not base:
            return 0, 1.0
        moved = [pitch + interval for pitch in base]
        octave, cover = _choose_octave(moved, inst.low, inst.high)
        total = interval + 12 * octave
        rank = (round(cover, 4), 1 if total % 12 == 0 else 0, -abs(total))
        if best_any is None or rank > best_any[0]:
            best_any = (rank, total, cover)
        if total not in blocked and (best_free is None or rank > best_free[0]):
            best_free = (rank, total, cover)
    chosen = best_free or best_any
    assert chosen is not None
    return chosen[1], chosen[2]


def _piano_hands(lines: list[Line]) -> tuple[list[list[Event]], list[list[Event]], str]:
    if len(lines) >= 2 and _rhythms_match(lines):
        rh: list[list[Event]] = []
        lh: list[list[Event]] = []
        for measure_index in range(len(lines[0].measures)):
            rh_bar: list[Event] = []
            lh_bar: list[Event] = []
            for event_index in range(len(lines[0].measures[measure_index])):
                dur = lines[0].measures[measure_index][event_index].dur
                pitches: list[int] = []
                for line in lines:
                    pitches.extend(line.measures[measure_index][event_index].pitches)
                pitches = sorted(set(pitches))
                if len(pitches) <= 1:
                    rh_bar.append(Event(tuple(pitches), dur))
                    lh_bar.append(Event((), dur))
                    continue
                cut = len(pitches) // 2
                lh_bar.append(Event(tuple(pitches[:cut]), dur))
                rh_bar.append(Event(tuple(pitches[cut:]), dur))
            rh.append(rh_bar)
            lh.append(lh_bar)
        return rh, lh, (
            "Chord reduction: the right hand takes the upper notes and the left hand takes the lower notes."
        )
    return (
        lines[0].measures,
        lines[-1].measures,
        "The right hand plays the top line and the left hand plays the bass.",
    )


def _rhythms_match(lines: list[Line]) -> bool:
    count = len(lines[0].measures)
    if any(len(line.measures) != count for line in lines):
        return False
    for measure_index in range(count):
        pattern = [event.dur for event in lines[0].measures[measure_index]]
        for line in lines[1:]:
            if [event.dur for event in line.measures[measure_index]] != pattern:
                return False
    return True


def _sketch_bass(voice: Voice, key: str) -> list[list[Event]]:
    tonic = KEY_PC[key]
    measures = []
    for bar in voice.measures:
        pitch = next((item for event in bar for item in event.pitches), None)
        dur = sum((event.dur for event in bar), Fraction(0)) or Fraction(1)
        if pitch is None:
            measures.append([Event((), dur)])
            continue
        root = (tonic + BASS_ROOT.get((pitch - tonic) % 12, 0)) % 12
        midi = 48 + root
        if midi > 55:
            midi -= 12
        measures.append([Event((midi,), dur)])
    return measures


def _fit_voice(measures, inst: Instrument):
    octave, cover = _choose_octave(_pitches(measures), inst.low, inst.high)
    sounding, nudged = _place(measures, 12 * octave, inst.low, inst.high)
    return sounding, 12 * octave, cover, nudged


def _choose_octave(pitches: list[int], low: int, high: int) -> tuple[int, float]:
    if not pitches:
        return 0, 1.0
    best = None
    for octave in range(-3, 4):
        moved = [pitch + 12 * octave for pitch in pitches]
        cover = sum(low <= pitch <= high for pitch in moved) / len(moved)
        mean = sum(moved) / len(moved)
        center = (low + high) / 2
        rank = (round(cover, 4), -abs(octave), -abs(mean - center))
        if best is None or rank > best[0]:
            best = (rank, octave, cover)
    assert best is not None
    return best[1], best[2]


def _place(measures, semitones: int, low: int, high: int):
    nudged = 0
    placed = []
    for bar in measures:
        new_bar = []
        for event in bar:
            pitches = []
            for pitch in event.pitches:
                shifted = pitch + semitones
                clamped = _clamp(shifted, low, high)
                if clamped != shifted:
                    nudged += 1
                pitches.append(clamped)
            new_bar.append(Event(tuple(pitches), event.dur))
        placed.append(new_bar)
    return placed, nudged


def _clamp(pitch: int, low: int, high: int) -> int:
    for _ in range(8):
        if pitch < low:
            pitch += 12
        elif pitch > high:
            pitch -= 12
        else:
            return pitch
    return min(max(pitch, low), high)


def _transpose(measures, semitones: int):
    return [
        [Event(tuple(pitch + semitones for pitch in event.pitches), event.dur) for event in bar]
        for bar in measures
    ]


def _part(inst, title, reason, role, source, shift, written_key, staves, used_ids, _number) -> Part:
    built = []
    for _kind, name, clef, sounding in staves:
        written = _transpose(sounding, inst.written_offset)
        built.append(Stave("v", name, clef, sounding, written))
    return Part(
        id=_slug(title, used_ids),
        title=title,
        instrument_id=inst.id,
        reason=reason,
        wave=inst.wave,
        written_key=written_key,
        role=role,
        source=source,
        semitone_shift=shift,
        staves=built,
    )


def _reason(inst: Instrument, source: str, shift: int, cover: float, nudged: int, written_key: str) -> str:
    text = f"{describe_shift(shift, source)}. {cover:.0%} of those notes sit in a comfortable range."
    if nudged:
        text += f" {nudged} notes nudged into range."
    if inst.written_offset:
        text += f" Written part is in {written_key} for {inst.name}."
        if inst.written_offset % 12 == 0:
            octaves = abs(inst.written_offset) // 12
            way = "higher" if inst.written_offset > 0 else "lower"
            word = "octave" if octaves == 1 else "octaves"
            text += f" It is written {octaves} {word} {way} than it sounds."
    return text


def _shift_phrase(semitones: int) -> str:
    if semitones == 0:
        return "stays at pitch"
    return describe_shift(semitones, "line").split(", ", 1)[1]


def _name_score(voice: Voice, slot: Slot) -> int:
    name = _squash(f"{voice.name} {voice.vid}")
    title = _squash(slot.voice_title)
    instrument = _squash(slot.instrument.name)
    ident = _squash(slot.instrument.id.replace("-", " "))
    if name == title:
        return 5
    if title and (title in name or name in title):
        return 4
    if name == instrument:
        return 3
    if ident and ident in name:
        return 2
    return 0


def _squash(name: str) -> str:
    text = name.lower().replace("right hand", "rh").replace("left hand", "lh")
    text = re.sub(r"\biv\b", "4", text)
    text = re.sub(r"\biii\b", "3", text)
    text = re.sub(r"\bii\b", "2", text)
    text = re.sub(r"\bi\b", "1", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _number_vids(parts: list[Part]) -> None:
    number = 1
    for part in parts:
        for stave in part.staves:
            stave.vid = f"v{number}"
            number += 1


def _slug(text: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "part"
    name = base
    number = 2
    while name in used:
        name = f"{base}-{number}"
        number += 1
    used.add(name)
    return name


def _pitches(measures) -> list[int]:
    return [pitch for bar in measures for event in bar for pitch in event.pitches]


def _mean(measures) -> float:
    pitches = _pitches(measures)
    if not pitches:
        return 60.0
    return sum(pitches) / len(pitches)
