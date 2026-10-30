import logging
from datetime import timedelta

from conftest import JUP, NOW
from solpaper.config import ScreenCfg
from solpaper.data.rpc import HolderAccount, MintInfo
from solpaper.data.sources import Snapshot
from solpaper.screen import TokenFacts, screen_token

AUTH = "So11111111111111111111111111111111111111112"
POOL = "C1MgLojNLWBKADvu9BHdtgzz1oZX4dZ5zGdGcgvvW8Wz"


def snap(liq=1e6, vol=1e6, age_days=100):
    return Snapshot(JUP, "JUP", 1.0, liq, vol, POOL, "orca", AUTH, NOW - timedelta(days=age_days), "test", NOW)


def mint(mint_auth=None, freeze=None, exts=(), supply=1_000_000):
    return MintInfo(JUP, "spl-token", 6, supply, mint_auth, freeze, tuple(exts))


def holders(*amounts, owners=None):
    owners = owners or [f"owner{i}" for i in range(len(amounts))]
    return [HolderAccount(f"acct{i}", o, a) for i, (a, o) in enumerate(zip(amounts, owners))]


CFG = ScreenCfg(max_top_holder_pct=50, top_holder_count=3, min_liquidity_usd=100_000,
                min_volume_24h_usd=100_000, min_pool_age_days=30)


def run(cfg=CFG, **kw):
    facts = dict(mint_info=mint(), holders=holders(100_000, 50_000, 10_000), snapshot=snap())
    facts.update(kw)
    return screen_token(TokenFacts(JUP, **facts), cfg, NOW)


def test_clean_token_passes():
    r = run()
    assert r.passed and r.reasons == []
    assert r.metrics["top_holder_pct"] == 16.0


def test_rejects_active_mint_authority():
    r = run(mint_info=mint(mint_auth=AUTH))
    assert not r.passed and any("mint authority" in x for x in r.reasons)


def test_rejects_active_freeze_authority():
    r = run(mint_info=mint(freeze=AUTH))
    assert not r.passed and any("freeze authority" in x for x in r.reasons)


def test_allowlist_exempts_authority_checks_only():
    cfg = CFG.model_copy(update={"authority_allowlist": [JUP]})
    assert run(cfg, mint_info=mint(mint_auth=AUTH, freeze=AUTH)).passed
    r = run(cfg, mint_info=mint(mint_auth=AUTH), snapshot=snap(liq=10))
    assert not r.passed and len(r.reasons) == 1 and "liquidity" in r.reasons[0]


def test_rejects_risky_token2022_extensions():
    r = run(mint_info=mint(exts=["permanentDelegate", "metadataPointer"]))
    assert not r.passed and "permanentDelegate" in r.reasons[0]


def test_rejects_holder_concentration():
    r = run(holders=holders(400_000, 100_000, 50_000))
    assert not r.passed and "top 3 holders own 55.0%" in r.reasons[0]


def test_pool_vault_and_excluded_owners_not_counted():
    locker = "LockerXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    h = holders(600_000, 100_000, 20_000, 10_000, owners=[POOL, locker, "a", "b"])
    # the pool's own vault (owner == pool address) is excluded automatically
    assert run(holders=h).metrics["top_holder_pct"] == 13.0
    # without the pool exclusion the same holders would fail
    other_pool = snap()
    other_pool = other_pool.__class__(**{**other_pool.__dict__, "pool_address": "elsewhere"})
    assert not run(holders=h, snapshot=other_pool).passed
    # configured owners (lockers, burn addresses) are excluded too
    cfg = CFG.model_copy(update={"holder_exclude_owners": [JUP]})
    h2 = holders(600_000, 100_000, 20_000, owners=[JUP, "a", "b"])
    assert run(cfg, holders=h2).metrics["top_holder_pct"] == 12.0


def test_minimum_liquidity_volume_and_age():
    r = run(snapshot=snap(liq=50_000, vol=10_000, age_days=3))
    assert not r.passed
    joined = " | ".join(r.reasons)
    assert "liquidity" in joined and "24h volume" in joined and "pool age 3.0d" in joined


def test_missing_data_fails_closed():
    r = run(mint_info=None, holders=None, snapshot=None)
    assert not r.passed
    assert {"mint account data unavailable", "holder data unavailable", "no market snapshot"} <= set(r.reasons)


def test_every_rejection_is_logged(caplog):
    caplog.set_level(logging.INFO, logger="solpaper.screen")
    r = run(mint_info=mint(mint_auth=AUTH, freeze=AUTH), snapshot=snap(liq=1))
    logged = [rec.getMessage() for rec in caplog.records]
    assert len(r.reasons) == 3
    for reason in r.reasons:
        assert any(reason in m and JUP in m for m in logged)


def test_rugcheck_holders_parsed_and_mint_checked():
    import pytest

    from solpaper.data.sources import parse_rugcheck_holders

    body = {"mint": JUP, "topHolders": [{"address": "a1", "owner": "o1", "amount": 500}, {"address": "bad"}]}
    hs = parse_rugcheck_holders(body, JUP)
    assert [(h.token_account, h.owner, h.amount) for h in hs] == [("a1", "o1", 500)]
    with pytest.raises(ValueError):
        parse_rugcheck_holders({**body, "mint": AUTH}, JUP)  # report for another token
    with pytest.raises(ValueError):
        parse_rugcheck_holders({"mint": JUP, "topHolders": []}, JUP)


def test_holder_source_recorded_and_missing_data_flagged():
    r = screen_token(TokenFacts(JUP, mint(), holders(1000), snap(), "rugcheck"), CFG, NOW)
    assert r.metrics["holder_source"] == "rugcheck" and not r.data_missing
    assert run(holders=None).data_missing
