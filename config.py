"""Central configuration. Every threshold the deterministic layer uses lives here."""

import os

# --- Model configuration (agents only; the deterministic layer never calls a model) ---
MODEL_ID = os.getenv("MODEL_ID", "gemini-2.5-flash")
USE_VERTEX = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "True")
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

# --- Deterministic signal thresholds ---
# Each is a business rule, not a learned parameter. Tune here, nowhere else.
STAGE_STALL_MULTIPLIER = 1.5      # days in stage vs. that stage's historical median
NO_CONTACT_DAYS = 14              # days since last two-way customer activity
CHAMPION_SILENT_DAYS = 21         # days since primary contact last engaged
CLOSE_DATE_SLIP_COUNT = 2         # number of close-date pushes
AMOUNT_EROSION_PCT = 0.20         # proportional drop from original amount
IMMINENT_CLOSE_DAYS = 14          # close date within N days
SINGLE_THREAD_MIN_AMOUNT = 50_000 # single-threading only matters above this size
MIN_DEAL_AGE_DAYS = 7             # below this we have too little history to judge

# --- Selection ---
MAX_DEALS_INVESTIGATED = 5        # hard cap on agent invocations per run

# --- Reproducibility ---
# Fixed "today" so every run, trace, and eval is byte-reproducible.
AS_OF_DATE = os.getenv("AS_OF_DATE", "2026-09-18")
RANDOM_SEED = 42

# Absolute so the package works when deployed (Agent Engine runs from a different cwd).
_ROOT = os.path.dirname(os.path.abspath(__file__))
# --- Data source ---
# "local" reads data/fixtures (default: keeps tests hermetic and offline).
# "gcs" reads the Salesforce-shaped export from the bucket below.
DATA_SOURCE = os.getenv("DATA_SOURCE", "local")
EXPORT_BUCKET = os.getenv("EXPORT_BUCKET", "deal-rescue-crm-export-valid-perigee-480016-f3")
EXPORT_PREFIX = os.getenv("EXPORT_PREFIX", "salesforce/opportunities")

# How stale a batch export may get before day-counting rules become unreliable.
# Silence-based rules are the vulnerable ones: a reply that landed after the
# export looks identical to no reply at all.
STALENESS_WARN_DAYS = float(os.getenv("STALENESS_WARN_DAYS", "1"))

# --- Managed ADK plugins ---
# BigQuery analytics is off unless a dataset is named, so tests stay offline.
BQ_ANALYTICS_DATASET = os.getenv("BQ_ANALYTICS_DATASET", "")
TOOL_MAX_RETRIES = int(os.getenv("TOOL_MAX_RETRIES", "2"))

# --- Model Armor ---
# Unset means disabled, which keeps the default test path offline.
MODEL_ARMOR_TEMPLATE = os.getenv("MODEL_ARMOR_TEMPLATE", "")
GCLOUD_BIN = os.getenv("GCLOUD_BIN", "")

# --- Memory Bank ---
# Reuses the deployed Agent Engine instance as the bank; no separate resource.
MEMORY_AGENT_ENGINE_ID = os.getenv("MEMORY_AGENT_ENGINE_ID", "")

DB_PATH = ":memory:"
FIXTURE_DIR = os.path.join(_ROOT, "data", "fixtures")
FIXTURE_DIR_HEALTHY = os.path.join(_ROOT, "data", "fixtures_healthy")
