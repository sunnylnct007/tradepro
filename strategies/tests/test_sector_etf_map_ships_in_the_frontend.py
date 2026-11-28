"""The symbol→sector-ETF map the chart header uses is a copy of the python
one (the frontend image sees only ./frontend — see
feedback_image_build_context_is_not_the_repo). Pinned here so they cannot
drift."""
import json
import pathlib

from tradepro_strategies import sector_rs as S

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_frontend_sector_etf_map_matches_python():
    d = json.loads((ROOT / "frontend" / "src" / "data" / "sectorEtf.json").read_text())
    assert d["SYMBOL_SECTOR_ETF"] == S.SYMBOL_SECTOR_ETF
    assert d["SECTOR_ETF"] == S.SECTOR_ETF
