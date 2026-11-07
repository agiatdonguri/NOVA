"""Offline checks. These do not call OpenRouter."""

from __future__ import annotations

import tempfile
from fractions import Fraction
from pathlib import Path

from nova.arrange import arrange, arrange_model_score
from nova.llm import MissingKeyError, build_prompt, complete, extract_abc
from nova.preview import write_output
from nova.score import load, parse_abc, parse_musicxml, score_to_abc
from nova.theory import SIG, note_to_abc, parse_ensemble, transpose_key

ROOT = Path(__file__).resolve().parents[1]


def run() -> None:
    _keys()
    _spelling()
    _ode()
    _ensembles()
    _jam()
    _other_ensembles()
    _any_to_any()
    _musicxml()
    _model_parts()
    _prompt_and_key()
    print("ok")


def _keys() -> None:
    assert transpose_key("C", 2) == "D"
    assert transpose_key("E", 2) == "F#"
    assert transpose_key("Bb", 2) == "C"
    assert transpose_key("C", 7) == "G"
    assert transpose_key("C", 9) == "A"
    assert transpose_key("C", 14) == "D"
    assert transpose_key("F", 7) == "C"
    assert transpose_key("C", 12) == "C"
    assert transpose_key("C", -3) == "A"


def _spelling() -> None:
    for key in SIG:
        for midi in range(36, 85):
            token = note_to_abc(midi, key)
            score = parse_abc(f"L:1/4\nK:{key}\nV:V name=V clef=treble\n[V:V] {token} |")
            got = score.voices[0].measures[0][0].pitches[0]
            if got != midi:
                raise AssertionError(f"{key} {midi} spelled {token} and parsed as {got}")
    sharp = parse_abc("L:1/4\nK:G\nV:V\n[V:V] F =F |")
    assert sharp.voices[0].measures[0][0].pitches == (66,)
    assert sharp.voices[0].measures[0][1].pitches == (65,)
    flat = parse_abc("L:1/4\nK:F\nV:V\n[V:V] B =B |")
    assert flat.voices[0].measures[0][0].pitches == (70,)
    assert flat.voices[0].measures[0][1].pitches == (71,)
    chord = parse_abc("L:1/4\nK:C\nV:V\n[V:V] [CEG]2 z2 |")
    assert chord.voices[0].measures[0][0].pitches == (60, 64, 67)
    assert chord.voices[0].measures[0][0].dur == Fraction(1, 2)
    assert chord.voices[0].measures[0][1].pitches == ()


def _ode() -> None:
    score = load(ROOT / "examples" / "ode_to_joy.abc")
    assert score.title == "Ode to Joy"
    assert score.key == "C"
    assert score.tempo == 112
    assert score.meter == (4, 4)
    assert len(score.voices) == 4
    assert [voice.name for voice in score.voices] == ["Soprano", "Alto", "Tenor", "Bass"]
    assert score.warnings == [], score.warnings
    soprano = score.voices[0].measures[0]
    assert [event.pitches[0] for event in soprano] == [64, 64, 65, 67]
    tenor = score.voices[2].measures[0]
    assert [event.pitches[0] for event in tenor] == [55, 55, 57, 60]
    bass = score.voices[3].measures[0]
    assert [event.pitches[0] for event in bass] == [48, 48, 41, 48]
    assert len(score.voices[0].measures) == 8
    again = parse_abc(score_to_abc(score))
    for left, right in zip(score.voices, again.voices):
        for bar_left, bar_right in zip(left.measures, right.measures):
            assert [(event.pitches, event.dur) for event in bar_left] == [
                (event.pitches, event.dur) for event in bar_right
            ]


def _ensembles() -> None:
    assert parse_ensemble("2 violin + trumpet + piano") == ["violin", "violin", "trumpet", "piano"]
    assert parse_ensemble("2x violin, trumpet, piano") == ["violin", "violin", "trumpet", "piano"]
    assert parse_ensemble("violin x 2 + viola + cello") == ["violin", "violin", "viola", "cello"]
    assert parse_ensemble("flute and cello") == ["flute", "cello"]
    assert parse_ensemble("1 guitar & voice") == ["guitar", "voice"]
    try:
        parse_ensemble("bagpipe")
    except ValueError as exc:
        assert "bagpipe" in str(exc)
    else:
        raise AssertionError("unknown instrument should fail")


def _jam() -> None:
    arrangement = arrange(load(ROOT / "examples" / "ode_to_joy.abc"), "2 violin + trumpet + piano")
    got = [
        (part.title, part.role, part.source, part.semitone_shift, part.written_key)
        for part in arrangement.parts
    ]
    expected = [
        ("Violin I", "melody", "Soprano", 0, "C"),
        ("Violin II", "inner", "Alto", 0, "C"),
        ("Trumpet in Bb", "inner", "Tenor", 12, "D"),
        ("Piano", "keyboard", "reduction", 0, "C"),
    ]
    if got != expected:
        raise AssertionError(got)
    violin = arrangement.parts[0]
    assert violin.staves[0].sounding[0][0].pitches == (64,)
    trumpet = arrangement.parts[2]
    assert trumpet.staves[0].sounding[0][0].pitches == (67,)
    assert trumpet.staves[0].written[0][0].pitches == (69,)
    piano = arrangement.parts[3]
    assert "Chord reduction" in piano.reason
    rh = piano.staves[0].sounding[0][0].pitches
    lh = piano.staves[1].sounding[0][0].pitches
    assert rh and lh and min(rh) > max(lh)
    with tempfile.TemporaryDirectory() as folder:
        path = write_output(arrangement, folder)
        html = path.read_text(encoding="utf-8")
    assert "Trumpet in Bb" in html
    assert "abcjs" in html
    assert '"midi": 64' in html or '"midi":64' in html


def _other_ensembles() -> None:
    score = load(ROOT / "examples" / "ode_to_joy.abc")
    duo = arrange(score, "flute + cello")
    assert [(part.role, part.source, part.semitone_shift) for part in duo.parts] == [
        ("melody", "Soprano", 0),
        ("bass", "Bass", 0),
    ]
    lead = arrange(score, "trumpet + piano")
    assert lead.parts[0].role == "melody"
    assert lead.parts[0].semitone_shift == 0
    assert lead.parts[0].written_key == "D"
    assert lead.parts[0].staves[0].written[0][0].pitches == (66,)
    melody = load(ROOT / "examples" / "ode_melody.abc")
    bass = arrange(melody, "cello")
    assert bass.parts[0].role == "bass"
    assert bass.parts[0].source == "sketched bass"
    assert bass.parts[0].staves[0].sounding[0][0].pitches != (64,)


def _any_to_any() -> None:
    score = load(ROOT / "examples" / "ode_to_joy.abc")
    one = arrange(score, "flute")
    assert len(one.parts) == 1
    assert one.parts[0].source == "Soprano"
    piano = arrange(score, "piano")
    assert len(piano.parts) == 1 and len(piano.parts[0].staves) == 2
    wide = arrange(score, "violin, violin, viola, cello, flute, trumpet, horn, trombone")
    assert len(wide.parts) == 8
    signatures = []
    for part in wide.parts:
        pitches = tuple(
            pitch
            for stave in part.staves
            for bar in stave.sounding
            for event in bar
            for pitch in event.pitches
        )
        signatures.append(pitches)
    assert len(set(signatures)) >= 4
    expanded = arrange(load(ROOT / "examples" / "ode_melody.abc"), "trumpet + violin + cello")
    assert len(expanded.parts) == 3
    assert "sketched bass" in {part.source for part in expanded.parts}
    assert any(part.source == "Melody" for part in expanded.parts)


def _musicxml() -> None:
    fragment = load(ROOT / "examples" / "ode_fragment.musicxml")
    assert fragment.title == "Ode fragment"
    assert fragment.tempo == 112
    assert [event.pitches[0] for event in fragment.voices[0].measures[0]] == [64, 64, 65, 67]
    duo = parse_musicxml(
        """<?xml version="1.0" encoding="UTF-8"?>
        <score-partwise xmlns="http://www.musicxml.org/ns/musicxml">
          <part-list><score-part id="P1"><part-name>Duo</part-name></score-part></part-list>
          <part id="P1">
            <measure number="1">
              <attributes>
                <divisions>1</divisions>
                <key><fifths>0</fifths></key>
                <time><beats>4</beats><beat-type>4</beat-type></time>
              </attributes>
              <note><pitch><step>E</step><octave>4</octave></pitch><duration>1</duration><voice>1</voice><tie type="start"/></note>
              <note><pitch><step>E</step><octave>4</octave></pitch><duration>1</duration><voice>1</voice><tie type="stop"/></note>
              <note><pitch><step>G</step><octave>4</octave></pitch><duration>2</duration><voice>1</voice></note>
              <note><chord/><pitch><step>C</step><alter>1</alter><octave>5</octave></pitch><duration>2</duration><voice>1</voice></note>
              <backup><duration>4</duration></backup>
              <note><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration><voice>2</voice></note>
            </measure>
          </part>
        </score-partwise>
        """
    )
    top = duo.voices[0].measures[0]
    assert top[0].pitches == (64,) and top[0].dur == Fraction(1, 2)
    assert top[1].pitches == (67, 73) and top[1].dur == Fraction(1, 2)
    assert duo.voices[1].measures[0][0].pitches == (48,)
    assert duo.voices[1].measures[0][0].dur == Fraction(1)
    layered = parse_abc(
        "M:2/4\nL:1/8\nK:C\nV:R name=Right clef=treble\n[V:R] E2 G2 & C2 E2 |\n"
    )
    assert [voice.name for voice in layered.voices] == ["Right", "Right, layer 2"]
    assert layered.voices[0].measures[0][0].pitches == (64,)
    assert layered.voices[1].measures[0][0].pitches == (60,)
    tied = parse_abc("M:3/4\nL:1/8\nK:C\nV:R name=Right\n[V:R] C4- C2 |\n")
    assert len(tied.voices[0].measures[0]) == 1
    assert tied.voices[0].measures[0][0].dur == Fraction(3, 4)


def _model_parts() -> None:
    parsed = parse_abc(
        """T:Test
M:4/4
L:1/4
K:C
V:v1 name="Violin I" clef=treble
V:t name="Trumpet in Bb" clef=treble
V:prh name="Piano RH" clef=treble
V:plh name="Piano LH" clef=bass
[V:v1] E E F G |
[V:t] G, G, A, C |
[V:prh] E E F G |
[V:plh] C, C, F,, C, |
"""
    )
    arrangement = arrange_model_score(parsed, "violin + trumpet + piano", "~openai/gpt-sol-latest")
    violin, trumpet, piano = arrangement.parts
    assert violin.semitone_shift == 0
    assert violin.staves[0].sounding[0][0].pitches == (64,)
    assert trumpet.written_key == "D"
    assert trumpet.semitone_shift == 12
    assert trumpet.staves[0].sounding[0][0].pitches == (67,)
    assert trumpet.staves[0].written[0][0].pitches == (69,)
    assert piano.written_key == "C"
    assert len(piano.staves) == 2
    fenced = extract_abc("Here you go\n```abc\nT:X\nK:C\nV:V\n[V:V] C |\n```\nDone")
    assert fenced.startswith("T:X")
    assert "C |" in fenced


def _prompt_and_key() -> None:
    score = load(ROOT / "examples" / "ode_to_joy.abc")
    prompt = build_prompt(score, "2 violin + trumpet + piano")
    lowered = prompt.lower()
    assert "concert pitch" in lowered
    assert "do not transpose" in lowered
    assert "not required to take the melody" in lowered
    assert "those two counts are independent" in lowered
    assert "4 voices" in lowered
    assert "4 instruments" in lowered
    assert "tenor line lifted" in lowered
    assert "both of these are musical" in lowered
    assert 'Voice name "Violin I"' in prompt
    assert 'Voice name "Trumpet in Bb"' in prompt
    assert 'Voice name "Piano RH"' in prompt
    flute = build_prompt(score, "flute + cello").lower()
    assert "tenor line lifted" not in flute
    assert "flute" in flute
    assert "disappear" in flute
    from nova.pdf_encode import document_input, encode_instructions, text_pdf

    instructions = encode_instructions().lower()
    assert "do not arrange" in instructions
    assert "engraved sheet music" in instructions
    assert "any number of parts" in instructions
    assert "do not reduce" in instructions
    assert "concert pitch" in instructions
    sample = text_pdf("Ode to Joy\nSoprano: E E F G | G F E D")
    assert sample.startswith(b"%PDF")
    assert b"Ode to Joy" in sample
    with tempfile.TemporaryDirectory() as folder:
        pdf_path = Path(folder) / "score.pdf"
        pdf_path.write_bytes(sample)
        payload = document_input(pdf_path)
    assert payload[0]["role"] == "user"
    assert payload[0]["content"][0]["type"] == "input_image"
    assert payload[0]["content"][0]["image_url"].startswith("data:image/png;base64,")
    assert "engraved sheet music" in payload[0]["content"][-1]["text"].lower()
    from openrouter.components.inputmessageitem import InputMessageItem

    InputMessageItem.model_validate(payload[0])
    import os
    from nova import llm as llm_module

    saved = os.environ.pop("OPENROUTER_API_KEY", None)
    saved_file = llm_module._ENV_FILE
    llm_module._ENV_FILE = Path("/tmp/nova-missing.env")
    try:
        complete("hello")
    except MissingKeyError as exc:
        assert "OPENROUTER_API_KEY" in str(exc)
    else:
        raise AssertionError("missing key should stop before any network call")
    finally:
        llm_module._ENV_FILE = saved_file
        if saved:
            os.environ["OPENROUTER_API_KEY"] = saved
