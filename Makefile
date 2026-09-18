.PHONY: setup seed test test-live eval eval-live eval-baseline run run-deterministic demo no-candidates report clean

setup:
	python3 -m venv .venv && .venv/bin/pip install -q -e .
	@echo "Done. Copy .env.example to .env and set GOOGLE_CLOUD_PROJECT for live runs."

seed:
	.venv/bin/python -m data.generate

test:            ## Full suite, offline, no model calls
	.venv/bin/python -m pytest tests/ -q

eval:            ## Tier 1 deterministic gate, offline
	.venv/bin/python -m eval.run_eval --tier1-only

test-live:       ## Behavioural tests against a real model (opt-in)
	.venv/bin/python -m pytest tests/ -m live -q

eval-live:       ## Tier 1 + Tier 2 grounding checks (calls the model)
	.venv/bin/python -m eval.run_eval

eval-baseline:   ## Tier 2 plus the single-agent architecture comparison
	.venv/bin/python -m eval.run_eval --baseline

no-candidates:   ## Demonstrate the no-candidate path on a healthy pipeline
	.venv/bin/python -m app.run --fixtures data/fixtures_healthy

report:          ## Build docs/run_report.md from captured traces only
	.venv/bin/python scripts/generate_run_report.py

run-deterministic:  ## Triage with the agents switched off
	.venv/bin/python -m app.run --no-agents

run:             ## Full pipeline, stops for human approval
	.venv/bin/python -m app.run

demo:            ## The four-deal walkthrough, auto-approved
	.venv/bin/python -m app.run --yes

clean:
	rm -rf __pycache__ */__pycache__ .pytest_cache outbox/*.json
