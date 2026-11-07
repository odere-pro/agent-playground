"""The rendered-charts fixture the PoC-9 chart tests share (`chart_render`)."""

from __future__ import annotations

import pytest
from chart_render import Charts, make_charts


@pytest.fixture(scope="session")
def chart(tmp_path_factory: pytest.TempPathFactory) -> Charts:
    return make_charts(tmp_path_factory.mktemp("helm"))
