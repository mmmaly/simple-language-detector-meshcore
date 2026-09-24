#!/usr/bin/env python3
"""
Trains a tiny character-trigram language model for the embedded detector
in lang_detect.h and writes it out as lang_model.h.

Input: Tatoeba per-language sentence dumps (<lang>_sentences.tsv[.bz2]),
       one per language, in --data-dir. Download from
       https://downloads.tatoeba.org/exports/per_language/<lang>/<lang>_sentences.tsv.bz2

The model:
  * text is lower-cased and mapped onto a small alphabet: space, a-z and the
    diacritic letters that actually distinguish PL / SK / DE. Every other
    accented Latin letter folds to its base letter, everything else is a space.
  * for every language we estimate P(trigram | language) with add-alpha
    smoothing and store cost = -log2(P) * 8, clipped to 255, as a uint8.
  * only the N most frequent trigrams (over all languages) are kept, sorted by
    key, so the C++ side can binary-search them. Trigrams outside the table
    carry no information (identical cost for all languages).

Training texts for PL / SK / DE are augmented with diacritic-stripped copies
(and ae/oe/ue/ss transliterations for DE) because people on a mesh network
very often type without accents.

The script also runs the same scoring in Python on a held-out split and
prints a threshold sweep so the decision constants can be chosen
consciously (the defaults written to the header come from --t-en, --t-other
and --min-trigrams).
"""
import argparse
import bz2
import collections
import math
import os
import random
import sys
import unicodedata

LANGS = ["eng", "pol", "slk", "deu"]          # index 0 must stay English (the fallback)
LANG_NAMES = {"eng": "EN", "pol": "PL", "slk": "SK", "deu": "DE", "ces": "CS"}
# Model class -> output channel. Extra classes (e.g. Czech) can be routed to
# the public channel (0) so that they are *not* mistaken for Slovak.
ROUTE = {"eng": 0, "pol": 1, "slk": 2, "deu": 3, "ces": 0}

# Diacritic letters that get their own symbol. Order defines symbol numbers.
SPECIAL = list("ąćęłńóśźżáäčďéíĺľňôŕšťúýžöüß")
assert len(SPECIAL) == len(set(SPECIAL))
SYM_SPACE = 0
SYM_A = 1
SYM_SPECIAL0 = 27
NSYM = SYM_SPECIAL0 + len(SPECIAL)            # 55 symbols
assert NSYM <= 64
SPECIAL_SYM = {c: SYM_SPECIAL0 + i for i, c in enumerate(SPECIAL)}

COST_SCALE = 8.0   # cost = -log2(p) * COST_SCALE
COST_MAX = 255


def base_letter(ch):
    """Strip all combining marks: 'ř' -> 'r', 'ß' stays (no decomposition)."""
    d = unicodedata.normalize("NFD", ch)
    d = "".join(c for c in d if not unicodedata.combining(c))
    if ch == "ł":
        return "l"
    if ch == "ß":
        return "ss"
    if ch == "đ":
        return "d"
    return d


def char_sym(ch):
    """Map one character (already lower-cased) to a symbol."""
    if "a" <= ch <= "z":
        return [ord(ch) - ord("a") + SYM_A]
    if ch in SPECIAL_SYM:
        return [SPECIAL_SYM[ch]]
    o = ord(ch)
    if 0xC0 <= o <= 0x17F or o in (0x1E9E,):
        b = base_letter(ch)
        out = []
        for c in b:
            if "a" <= c <= "z":
                out.append(ord(c) - ord("a") + SYM_A)
        if out:
            return out
    return [SYM_SPACE]


def build_charmap():
    """Symbol for every code point U+00C0..U+017F (after lower-casing)."""
    table = []
    for o in range(0xC0, 0x180):
        ch = chr(o).lower()
        syms = char_sym(ch) if len(ch) == 1 else [SYM_SPACE]
        if len(syms) != 1:
            # 'ß' -> "ss" is the only multi-symbol case; C++ handles it specially,
            # and ß itself is in SPECIAL so we never get here for it. Others -> space.
            syms = [SYM_SPACE]
        table.append(syms[0])
    return table


def to_syms(text):
    out = [SYM_SPACE]
    for ch in text.lower():
        for s in char_sym(ch):
            if s == SYM_SPACE and out[-1] == SYM_SPACE:
                continue
            out.append(s)
    if out[-1] != SYM_SPACE:
        out.append(SYM_SPACE)
    return out


def trigram_keys(syms):
    return [(syms[i] << 12) | (syms[i + 1] << 6) | syms[i + 2] for i in range(len(syms) - 2)]


def strip_diacritics(text):
    out = []
    for ch in text:
        if ch in ("ß", "ẞ"):
            out.append("ss")
            continue
        d = unicodedata.normalize("NFD", ch)
        d = "".join(c for c in d if not unicodedata.combining(c))
        d = d.replace("ł", "l").replace("Ł", "L").replace("đ", "d").replace("Đ", "D")
        out.append(d)
    return "".join(out)


DE_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                             "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ẞ": "SS"})


def read_sentences(path, limit, seed):
    opener = bz2.open if path.endswith(".bz2") else open
    rows = []
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3 and parts[2].strip():
                rows.append(parts[2].strip())
    rnd = random.Random(seed)
    rnd.shuffle(rows)
    return rows[:limit] if limit else rows


def augment(lang, text):
    """Yield (text, weight) training variants."""
    yield text, 1.0
    if lang == "eng":
        return
    s = strip_diacritics(text)
    if s != text:
        yield s, 0.5
    if lang == "deu":
        t = text.translate(DE_TRANSLIT)
        if t != text:
            yield t, 0.5


def train(train_sets, table_size, alpha):
    counts = {l: collections.Counter() for l in LANGS}
    for l in LANGS:
        c = counts[l]
        for text in train_sets[l]:
            for variant, w in augment(l, text):
                for k in trigram_keys(to_syms(variant)):
                    c[k] += w
    totals = {l: sum(counts[l].values()) for l in LANGS}
    # Rank trigrams by their highest relative frequency in any language.
    score = collections.defaultdict(float)
    for l in LANGS:
        for k, c in counts[l].items():
            score[k] = max(score[k], c / totals[l])
    keep = sorted(score, key=lambda k: -score[k])[:table_size]
    keep.sort()
    vocab = NSYM ** 3
    costs = {}
    floor = {}
    for l in LANGS:
        n = totals[l]
        floor[l] = min(COST_MAX, round(-math.log2(alpha / (n + alpha * vocab)) * COST_SCALE))
    for k in keep:
        row = []
        for l in LANGS:
            p = (counts[l].get(k, 0.0) + alpha) / (totals[l] + alpha * vocab)
            row.append(min(COST_MAX, round(-math.log2(p) * COST_SCALE)))
        costs[k] = row
    return keep, costs, floor, counts, totals


class Model:
    def __init__(self, keys, costs):
        self.costs = costs

    def scores(self, text):
        tot = [0] * len(LANGS)
        n = 0
        for k in trigram_keys(to_syms(text)):
            n += 1
            row = self.costs.get(k)
            if row is None:
                continue
            for i, c in enumerate(row):
                tot[i] += c
        return tot, n


def decide(tot, n, t_en, t_other, min_n, per_tri):
    """Mirror of ld_decide() in lang_detect.h. Returns output channel (0 = EN/public)."""
    if n < min_n:
        return 0
    best = min(range(len(LANGS)), key=lambda i: tot[i])
    if ROUTE[LANGS[best]] == 0:
        return 0
    # margin against every class that routes to public, and against every other class
    margin_pub = min(tot[i] - tot[best] for i in range(len(LANGS)) if ROUTE[LANGS[i]] == 0)
    margin_other = min(tot[i] - tot[best] for i in range(len(LANGS)) if i != best)
    if margin_pub < t_en or margin_other < t_other:
        return 0
    # the *whole* message must lean that way: average margin per trigram
    if margin_pub < n * per_tri:
        return 0
    return ROUTE[LANGS[best]]


def evaluate(model, test_sets, t_en, t_other, min_n, per_tri):
    conf = {}
    for src, texts in test_sets.items():
        row = collections.Counter()
        for text in texts:
            tot, n = model.scores(text)
            row[decide(tot, n, t_en, t_other, min_n, per_tri)] += 1
        conf[src] = row
    return conf


def print_conf(conf, test_sets, header):
    print(header)
    outs = ["EN", "PL", "SK", "DE"]
    print("  %-14s" % "source" + "".join("%7s" % o for o in outs) + "   n")
    for src, row in conf.items():
        n = len(test_sets[src])
        cells = "".join("%6.1f%%" % (100.0 * row[i] / n) for i in range(len(outs)))
        print("  %-14s%s %5d" % (src, cells, n))


def write_header(path, keys, costs, floor, charmap, t_en, t_other, min_n, per_tri, alpha, table_size):
    with open(path, "w", encoding="utf-8") as f:
        w = f.write
        w("// Generated by train.py -- do not edit.\n")
        w("// Character-trigram language model for lang_detect.h\n")
        w("// table_size=%d alpha=%g cost_scale=%g\n" % (table_size, alpha, COST_SCALE))
        w("#pragma once\n#include <stdint.h>\n\n")
        w("#define LD_NLANG %d\n" % len(LANGS))
        w("#define LD_NSYM %d\n" % NSYM)
        w("#define LD_SYM_SPECIAL0 %d\n" % SYM_SPECIAL0)
        w("#define LD_NKEYS %d\n" % len(keys))
        w("// Decision thresholds (units: bits * %g). See README.\n" % COST_SCALE)
        w("#define LD_THRESH_VS_EN %d\n" % t_en)
        w("#define LD_THRESH_VS_OTHER %d\n" % t_other)
        w("#define LD_MIN_TRIGRAMS %d\n" % min_n)
        w("#define LD_MIN_MARGIN_PER_TRIGRAM %d\n\n" % per_tri)
        w("// Model classes: %s\n" % ", ".join("%d=%s" % (i, LANG_NAMES[l]) for i, l in enumerate(LANGS)))
        w("// Output channel for each class (0=EN/public, 1=PL, 2=SK, 3=DE).\n")
        w("static const uint8_t LD_ROUTE[LD_NLANG] = {%s};\n" % ", ".join(str(ROUTE[l]) for l in LANGS))
        w("// Cost for a trigram absent from the table (same for every language; kept for reference).\n")
        w("// floor: %s\n\n" % ", ".join("%s=%d" % (LANG_NAMES[l], floor[l]) for l in LANGS))
        w("// Symbol for code points U+00C0..U+017F (after ASCII/Latin lower-casing in C++).\n")
        w("static const uint8_t LD_CHARMAP[%d] = {\n" % len(charmap))
        for i in range(0, len(charmap), 16):
            w("  " + ", ".join("%2d" % v for v in charmap[i:i + 16]) + ",\n")
        w("};\n\n")
        w("// Trigram keys: (s0 << 12) | (s1 << 6) | s2, sorted ascending.\n")
        w("static const uint32_t LD_KEYS[LD_NKEYS] = {\n")
        for i in range(0, len(keys), 8):
            w("  " + ", ".join("0x%05x" % k for k in keys[i:i + 8]) + ",\n")
        w("};\n\n")
        w("// Per-language cost of each trigram: -log2(P(trigram|lang)) * %g, clipped to 255.\n" % COST_SCALE)
        w("static const uint8_t LD_COSTS[LD_NKEYS][LD_NLANG] = {\n")
        for i in range(0, len(keys), 4):
            w("  " + ", ".join("{%s}" % ",".join("%3d" % c for c in costs[k]) for k in keys[i:i + 4]) + ",\n")
        w("};\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out", default="lang_model.h")
    ap.add_argument("--table-size", type=int, default=2000)
    ap.add_argument("--alpha", type=float, default=0.5)
    ap.add_argument("--limit", type=int, default=150000, help="max training sentences per language")
    ap.add_argument("--test-frac", type=float, default=0.1)
    ap.add_argument("--t-en", type=int, default=96)
    ap.add_argument("--t-other", type=int, default=48)
    ap.add_argument("--min-trigrams", type=int, default=12)
    ap.add_argument("--per-trigram", type=int, default=6,
                    help="required average margin vs. English per trigram (bits*8)")
    ap.add_argument("--sweep", action="store_true", help="print a threshold sweep")
    ap.add_argument("--extra-test", default="ces", help="comma list of distractor languages to evaluate")
    ap.add_argument("--dump-test", default="", help="write held-out test set as TSV (lang<TAB>text)")
    ap.add_argument("--with-czech", action="store_true",
                    help="train Czech as an extra class routed to the public channel")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    if args.with_czech:
        LANGS.append("ces")
        args.extra_test = ",".join(x for x in args.extra_test.split(",") if x and x != "ces")

    def find(lang):
        for ext in (".tsv.bz2", ".tsv"):
            p = os.path.join(args.data_dir, "%s_sentences%s" % (lang, ext))
            if os.path.exists(p):
                return p
        return None

    train_sets, test_sets = {}, {}
    for l in LANGS:
        p = find(l)
        if not p:
            sys.exit("missing %s_sentences.tsv(.bz2) in %s" % (l, args.data_dir))
        rows = read_sentences(p, args.limit + int(args.limit * args.test_frac), args.seed)
        n_test = int(len(rows) * args.test_frac)
        test_sets[l] = rows[:n_test]
        train_sets[l] = rows[n_test:]
        print("%s: %d train, %d test" % (l, len(train_sets[l]), len(test_sets[l])), file=sys.stderr)

    for l in [x for x in args.extra_test.split(",") if x]:
        p = find(l)
        if p:
            test_sets[l] = read_sentences(p, 5000, args.seed)
    # Diacritic-stripped test variants (people often type without accents).
    for l in ["pol", "slk", "deu"]:
        test_sets[l + "-noacc"] = [strip_diacritics(t) for t in test_sets[l]]
    test_sets["deu-translit"] = [t.translate(DE_TRANSLIT) for t in test_sets["deu"]]

    keys, costs, floor, counts, totals = train(train_sets, args.table_size, args.alpha)
    model = Model(keys, costs)
    print("table: %d trigrams, ~%d bytes flash" % (len(keys), len(keys) * (4 + len(LANGS))), file=sys.stderr)

    if args.sweep:
        for min_n in (8, 12, 16):
            for t_en in (64, 96, 128):
                for t_other in (32, 48, 64):
                    for per_tri in (0, 4, 6, 8, 10):
                        conf = evaluate(model, test_sets, t_en, t_other, min_n, per_tri)
                        print_conf(conf, test_sets, "\n== min_trigrams=%d  t_en=%d  t_other=%d  per_tri=%d"
                                   % (min_n, t_en, t_other, per_tri))
    conf = evaluate(model, test_sets, args.t_en, args.t_other, args.min_trigrams, args.per_trigram)
    print_conf(conf, test_sets, "\n== chosen: min_trigrams=%d  t_en=%d  t_other=%d  per_tri=%d"
               % (args.min_trigrams, args.t_en, args.t_other, args.per_trigram))

    write_header(args.out, keys, costs, floor, build_charmap(), args.t_en, args.t_other, args.min_trigrams,
                 args.per_trigram, args.alpha, args.table_size)
    print("wrote %s" % args.out, file=sys.stderr)

    if args.dump_test:
        with open(args.dump_test, "w", encoding="utf-8") as f:
            for src, texts in test_sets.items():
                for t in texts:
                    f.write("%s\t%s\n" % (src, t.replace("\t", " ")))
        print("wrote %s" % args.dump_test, file=sys.stderr)


if __name__ == "__main__":
    main()
