#!/usr/bin/env python3
"""
Reference implementation that loads the generated lang_model.h and classifies
text the same way lang_detect.h does. Handy for trying messages without a
compiler, and for checking the C++ port against Python.

  echo "Cześć, jak się masz?" | python3 detect.py
  python3 detect.py --verbose < messages.txt
  python3 detect.py --tsv test.tsv          # lang<TAB>text lines -> confusion table
"""
import argparse
import collections
import re
import sys

import train  # for to_syms / trigram_keys (same alphabet as the C++ side)

CHANNELS = ["EN", "PL", "SK", "DE"]


def load_header(path):
    src = open(path, encoding="utf-8").read()
    g = lambda name: int(re.search(r"#define %s (\d+)" % name, src).group(1))
    keys = [int(x, 16) for x in re.findall(r"0x[0-9a-f]{5}", src.split("LD_KEYS")[1].split("};")[0])]
    rows = re.findall(r"\{\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*(\d+))?\s*\}",
                      src.split("LD_COSTS")[1].split("};")[0])
    costs = {k: [int(c) for c in r if c != ""] for k, r in zip(keys, rows)}
    route = [int(x) for x in re.search(r"LD_ROUTE\[LD_NLANG\] = \{([^}]*)\}", src).group(1).split(",")]
    return {
        "costs": costs, "route": route, "nlang": g("LD_NLANG"),
        "t_en": g("LD_THRESH_VS_EN"), "t_other": g("LD_THRESH_VS_OTHER"), "min_n": g("LD_MIN_TRIGRAMS"),
        "per_tri": g("LD_MIN_MARGIN_PER_TRIGRAM"),
    }


def scores(model, text):
    tot = [0] * model["nlang"]
    n = 0
    for k in train.trigram_keys(train.to_syms(text)):
        n += 1
        row = model["costs"].get(k)
        if row:
            for i, c in enumerate(row):
                tot[i] += c
    return tot, n


def detect(model, text):
    tot, n = scores(model, text)
    route = model["route"]
    if n < model["min_n"]:
        return 0, tot, n
    best = min(range(len(tot)), key=lambda i: tot[i])
    if route[best] == 0:
        return 0, tot, n
    m_pub = min(tot[i] - tot[best] for i in range(len(tot)) if route[i] == 0)
    m_other = min(tot[i] - tot[best] for i in range(len(tot)) if i != best)
    if m_pub < model["t_en"] or m_other < model["t_other"] or m_pub < n * model["per_tri"]:
        return 0, tot, n
    return route[best], tot, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="lang_model.h")
    ap.add_argument("--tsv", help="evaluate a lang<TAB>text file")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    model = load_header(args.model)

    if args.tsv:
        conf = collections.defaultdict(collections.Counter)
        for line in open(args.tsv, encoding="utf-8"):
            src, _, text = line.rstrip("\n").partition("\t")
            conf[src][detect(model, text)[0]] += 1
        print("%-14s%s      n" % ("source", "".join("%7s" % c for c in CHANNELS)))
        for src, row in conf.items():
            n = sum(row.values())
            print("%-14s%s  %5d" % (src, "".join("%6.1f%%" % (100.0 * row[i] / n) for i in range(4)), n))
        return

    for line in sys.stdin:
        text = line.rstrip("\n")
        if not text:
            continue
        ch, tot, n = detect(model, text)
        if args.verbose:
            print("%s  n=%-3d costs=%s  | %s" % (CHANNELS[ch], n, tot, text))
        else:
            print("%s\t%s" % (CHANNELS[ch], text))


if __name__ == "__main__":
    main()
