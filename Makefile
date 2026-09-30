.PHONY: check test lint type sast deps sbom eval lock run
check: lint type test sast eval
lint:
	ruff format --check . && ruff check .
type:
	mypy .
test:
	pytest --cov --cov-report=term-missing:skip-covered
sast:
	bandit -q -r . -c pyproject.toml
deps:
	pip-audit -r requirements.lock --require-hashes
sbom:
	cyclonedx-py environment -o sbom.json
eval:
	python -m evals.run_evals >/dev/null
	python scripts/check_change_approvals.py
lock:
	pip-compile --generate-hashes --allow-unsafe --strip-extras -o requirements.lock requirements.in
run:
	./run.sh
