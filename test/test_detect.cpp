// Test / demo driver for lang_detect.h.
//
//   test_detect                     read lines from stdin, print "<channel>\t<line>"
//   test_detect -v                  same, plus costs and trigram count
//   test_detect --tsv FILE          FILE has "<source>\t<text>" lines: print confusion table
//   test_detect --tsv FILE --dump   also print "<channel>\t<text>" per line (to diff with detect.py)
//   test_detect --expect FILE       FILE has "<expected>\t<text>" lines: fail on any mismatch
//   test_detect --bench             time the detector on a few fixed messages
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>
#include <string>
#include <vector>

#include "../lang_detect.h"

static const char* NAMES[] = {"EN", "PL", "SK", "DE"};

static int run_tsv(const char* path, bool dump) {
    std::ifstream f(path);
    if (!f) { fprintf(stderr, "cannot open %s\n", path); return 2; }
    std::map<std::string, std::vector<int>> conf;
    std::vector<std::string> order;
    std::string line;
    while (std::getline(f, line)) {
        size_t tab = line.find('\t');
        if (tab == std::string::npos) continue;
        std::string src = line.substr(0, tab);
        std::string text = line.substr(tab + 1);
        if (!conf.count(src)) { conf[src] = std::vector<int>(5, 0); order.push_back(src); }
        LangChannel ch = ld_detect(text.c_str(), text.size());
        conf[src][ch]++;
        conf[src][4]++;
        if (dump) printf("%s\t%s\n", NAMES[ch], text.c_str());
    }
    if (dump) return 0;
    printf("%-14s     EN     PL     SK     DE      n\n", "source");
    for (const std::string& src : order) {
        const std::vector<int>& r = conf[src];
        printf("%-14s", src.c_str());
        for (int i = 0; i < 4; i++) printf("%6.1f%%", 100.0 * r[i] / r[4]);
        printf("  %5d\n", r[4]);
    }
    return 0;
}

static int run_expect(const char* path) {
    std::ifstream f(path);
    if (!f) { fprintf(stderr, "cannot open %s\n", path); return 2; }
    std::string line;
    int total = 0, failed = 0;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#') continue;
        size_t tab = line.find('\t');
        if (tab == std::string::npos) continue;
        std::string want = line.substr(0, tab);
        std::string text = line.substr(tab + 1);
        LangScores sc;
        LangChannel ch = ld_detect(text.c_str(), text.size(), &sc);
        total++;
        bool ok = want == NAMES[ch];
        if (!ok) failed++;
        printf("%s %s -> %s  n=%u costs=[%u %u %u %u]  %s\n", ok ? "ok  " : "FAIL", want.c_str(), NAMES[ch],
               sc.ntrigrams, sc.cost[0], sc.cost[1], sc.cost[2], sc.cost[3], text.c_str());
    }
    printf("%d/%d passed\n", total - failed, total);
    return failed ? 1 : 0;
}

static int run_bench() {
    const char* msgs[] = {
        "Cześć wszystkim, czy ktoś słyszy mój węzeł w okolicy Krakowa?",
        "Ahojte, má niekto z vás repeater niekde na Zobore pri Nitre?",
        "Hallo zusammen, wer hat noch Empfang im Norden von Berlin? Hier ist alles ruhig.",
        "Hi all, testing my new node from the hill, anyone hearing this?",
    };
    const int reps = 20000;
    size_t bytes = 0;
    auto t0 = std::chrono::steady_clock::now();
    volatile int sink = 0;
    for (int r = 0; r < reps; r++)
        for (const char* m : msgs) { sink += ld_detect(m, strlen(m)); bytes += strlen(m); }
    auto t1 = std::chrono::steady_clock::now();
    double us = std::chrono::duration<double, std::micro>(t1 - t0).count();
    printf("%d messages, %.2f us/message, %.1f ns/byte (desktop)\n", reps * 4, us / (reps * 4), 1000.0 * us / bytes);
    printf("table: %d keys, %zu bytes of const data\n", LD_NKEYS,
           sizeof(LD_KEYS) + sizeof(LD_COSTS) + sizeof(LD_CHARMAP) + sizeof(LD_ROUTE));
    return 0;
}

int main(int argc, char** argv) {
    bool verbose = false, dump = false;
    const char* tsv = 0;
    const char* expect = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-v")) verbose = true;
        else if (!strcmp(argv[i], "--dump")) dump = true;
        else if (!strcmp(argv[i], "--bench")) return run_bench();
        else if (!strcmp(argv[i], "--tsv") && i + 1 < argc) tsv = argv[++i];
        else if (!strcmp(argv[i], "--expect") && i + 1 < argc) expect = argv[++i];
        else { fprintf(stderr, "unknown arg %s\n", argv[i]); return 2; }
    }
    if (tsv) return run_tsv(tsv, dump);
    if (expect) return run_expect(expect);

    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        LangScores sc;
        LangChannel ch = ld_detect(line.c_str(), line.size(), &sc);
        if (verbose)
            printf("%s  n=%-3u costs=[%u %u %u %u]  | %s\n", NAMES[ch], sc.ntrigrams,
                   sc.cost[0], sc.cost[1], sc.cost[2], sc.cost[3], line.c_str());
        else
            printf("%s\t%s\n", NAMES[ch], line.c_str());
    }
    return 0;
}
