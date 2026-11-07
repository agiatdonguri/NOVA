"""How instruments behave in a jam session.

These are short explanations for the arranger prompt. They are not full
scores, and they are not a fixed assignment. A trumpet is often the melody
and sometimes is not, depending on who else showed up.
"""

from __future__ import annotations

from collections import Counter

from nova.theory import INSTRUMENTS, parse_ensemble

# One card per instrument. Written as behavior, not as "always play this part."
BEHAVIOR: dict[str, str] = {
    "violin": (
        "Sings easily and can take the tune, a descant, or a moving inner part. "
        "When two or more violins are in the room, split them: one on the melody, "
        "another on harmony or a countermelody. Unison for the whole piece wastes the second player."
    ),
    "viola": (
        "Lives in the middle. A natural alto or tenor line. It can take the tune when the melody "
        "sits low and the higher instruments are covering something else."
    ),
    "cello": (
        "The bass of a string group, and a singing tenor if the bass is already covered. "
        "Do not leave it doubling a violin an octave down for every bar."
    ),
    "bass": (
        "Plays the foundation, usually roots and a simple line. It is not a melody instrument "
        "unless the tune itself is the bass."
    ),
    "flute": (
        "Bright and agile on a tune that sits in the upper half of its range. "
        "A low inner part makes it disappear. If the melody lies low, give the flute a descant "
        "and let a violin, voice, or clarinet keep the tune."
    ),
    "oboe": (
        "A penetrating singer. Good on a lyrical melody or a sustained inner line that needs to be heard. "
        "It tires in the extreme top, so do not park a long tune there."
    ),
    "clarinet": (
        "Wide range. It can take the tune, a chalumeau countermelody, or an inner part. "
        "Choose from where the melody actually sits, not from a habit that the clarinet always solos."
    ),
    "alto-sax": (
        "Projects like a horn and blends like a string. Often a strong melody voice. "
        "In a group that already has a trumpet or violin on the tune, an alto sax is useful "
        "on a jazz-like inner line or a harmony a third below."
    ),
    "tenor-sax": (
        "Warm in the middle. It can sing the tune an octave down from a flute or violin, "
        "or take a tenor countermelody. The bottom of the horn gets muddy if the bass is already busy."
    ),
    "trumpet": (
        "Loud and brilliant, so it is often the right voice for a bold tune, especially when it is "
        "the only treble instrument (trumpet with cello, or trumpet with piano). That is not a rule. "
        "When a violin, flute, or voice already sings the melody cleanly, the trumpet is often better "
        "on a countermelody or on an inner line lifted into its comfortable octave, instead of "
        "doubling the tune in unison. The bottom notes sound pinched; move those up."
    ),
    "horn": (
        "A middle voice that can also take a broad melody. It blends. Give it the tune when the line "
        "is lyrical and nobody else is covering the soprano. Give it the inner part when a trumpet "
        "or violin already has the top."
    ),
    "trombone": (
        "Bass or tenor. A powerful bass line in a brass group, or a countermelody in the middle. "
        "It is rarely the soprano."
    ),
    "guitar": (
        "Comps. Play the harmony, or a single inner line, under a singer or a melody instrument. "
        "A second full melody on top of a voice fights the singer."
    ),
    "piano": (
        "The harmonic glue. Right hand takes the upper voices, left hand the bass, unless a bass "
        "instrument already owns the bottom. The piano should not also steal the melody from a wind "
        "or string who is playing it, unless the piano is the only one who can reach the tune."
    ),
    "voice": (
        "If someone is singing, they usually keep the tune, and the instruments support. "
        "An instrumental descant is welcome. A second person playing the vocal line in unison for "
        "every bar is not."
    ),
}

# Short rooms. Included only when they match the instruments that are actually present.
# `needs` must all be in the room. If any id in `avoid` is in the room, skip the card.
SITUATIONS: list[dict[str, object]] = [
    {
        "needs": {"trumpet", "violin"},
        "avoid": set(),
        "text": (
            "Room with a trumpet and at least one violin: both of these are musical. "
            "The trumpet takes the bold tune and the violin plays harmony. "
            "Or Violin I keeps the tune, another violin takes the alto, and the trumpet takes "
            "the tenor line lifted into its comfortable range, so it colors the middle instead of "
            "covering the violin. Choose from the character of this tune. Do not do both by "
            "doubling the violin in unison for the whole piece."
        ),
    },
    {
        "needs": {"trumpet"},
        "avoid": {"violin", "flute", "oboe", "clarinet", "voice", "alto-sax"},
        "text": (
            "Room where the trumpet is the only treble instrument: it should take the melody. "
            "A cello, trombone, guitar, or piano left hand takes the bass. There is no one else to sing the tune."
        ),
    },
    {
        "needs": {"flute", "violin"},
        "avoid": set(),
        "text": (
            "Room with flute and violin: give the tune to whichever one sits in the melody's comfortable octave. "
            "The other plays an inner line or a descant. A flute forced onto a low alto part disappears; "
            "a violin forced to the top of the flute's range sounds strained."
        ),
    },
    {
        "needs": {"voice"},
        "avoid": set(),
        "text": (
            "Room with a singer: the voice keeps the melody. Guitar, piano, and strings support. "
            "A trumpet or flute may answer with a short descant, not a second copy of the vocal line."
        ),
    },
    {
        "needs": {"horn", "trombone"},
        "avoid": set(),
        "text": (
            "Room with horn and trombone: the horn takes a singing middle line or the tune if it is "
            "the top voice. The trombone takes the bass. Do not write either of them in violin range."
        ),
    },
    {
        "needs": {"violin"},
        "avoid": {"trumpet", "flute", "oboe", "clarinet", "voice", "alto-sax", "tenor-sax"},
        "text": (
            "Room of strings, no winds and no singer: the top violin takes the tune, "
            "the next violin or the viola takes the inner part, and the cello or bass takes the bass."
        ),
    },
]


def behavior_text(ensemble: str) -> str:
    """Explanations for this room only. No full arrangement, no fixed part assignment."""
    ids = parse_ensemble(ensemble)
    counts = Counter(ids)
    present = set(ids)
    lines = [
        "Decide who plays the tune from this room and from the tune.",
        "A loud or brilliant instrument is a candidate for the melody.",
        "It is not required to take the melody when someone else already sings it "
        "and the loud instrument is more useful on a countermelody or an inner line.",
        "",
        "How these instruments behave:",
    ]
    for inst_id in _unique(ids):
        inst = INSTRUMENTS[inst_id]
        count = counts[inst_id]
        label = inst.name if count == 1 else f"{count} × {inst.name}"
        lines.append(f"- {label}: {BEHAVIOR[inst_id]}")
    matched = []
    for situation in SITUATIONS:
        needs = situation["needs"]
        avoid = situation["avoid"]
        if needs <= present and present.isdisjoint(avoid):
            matched.append(str(situation["text"]))
    if matched:
        lines.extend(["", "Situations like this room:"])
        lines.extend(f"- {text}" for text in matched)
    return "\n".join(lines)


def _unique(ids: list[str]) -> list[str]:
    seen: list[str] = []
    for inst_id in ids:
        if inst_id not in seen:
            seen.append(inst_id)
    return seen
