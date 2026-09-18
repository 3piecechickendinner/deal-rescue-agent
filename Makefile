.PHONY: setup seed test eval eval-live run run-deterministic demo clean

setup:
	python3 -m venv .venv && .venv/bin/pip install -q -e .
	@echo "Done. Copy .env.example to .env and set GOOGLE_CLOUD_PROJECT for live runs."

seed:
	.venv/bin/python -m data.generate

test:            ## Full suite, offline, no model calls
	.venv/bin/python -m pytest tests/ -q

eval:            ## Tier 1 deterministic gate, offline
	.venv/bin/python -m eval.run_eval --tier1-only

eval-live:       ## Tier 1 + Tier 2 grounding checks (calls the model)
	.venv/bin/python -m eval.run_eval

run-deterministic:  ## Triage with the agents switched off
	.venv/bin/python -m app.run --no-agents

run:             ## Full pipeline, stops for human approval
	.venv/bin/python -m app.run

demo:            ## The four-deal walkthrough, auto-approved
	.venv/bin/python -m app.run --yes

clean:
	rm -rf __pycache__ */__pycache__ .pytest_cache outbox/*.json
