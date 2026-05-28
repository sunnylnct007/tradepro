"""tradepro_strategies.viz — chart framework.

One contract: every chart builder returns a Plotly figure as JSON-
serialisable dict. Frontend renders any chart with a single component;
new charts are added with a Python file + registry decorator, no UI
change required.

See ``registry`` for the decorator + lookup, and ``backtest_4panel`` /
``monte_carlo_fan`` for the trader-requested charts that anchor the
framework.
"""
from .registry import (  # noqa: F401
    ChartBuilder,
    ChartSpec,
    build_chart,
    list_charts,
    register_chart,
)

# Import side-effecting modules so their @register_chart decorators run.
from . import backtest_4panel  # noqa: F401
from . import monte_carlo_fan  # noqa: F401
from . import ichimoku_cloud  # noqa: F401
