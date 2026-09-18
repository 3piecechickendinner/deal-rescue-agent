"""Registers the deployed agent with Gemini Enterprise.

Contract confirmed against the live Discovery Engine v1alpha discovery document,
not from memory:

  POST projects/{p}/locations/global/collections/default_collection
       /engines/{engine}/assistants/{assistant}/agents
  {
    "displayName": ...,
    "description": ...,
    "adkAgentDefinition": {
      "provisionedReasoningEngine": {"reasoningEngine": "projects/.../reasoningEngines/..."}
    }
  }

`state` is output-only and reports what Gemini Enterprise actually did with the
registration. This script prints it verbatim. If registration fails, it prints
the error rather than reporting success.
"""

import argparse
import json
import subprocess
import sys


GCLOUD = "/Users/sd/Downloads/google-cloud-sdk/bin/gcloud"
BASE = "https://discoveryengine.googleapis.com/v1alpha"


def token() -> str:
    out = subprocess.run([GCLOUD, "auth", "print-access-token"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def call(method: str, url: str, project: str, body: dict = None) -> dict:
    """Uses curl rather than urllib: this Python build has no usable CA bundle."""
    cmd = ["curl", "-sS", "-X", method, url,
           "-H", f"Authorization: Bearer {token()}",
           "-H", f"x-goog-user-project: {project}",
           "-H", "Content-Type: application/json"]
    if body is not None:
        cmd += ["-d", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        return {"__error__": out.returncode, "body": out.stderr[:1500]}
    res = json.loads(out.stdout or "{}")
    if isinstance(res, dict) and "error" in res:
        return {"__error__": res["error"].get("code", 1), "body": json.dumps(res["error"])[:1500]}
    return res


def agents_url(project: str, engine: str, assistant: str) -> str:
    return (f"{BASE}/projects/{project}/locations/global/collections/"
            f"default_collection/engines/{engine}/assistants/{assistant}/agents")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="valid-perigee-480016-f3")
    ap.add_argument("--engine", default="databackfill_1789695823681")
    ap.add_argument("--assistant", default="default_assistant")
    ap.add_argument("--reasoning-engine", help="Full reasoningEngines/... resource name")
    ap.add_argument("--display-name", default="Deal Rescue Agent")
    ap.add_argument("--list", action="store_true", help="List registered agents and exit")
    ap.add_argument("--delete", help="Delete a registered agent by full resource name")
    args = ap.parse_args()

    url = agents_url(args.project, args.engine, args.assistant)

    if args.list:
        res = call("GET", url, args.project)
        for a in res.get("agents", []):
            kind = next((k for k in a if k.endswith("AgentDefinition")), "unknown")
            print(f"{a.get('state','?'):<12} {a.get('displayName'):<28} {kind}")
            print(f"             {a['name']}")
        if "__error__" in res:
            print(json.dumps(res, indent=2), file=sys.stderr)
            return 1
        return 0

    if args.delete:
        res = call("DELETE", f"{BASE}/{args.delete}", args.project)
        print(json.dumps(res, indent=2))
        return 1 if "__error__" in res else 0

    if not args.reasoning_engine:
        print("--reasoning-engine is required (deploy to Agent Engine first)", file=sys.stderr)
        return 2

    body = {
        "displayName": args.display_name,
        "description": (
            "Reviews the sales pipeline, identifies at-risk deals using deterministic "
            "scoring, investigates why each is stuck with cited evidence, and proposes "
            "a next action with a draft follow-up email. All actions require human "
            "approval; nothing is sent and no CRM record is changed."
        ),
        "adkAgentDefinition": {
            "provisionedReasoningEngine": {"reasoningEngine": args.reasoning_engine}
        },
        "starterPrompts": [
            {"text": "Which deals need my attention this week?"},
            {"text": "Why is the Northwind Traders deal stuck?"},
            {"text": "Show me the evidence behind the Contoso recommendation."},
        ],
    }

    res = call("POST", url, args.project, body)
    print(json.dumps(res, indent=2)[:2000])
    if "__error__" in res:
        return 1
    print(f"\nRegistered. state={res.get('state', 'NOT REPORTED')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
