.PHONY: clean clean-test clean-pyc clean-build docs help
.DEFAULT_GOAL := help

PY ?= python3

help:
	@mh -f $(MAKEFILE_LIST) $(target) || echo "Please install mh from github/oz123/mh"
ifndef target
	@echo ""
	@echo "Use \`make help target=foo\` to learn more about foo."
endif

clean: clean-build clean-pyc clean-test ## remove all build, test, coverage and Python artifacts


clean-build: ## remove build artifacts
	rm -fr build/
	rm -fr dist/
	rm -fr .eggs/
	find . -name '*.egg-info' -exec rm -fr {} +
	find . -name '*.egg' -exec rm -f {} +

clean-pyc: ## remove Python file artifacts
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -fr {} +

clean-test: ## remove test and coverage artifacts
	rm -fr .tox/
	rm -f .coverage
	rm -fr htmlcov/

test: OPTS = --pdb
test: ## run tests quickly with the default Python
	pytest $(OPTS) -x -vv

coverage: ## check code coverage quickly with the default Python
	pytest -x -vv --cov --cov-report html --cov-config .coveragerc tests/

	#$(BROWSER) htmlcov/index.html

coverage-record: TMPFILE := $(shell mktemp)
coverage-record: coverage
	git show -s --format=%B HEAD > $(TMPFILE)
	coverage report >> $(TMPFILE)
	git commit --amend -F $(TMPFILE)
	rm $(TMPFILE)

docs: ## generate Sphinx HTML documentation, including API docs
	$(MAKE) -C docs clean
	$(MAKE) -C docs html
	$(BROWSER) docs/_build/html/index.html


dev: clean
	pip3 install --only-binary :all: -e .

install: clean
	pip3 install --only-binary :all: .

build-exec: ## build a single file executable of wolkenbrot 
	pip3 install --only-binary :all: .
	pyinstaller wolkenbrot.spec

build-exec-in-docker:  ## build an executable with pyinstaller
	#docker run --rm -w /usr/src -v $(CURDIR):/usr/src/ docker.io/oz123/pyinstaller-builder:latest bash -c "make install build-exec PY=$(PY)"
	docker run -it --rm -w /usr/src -v $(CURDIR):/usr/src/ docker.io/oz123/pyinstaller-builder:latest bash

build-sdist: clean
	$(PY) setup.py sdist

pypi-release:
	twine upload sdist/wolkenbrot.tar.gz
