.PHONY: setup seed test test-live eval eval-live eval-baseline run run-deterministic run-gcs demo no-candidates report publish-export publish-export-stale deploy ge-register ge-list ge-delete clean

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

publish-export:  ## Write the synthetic Salesforce-shaped export to the GCS bucket
	PYTHONPATH=$(PWD) .venv/bin/python scripts/publish_export.py $(ARGS)

publish-export-stale:  ## Same, but backdated 3 days, to exercise staleness handling
	PYTHONPATH=$(PWD) .venv/bin/python scripts/publish_export.py --stale-days 3

run-gcs:         ## Run the pipeline against the GCS export instead of local fixtures
	DATA_SOURCE=gcs PYTHONPATH=$(PWD) .venv/bin/python -m app.run

deploy:          ## Deploy the root agent to Vertex AI Agent Engine
	PYTHONPATH=$(PWD) .venv/bin/adk deploy agent_engine \
	  --project=$(or $(PROJECT),valid-perigee-480016-f3) --region=us-central1 \
	  --display_name="Deal Rescue Agent" \
	  --extra_packages=config.py --extra_packages=deal_agents --extra_packages=core \
	  --agent_engine_id=$(or $(ENGINE_ID),3605276087489134592) --otel_to_cloud \
	  --extra_packages=schemas --extra_packages=verify --extra_packages=data \
	  deal_rescue

ge-register:     ## Register the deployed agent with Gemini Enterprise (pass RE=...)
	.venv/bin/python scripts/register_gemini_enterprise.py --reasoning-engine "$(RE)"

ge-list:         ## List agents registered in the Gemini Enterprise app
	.venv/bin/python scripts/register_gemini_enterprise.py --list

ge-delete:       ## Remove the registration (pass AGENT=full/resource/name)
	.venv/bin/python scripts/register_gemini_enterprise.py --delete "$(AGENT)"

clean:
	rm -rf __pycache__ */__pycache__ .pytest_cache outbox/*.json
