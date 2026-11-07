# musicarrange

Arrange a score for whatever instruments showed up. An 8-bar piece becomes sheet music and playback in one command.

The instant arranger is deterministic and needs no API key. `--llm` sends concert-pitch ABC to OpenRouter and still writes the transposed parts locally, so a Bb trumpet player gets a part in the right key.

## Run

```bash
cd musicarrange
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m nova demo
```

Or `./run.sh`, which creates the venv if it is missing.

Open `out/preview.html`. The page starts with the original score, then the arranged concert score, then each player's written part. PDF downloads sit above the staves. **Play concert pitch** sounds the ensemble.

```bash
python -m nova arrange examples/ode_to_joy.abc -e "flute + cello"
python -m nova arrange examples/ode_melody.abc -e "voice, guitar, bass"
python -m nova arrange examples/ode_fragment.musicxml -e "violin + piano"
python -m nova instruments
python -m nova selftest
```

`--open` opens the preview on macOS. List instruments first when the room changes, and edit the comfortable ranges in `nova/theory.py` if a part sits too high or too low. The first melodic instrument in the list wins the tune when several could play it. Piano always takes a reduction.

Bring a piece in as a PDF, a photo, ABC, or MusicXML (`.xml` or `.mxl`).

## Pipeline

```
PDF or photo of the original engraved score  →  ABC, one voice per printed staff
ABC or MusicXML is already that encoding, so this step is skipped
ABC, any number of voices  →  arrangement for the client's instruments, any number
local code  →  sheet music: concert score plus a written part for each player
```

```bash
export OPENROUTER_API_KEY=...
python -m nova serve
```

Then open http://127.0.0.1:8734 . Recruitment is the empty desk. Arrangement uploads a score and returns a PDF of the full score and a PDF of each part.

```bash
python -m nova pipeline examples/ode_to_joy.pdf -e "2 violin + trumpet + piano" --open
python -m nova prompt examples/ode_to_joy.pdf
python -m nova prompt examples/ode_to_joy.abc -e "2 violin + trumpet + piano"
```

`nova prompt` prints the words and does not spend credits. The arrange prompt explains how each instrument behaves and gives short rooms, not full sample scores. A trumpet often takes a bold tune, and it does not have to when a violin or a voice already has it.

`--rules` transcribes a PDF, then assigns parts with the instant arranger instead of the model. ABC and MusicXML still arrange with no key:

```bash
python -m nova arrange examples/ode_to_joy.abc -e "flute + cello"
python -m nova demo
```

The instant arranger is the fallback when the model reply is not usable ABC. It gives the tune to the first instrument that can sing it. The behavior notes are only in the model prompt.
