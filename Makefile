PYTHON ?= python3

.PHONY: test verify
test:
	$(PYTHON) -m unittest discover -s tests -v

verify:
	$(PYTHON) scripts/verify.py
