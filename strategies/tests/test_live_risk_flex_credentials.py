"""The live lane reads the Flex secret the API already holds — env first."""
from tradepro_strategies.cli.live_risk import FLEX_SECRET_ID, flex_credentials


def test_environment_wins_and_secrets_manager_is_not_called():
    called = []
    t, q, src = flex_credentials(
        env={"TRADEPRO_IBKR_FLEX_TOKEN": "env-tok", "TRADEPRO_IBKR_FLEX_POSITIONS_QUERY": "42"},
        fetch=lambda sid: called.append(sid) or {"token": "sm-tok", "positions_query_id": "9"})
    assert (t, q, src) == ("env-tok", "42", "environment")
    assert called == [], "with both env vars set, AWS must not be touched"


def test_falls_back_to_the_secret_the_api_uses():
    t, q, src = flex_credentials(env={}, fetch=lambda sid: {"token": "sm-tok", "positions_query_id": "9"}
                                 if sid == FLEX_SECRET_ID else None)
    assert (t, q) == ("sm-tok", "9") and FLEX_SECRET_ID in src


def test_the_secret_id_has_no_leading_slash():
    # /tradepro/ibkr-flex and tradepro/ibkr-flex are DIFFERENT secrets to AWS;
    # the API stores the latter. A prefixed id resolves to nothing, silently.
    assert FLEX_SECRET_ID == "tradepro/ibkr-flex"


def test_a_partial_env_is_completed_from_the_secret():
    t, q, src = flex_credentials(env={"TRADEPRO_IBKR_FLEX_TOKEN": "env-tok"},
                                 fetch=lambda sid: {"positions_query_id": "77"})
    assert (t, q) == ("env-tok", "77") and src.startswith("secrets manager")


def test_nothing_anywhere_is_an_empty_source_not_a_crash():
    assert flex_credentials(env={}, fetch=lambda sid: None) == ("", "", "")
