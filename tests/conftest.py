"""Shared test setup.

The supervisor's agents are cached for the process -- that is the point of item
2.1 in `docs/framework-alignment.md`: the tools take no run-specific state, so the
agent is built once. Tests replace the model route per test, which a cached agent
would ignore, so each test gets a fresh pair.
"""

from __future__ import annotations

import pytest

from trip_planner import supervisor


@pytest.fixture(autouse=True)
def fresh_supervisor_agents():
    supervisor._dispatch_agent.cache_clear()
    supervisor._revision_agent.cache_clear()
    yield
    supervisor._dispatch_agent.cache_clear()
    supervisor._revision_agent.cache_clear()
