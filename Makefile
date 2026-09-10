PY ?= python
export PYTHONPATH := src

.PHONY: install test board discover collect screen report all clean

install:
	$(PY) -m pip install -r requirements.txt

test:
	$(PY) -m pytest -q

board:
	$(PY) -m brackets.cli board

discover:
	$(PY) -m brackets.cli discover

collect:
	$(PY) -m brackets.cli -v collect --minutes $(MINUTES) --interval 60

screen:
	$(PY) -m brackets.cli screen

report:
	$(PY) -m brackets.cli report
	$(PY) scripts/capture.py

all: test screen report

clean:
	rm -rf results docs/img/*.png

MINUTES ?= 60
