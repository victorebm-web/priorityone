"""Escalation routing: the owning team and the named person for the account's region."""
from types import SimpleNamespace as NS

from actions import RECIPIENTS
from escalation import ROUTES, TEAMS, directory, recommend
from models import ESCALATION_TARGETS
from scoring import SIGNAL_WEIGHTS


def r(t):
    return {"signal_type": t, "signal_id": 1}


ACC = NS(region="EMEA", segment="Enterprise")


def test_each_problem_goes_to_the_team_that_owns_it():
    assert recommend(ACC, [r("sev1_open")], "Ingrid Vos")["team"] == "support"
    assert recommend(ACC, [r("chargeback_spike")], "Ingrid Vos")["team"] == "risk"
    assert recommend(ACC, [r("nps_detractor")], "Ingrid Vos")["name"] == "Ingrid Vos"
    assert recommend(ACC, [r("renewal_approaching")], None)["team"] == "finance"
    assert recommend(ACC, [{"signal_type": "resurfaced"}], None) is None


def test_a_renewal_with_a_live_problem_goes_to_the_manager_not_the_deal_desk():
    out = recommend(ACC, [r("renewal_approaching"), r("volume_drop")], "Ingrid Vos")
    assert out["team"] == "manager" and "renewal with a live problem" in out["why"]


def test_the_named_person_depends_on_region_and_comes_with_a_slack_handle():
    na, apac = recommend(NS(region="NA"), [r("sev1_open")], None), recommend(NS(region="APAC"), [r("sev1_open")], None)
    assert na["name"] != apac["name"] and na["slack"].startswith("#merchant-support-escalations · @")


def test_every_signal_has_a_route_and_every_team_is_a_valid_target():
    assert set(ROUTES) == set(SIGNAL_WEIGHTS)
    assert set(directory(ACC, "Ingrid Vos")) == set(ESCALATION_TARGETS) == set(RECIPIENTS) - {"customer"}
    assert set(TEAMS) | {"manager"} == set(ESCALATION_TARGETS)
