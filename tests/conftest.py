import pytest
from core.db import load_db
from core.signals import evaluate_all


@pytest.fixture(scope="session")
def conn():
    return load_db()


@pytest.fixture(scope="session")
def risks(conn):
    return evaluate_all(conn)


@pytest.fixture(scope="session")
def by_id(risks):
    return {r.deal_id: r for r in risks}
