from conftest import BONK, JUP, make_settings, ohlcv
from ghostfill.backtest import BacktestInput
from ghostfill.discover import parse_top_pools
from ghostfill.research import format_table, research

SOL = "So11111111111111111111111111111111111111112"


def test_research_covers_every_family_and_flags_random_walks():
    inp = BacktestInput({JUP: ohlcv(700, seed=21), BONK: ohlcv(700, seed=22)}, {JUP: 5e6, BONK: 5e6},
                        {JUP: "orca", BONK: "orca"}, 150.0)
    grids = {"ma_crossover": {"fast": [5], "slow": [20, 40]}, "donchian_breakout": {"entry": [20], "exit": [10]},
             "zscore_reversion": {"window": [20], "entry_z": [2.0], "exit_z": [0.0]},
             "rsi_pullback": {"trend": [50], "rsi": [14], "entry": [40], "exit": [60]}}
    rows = research(inp, make_settings(walk_forward={"splits": 3}), "1h", grids)
    assert {r.strategy for r in rows} == set(grids)
    text = format_table(rows)
    assert "RESEARCH" in text and "of 4" in text


def test_discover_parses_base_mints_and_skips_quotes():
    body = {"data": [
        {"relationships": {"base_token": {"data": {"id": f"solana_{JUP}"}}}},
        {"relationships": {"base_token": {"data": {"id": f"solana_{SOL}"}}}},   # quote token, skipped
        {"relationships": {"base_token": {"data": {"id": f"solana_{JUP}"}}}},   # duplicate pool
        {"relationships": {"base_token": {"data": {"id": "eth_0xabc"}}}},       # other chain
        {"relationships": {"base_token": {"data": {"id": f"solana_{BONK}"}}}},
    ]}
    assert parse_top_pools(body) == [JUP, BONK]
