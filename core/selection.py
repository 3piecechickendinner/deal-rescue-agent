"""Which deals get investigated. Fully deterministic and therefore reproducible."""

from typing import List

import config
from schemas.models import DealRisk


def select_for_investigation(risks: List[DealRisk], limit: int = None) -> List[DealRisk]:
    """Rank by risk score and take the top N.

    Ties break on amount then deal_id so the same fixtures always yield the same
    deals in the same order. Identical inputs producing different outputs is the
    exact failure this architecture exists to prevent.
    """
    limit = limit or config.MAX_DEALS_INVESTIGATED
    eligible = [r for r in risks if r.band in ("CRITICAL", "AT_RISK")]
    ranked = sorted(eligible, key=lambda r: (-r.risk_score, -r.amount, r.deal_id))
    return ranked[:limit]
