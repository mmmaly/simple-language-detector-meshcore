# simple-language-detector-meshcore

A very small, very fast language detector for routing Meshcore public-channel
messages to per-language channels. It recognises **Polish**, **Slovak** and
**German** and otherwise answers **English / public**. It is deliberately
conservative: a message is only routed away from the public channel when the
whole message very clearly leans one way.

* header-only C++ (`lang_detect.h`, ~100 lines), no heap, no floats, no STL
* model tables (`lang_model.h`) are 16 KB of `const` data (2000 trigrams);
  a Cortex-M4 build is 16.7 KB in total, i.e. ~500 bytes of code
* measured 0.85 µs per 60-character message on an Apple M1; estimated
  ~50 µs on an ESP32-S3 (Heltec V3, T-Deck) and ~0.2 ms on an nRF52840
  (RAK4631, T-Echo), see [Timing](#timing)
* handles UTF-8, upper/lower case, and text typed **without diacritics**
  (`Dakujem za odpoved` is recognised as Slovak, `Hoert mich jemand` as German)

## Usage

```cpp
#include "lang_detect.h"

LangChannel ch = ld_detect(text, len);   // LANG_EN, LANG_PL, LANG_SK or LANG_DE
switch (ch) {
    case LANG_PL: /* forward to Polish channel */ break;
    case LANG_SK: /* forward to Slovak channel */ break;
    case LANG_DE: /* forward to German channel */ break;
    default:      /* keep on public */ break;
}
```

`ld_detect()` returns `LANG_EN` whenever it is not sure, so the only failure
mode you need to worry about is a Polish/Slovak/German message that stays on
the public channel. For diagnostics pass a `LangScores*` as the third argument
to get the per-language costs and the amount of evidence.

## Examples

Output of `./build/test_detect -v` (channel, number of trigrams, summed costs
for EN/PL/SK/DE; lower cost = more likely). Note that none of the Slovak,
Polish or German lines use any diacritics:

```
SK  n=58  costs=[4324 3958 3529 4336]  | Ahojte, ma niekto z vas repeater niekde na Zobore pri Nitre?
PL  n=59  costs=[6373 3882 5524 6517]  | Czesc wszystkim, czy ktos slyszy moj wezel w okolicy Krakowa?
DE  n=64  costs=[5214 5832 5661 4442]  | Hoert mich jemand aus Muenchen? Ich teste gerade die neue Antenne.
EN  n=60  costs=[4093 6015 5753 5242]  | Hi all, testing my new node from the hill, anyone hearing this?
EN  n=46  costs=[3857 3812 3996 3896]  | Hello everyone, dzien dobry, guten Tag, dobry den!
EN  n=22  costs=[1436 1640 1627 1630]  | Node OK-1234 online, battery 87%
EN  n=2   costs=[204 198 180 258]      | ok
```

The mixed greeting has Polish as the cheapest class, but only by 45 cost
units over 46 trigrams (about 0.1 bit per trigram), far below the required
margin, so it stays public. "ok" has too little evidence and stays public.

## How it works

1. The text is lower-cased and mapped onto a 55-symbol alphabet: space, `a-z`
   and the diacritic letters that separate the three languages
   (`ąćęłńóśźż áäčďéíĺľňôŕšťúýž öüß`). Other accented Latin letters fold to
   their base letter; digits, punctuation, emoji and non-Latin scripts become
   spaces. The text is padded with a space on both ends.
2. Every character trigram is looked up (binary search) in a sorted table of
   the 2000 most frequent trigrams. Each entry stores, per language,
   `cost = -log2 P(trigram | language) * 8` as one byte. Costs are summed.
   Trigrams not in the table carry no information.
3. The language with the lowest cost wins **only if** all of these hold
   (`ld_decide()`):
   * at least `LD_MIN_TRIGRAMS` (12) trigrams were seen, i.e. ~10 letters;
   * it beats English by `LD_THRESH_VS_EN` (96 = 12 bits, so the message is
     2^12 times more likely under the winner) **and** by
     `LD_MIN_MARGIN_PER_TRIGRAM` (6 = 0.75 bit) on average per trigram, which
     is what makes "the whole message is in that language" hold; a message
     with one Polish word in an English sentence does not pass;
   * it beats every other language by `LD_THRESH_VS_OTHER` (48 = 6 bits),
     which is mostly about Polish vs. Slovak.

   Otherwise the answer is English / public.

## Accuracy

Held-out Tatoeba sentences (short, one sentence each, so this is a pessimistic
setting; real chat messages are usually longer and easier). Rows are the true
language, columns the channel chosen:

| source          | EN     | PL    | SK    | DE    |     n |
|-----------------|-------:|------:|------:|------:|------:|
| English         | 100.0% |  0.0% |  0.0% |  0.0% | 16500 |
| Polish          |   5.2% | 94.7% |  0.0% |  0.0% | 13717 |
| Slovak          |  14.1% |  0.2% | 85.7% |  0.1% |  3319 |
| German          |   2.4% |  0.0% |  0.0% | 97.6% | 16500 |
| Polish, no accents |  7.6% | 92.3% |  0.2% |  0.0% | 13717 |
| Slovak, no accents | 20.2% |  0.3% | 79.5% |  0.1% |  3319 |
| German, ae/oe/ue/ss |  2.3% |  0.0% |  0.0% | 97.7% | 16500 |
| Czech (not trained) | 15.3% |  0.4% | 84.2% |  0.1% |  5000 |

Zero of 16,500 English sentences were routed away from the public channel.
`test/messages.tsv` holds 57 realistic mesh messages (callsigns, RSSI reports,
URLs, emoji, short "ok"/"73"/"test", mixed-language greetings, other European
languages); all are routed as expected.

**Czech goes to the Slovak channel.** The four-class model cannot tell Czech
from Slovak, and for a Slovak community that is probably what you want. If
not, retrain with `--with-czech`: Czech becomes a fifth class that routes to
the public channel. That makes Slovak vs. Czech a real contest, and Slovak
recall drops from ~86% to ~58% on short sentences, so it is off by default.

## Timing

Measured with `make bench` (four ~60-character messages, 80,000 runs):

| device | clock | per message | per byte | source |
|---|---:|---:|---:|---|
| Apple M1 (laptop) | 3.2 GHz | 0.85 µs | 12.5 ns (~40 cycles) | measured |
| ESP32-S3 (Heltec V3, LilyGo T-Deck) | 240 MHz | ~50 µs | ~0.7 µs | estimate |
| nRF52840 (RAK4631, LilyGo T-Echo) | 64 MHz | ~0.2 ms | ~3 µs | estimate |

The embedded numbers are estimates, not measurements: the work per byte is
one UTF-8 decode plus an 11-step binary search over 2000 keys, which on an
in-order MCU core with flash wait states and no branch predictor costs
roughly 4-6x the M1's ~40 cycles per byte. Either way it is negligible next
to a LoRa packet (hundreds of milliseconds on air). The code size on
Cortex-M4 (`clang++ --target=thumbv7em-none-eabi -mcpu=cortex-m4 -Os`) is
16,708 bytes including the tables.

To measure on your own board:

```cpp
uint32_t t0 = micros();
LangChannel ch = ld_detect(text, strlen(text));
Serial.printf("lang=%s in %lu us\n", ld_channel_name(ch), (unsigned long)(micros() - t0));
```

## Tuning

All knobs are `#define`s at the top of `lang_model.h`:

| define                       | default | effect of raising it |
|------------------------------|--------:|----------------------|
| `LD_MIN_TRIGRAMS`            | 12      | ignore shorter messages |
| `LD_THRESH_VS_EN`            | 96      | need more total evidence vs. English |
| `LD_MIN_MARGIN_PER_TRIGRAM`  | 6       | need a purer single-language message |
| `LD_THRESH_VS_OTHER`         | 48      | need clearer PL vs. SK vs. DE separation |

`python3 train.py --sweep` prints the confusion table for a grid of these
values on the held-out set, so you can pick your own trade-off. If you see
English chat being misrouted in practice, raise `LD_MIN_MARGIN_PER_TRIGRAM`
first; it is the strongest guard against mixed or foreign-looking English.

## Files

| file                   | what |
|------------------------|------|
| `lang_detect.h`        | the detector; copy this and `lang_model.h` into the firmware |
| `lang_model.h`         | generated tables + thresholds (do not edit by hand, retrain) |
| `train.py`             | trains the model from Tatoeba sentence dumps and writes `lang_model.h` |
| `detect.py`            | Python reference that reads `lang_model.h`; `echo "..." \| python3 detect.py` |
| `test/test_detect.cpp` | CLI: classify stdin, evaluate a TSV, check `messages.tsv`, benchmark |
| `test/messages.tsv`    | hand-written mesh messages with expected channels |

## Building and testing

```bash
make test          # build and run test/messages.tsv through the C++ detector
make bench         # speed and table size
echo "Ahojte, počuje ma niekto?" | ./build/test_detect -v
```

## Retraining

```bash
make data          # downloads Tatoeba dumps for eng/pol/slk/deu/ces into data/
make train         # writes lang_model.h (takes ~2 minutes)
python3 train.py --dump-test build/test.tsv && make crosscheck   # C++ == Python on 100k lines
```

Useful options: `--table-size N` (500 → 4 KB and ~4 points less recall,
4000 → 32 KB and ~1 point more), `--with-czech`, `--t-en`, `--t-other`,
`--min-trigrams`, `--per-trigram`, `--sweep`. Training data for PL/SK/DE is
automatically augmented with diacritic-stripped copies (and `ae/oe/ue/ss`
for German), which is why accent-less typing works.

Adding a language: append it to `LANGS` and `ROUTE` in `train.py`, add its
distinctive letters to `SPECIAL` (keep the alphabet ≤ 64 symbols), extend
the `LangChannel` enum, drop its Tatoeba dump into `data/` and retrain.

## Limits

* Messages shorter than ~10 letters always stay public; there is not enough
  evidence in "ok", "dík" or "73" and the spec says to err on the public side.
* Very short Slovak/Polish messages without accents are the hardest case
  (~80% recall on one-liners). Longer messages are close to 100%.
* Czech is routed as Slovak unless you retrain with `--with-czech`.
* Training data is Tatoeba (clean, mostly full sentences). Chat slang and
  radio jargon are unseen, but only hurt recall, not the public-channel default.
