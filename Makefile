CXX ?= c++
CXXFLAGS ?= -O2 -std=c++11 -Wall -Wextra -pedantic
DATA_DIR ?= data
SCRATCH ?= build

all: build/test_detect

build/test_detect: test/test_detect.cpp lang_detect.h lang_model.h
	@mkdir -p build
	$(CXX) $(CXXFLAGS) -o $@ test/test_detect.cpp

# Hand-written realistic messages with expected channels.
test: build/test_detect
	./build/test_detect --expect test/messages.tsv

bench: build/test_detect
	./build/test_detect --bench

# Download Tatoeba per-language sentence dumps (CC BY 2.0 FR) into $(DATA_DIR).
data:
	@mkdir -p $(DATA_DIR)
	@for l in eng pol slk deu ces; do \
	  echo "downloading $$l"; \
	  curl -sSfo $(DATA_DIR)/$${l}_sentences.tsv.bz2 https://downloads.tatoeba.org/exports/per_language/$$l/$${l}_sentences.tsv.bz2; \
	done

# Re-train the model from Tatoeba dumps in $(DATA_DIR) (see README).
train:
	python3 train.py --data-dir $(DATA_DIR) --out lang_model.h

# Check that the C++ port and the Python reference agree on a held-out set
# written by `python3 train.py --dump-test build/test.tsv`.
crosscheck: build/test_detect
	./build/test_detect --tsv build/test.tsv --dump > build/cpp.txt
	python3 detect.py --tsv build/test.tsv
	cut -f2- build/test.tsv | python3 detect.py > build/py.txt
	cmp build/cpp.txt build/py.txt && echo "C++ and Python agree on $$(wc -l < build/test.tsv) lines"

clean:
	rm -rf build

.PHONY: all test bench data train crosscheck clean
