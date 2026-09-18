from core.selection import select_for_investigation


def test_selection_is_stable_across_runs(risks):
    assert [r.deal_id for r in select_for_investigation(risks)] == \
           [r.deal_id for r in select_for_investigation(risks)]


def test_selection_respects_cap(risks):
    assert len(select_for_investigation(risks, limit=2)) == 2


def test_healthy_and_new_deals_are_never_selected(risks):
    ids = {r.deal_id for r in select_for_investigation(risks, limit=10)}
    assert "DEAL-1015" not in ids
    assert "DEAL-1120" not in ids


def test_ranked_by_risk_descending(risks):
    scores = [r.risk_score for r in select_for_investigation(risks, limit=10)]
    assert scores == sorted(scores, reverse=True)
