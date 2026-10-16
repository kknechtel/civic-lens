"""Integrity extraction (on real Long Beach records) and red-flag rules (synthetic facts)."""
from datetime import date

import pytest

from conftest import FIXTURES
from services.integrity.extract import extract_all, extract_officials, extract_personnel, find_date, norm_name
from services.integrity.rules import Ctx, run_rules

NY = {
    "bid_threshold": {"purchase": 35000, "public_works": 20000, "citation": "GML §103"},
    "near_threshold_ratio": 0.85, "split_window_days": 90, "payout_review_amount": 25000,
    "change_order": {"cap_pct": 20},
    "pay_to_play": {"statutory": False, "window_days": 365, "contribution_threshold": 0, "contract_threshold": 0},
}
NJ_P2P = {**NY, "pay_to_play": {"statutory": True, "window_days": 365, "contribution_threshold": 300,
                                 "contract_threshold": 17500, "citation": "N.J.S.A. 19:44A-20.4"}}


# ── Extraction on real records ───────────────────────────────────────────────

@pytest.fixture(scope="module")
def agenda():
    return extract_all((FIXTURES / "lb_agenda_2025-10-07.txt").read_text())


def test_agenda_awards(agenda):
    by_item = {a.item_no: a for a in agenda.awards}
    assert len(agenda.awards) == 9
    assert all(a.meeting_date == date(2025, 10, 7) for a in agenda.awards)  # from the header, not a quoted 2021 date

    sw = by_item["1"]
    assert (sw.vendor_name, sw.amount, sw.account_code) == ("ICC Community Development Solutions", 20400.0, "A1680.54445")
    assert sw.vendor_address == "P.O. Box 772511, Detroit MI 48277"

    water = by_item["5"]
    assert water.procurement == "emergency" and water.bids_received == 3 and water.amount == 47850.0
    assert "Allen Industries" in water.vendor_name

    design = by_item["6"]
    assert design.vendor_name == "D&B Engineers and Architects"  # name wrapped across lines in the PDF
    assert (design.procurement, design.bids_received) == ("rfp", 10)

    pumper = by_item["9"]
    assert pumper.procurement == "state_contract" and pumper.amount == 1559554.0  # HGAC cooperative purchasing

    grant = by_item["4"]
    assert grant.action_type == "transfer" and grant.procurement == "n/a"  # "Emergency Services" is an agency name

    payouts = [a for a in agenda.awards if a.action_type == "payout"]
    assert {a.amount for a in payouts} == {8517.69, 67741.28}


def test_roll_call():
    officials = extract_officials((FIXTURES / "lb_proceedings_2024-08-20.txt").read_text())
    assert [(o.last_name, o.role) for o in officials] == [
        ("Finn", "Council President"), ("Bendo", "Council Members"), ("Fiumara", "Council Members"),
        ("Lester", "Council Members"), ("Reinhart", "Council Members")]


def test_personnel_report():
    people = extract_personnel((FIXTURES / "lb_personnel_2025-05-20.txt").read_text())
    assert people[0].last_name == "Miller" and people[0].action == "Appointed"
    assert people[0].salary == "$10.00/hr" and people[0].effective == "5/22/2025"
    assert len(people) >= 5


@pytest.mark.parametrize("raw,norm", [
    ("ICC Community Development Solutions, Inc.", "icc community development solutions"),
    ("D&B Engineers and Architects, P.C.", "d and b engineers and architects"),
    ("The Smith Group LLC", "smith group"),
])
def test_norm_name(raw, norm):
    assert norm_name(raw) == norm


def test_find_date():
    assert find_date("REGULAR MEETING held OCTOBER 7, 2025") == date(2025, 10, 7)
    assert find_date("Minutes 08/19/2025") == date(2025, 8, 19)


# ── Rules on synthetic facts ─────────────────────────────────────────────────

def award(**kw):
    base = {"document_id": "d1", "entity_key": "long_beach", "item_no": "1", "page": 1, "title": "Resolution for supplies",
            "vendor_name": "Acme Supply Inc.", "vendor_norm": "acme supply", "amount": 10000.0, "action_type": "purchase",
            "procurement": "unknown", "bids_received": None, "meeting_date": date(2025, 1, 7), "quote": "…"}
    base.update(kw)
    return base


def rules(*awards, config=NY, **ctx):
    return run_rules(Ctx(awards=list(awards), config=config, **ctx))


def by_rule(flags, rule):
    return [f for f in flags if f.rule == rule]


def test_near_threshold_only_without_competition():
    f = rules(award(amount=33000.0), award(document_id="d2", amount=34000.0, procurement="competitive_bid", vendor_norm="b"))
    hits = by_rule(f, "near_threshold")
    assert len(hits) == 1 and hits[0].amount == 33000.0 and "GML §103" in hits[0].explanation


def test_public_works_threshold_used_for_repairs():
    f = rules(award(title="Resolution for roof repairs", amount=18000.0))
    assert by_rule(f, "near_threshold")  # 18k is 90% of the 20k public-works threshold


def test_split_purchases_within_window():
    f = rules(award(document_id="d1", amount=20000.0, meeting_date=date(2025, 1, 7)),
              award(document_id="d2", amount=18000.0, meeting_date=date(2025, 2, 4)),
              award(document_id="d3", amount=19000.0, meeting_date=date(2025, 9, 1)))  # outside 90 days
    hits = by_rule(f, "split_purchases")
    assert len(hits) == 1 and hits[0].severity == "high" and hits[0].amount == 38000.0
    assert len(hits[0].evidence) == 2


def test_competitive_awards_are_not_split():
    f = rules(award(amount=20000.0, procurement="competitive_bid"),
              award(document_id="d2", amount=20000.0, procurement="competitive_bid", meeting_date=date(2025, 2, 1)))
    assert not by_rule(f, "split_purchases")


def test_repeat_emergency_vendor_is_high():
    f = rules(award(procurement="emergency", amount=40000.0),
              award(document_id="d2", procurement="emergency", amount=30000.0, meeting_date=date(2025, 3, 1)))
    hits = by_rule(f, "emergency_procurement")
    assert len(hits) == 1 and hits[0].severity == "high" and len(hits[0].evidence) == 2


def test_single_bid_and_duplicates():
    f = rules(award(procurement="competitive_bid", bids_received=1, amount=90000.0, vendor_norm="x", vendor_name="X"),
              award(document_id="d2", amount=12345.0), award(document_id="d3", amount=12345.0, meeting_date=date(2025, 2, 1)))
    assert len(by_rule(f, "single_bid")) == 1
    assert len(by_rule(f, "duplicate_award")) == 1


def test_noncompetitive_concentration():
    f = rules(*[award(document_id=f"d{i}", procurement="professional_services", amount=30000.0,
                      vendor_name="Counsel LLP", vendor_norm="counsel", meeting_date=date(2025, i, 1)) for i in range(1, 5)])
    hits = by_rule(f, "noncompetitive_concentration")
    assert len(hits) == 1 and hits[0].amount == 120000.0


def test_change_order_growth():
    f = rules(award(action_type="contract", procurement="competitive_bid", amount=100000.0, vendor_norm="paver"),
              award(document_id="d2", action_type="change_order", amount=30000.0, vendor_norm="paver"))
    hits = by_rule(f, "change_order_growth")
    assert len(hits) == 1 and "30%" in hits[0].title


def test_payouts_individual_and_fiscal_year_total():
    f = rules(*[award(document_id=f"d{i}", action_type="payout", procurement="n/a", amount=60000.0,
                      vendor_norm=None, vendor_name=None, fiscal_year="2025-26") for i in range(2)])
    assert len(by_rule(f, "payout")) == 2
    assert by_rule(f, "payout_total")[0].amount == 120000.0


def test_official_surname_hire_skips_common_names():
    officials = [{"entity_key": "long_beach", "last_name": "Fiumara", "name": "Fiumara", "role": "Council Members"},
                 {"entity_key": "long_beach", "last_name": "Miller", "name": "Miller", "role": "Council Members"}]
    personnel = [{"document_id": "p1", "entity_key": "long_beach", "last_name": "Fiumara", "first_name": "Ann",
                  "position": "Clerk", "quote": "Fiumara / Ann"},
                 {"document_id": "p1", "entity_key": "long_beach", "last_name": "Miller", "first_name": "Bo",
                  "position": "Lifeguard", "quote": "Miller / Bo"}]
    hits = by_rule(rules(officials=officials, personnel=personnel), "official_surname_hire")
    assert [h.subject for h in hits] == ["Ann Fiumara"] and hits[0].severity == "low"


def test_pay_to_play_nj_statutory():
    contrib = [{"source": "NJ ELEC", "contributor": "John Roe", "contributor_norm": "john roe",
                "employer": "Acme Supply, Inc.", "employer_norm": "acme supply", "recipient": "Committee to Elect",
                "amount": 500.0, "date": date(2024, 11, 1)}]
    f = rules(award(amount=50000.0, procurement="professional_services", meeting_date=date(2025, 3, 4)),
              config=NJ_P2P, contributions=contrib)
    hits = by_rule(f, "pay_to_play")
    assert len(hits) == 1 and hits[0].severity == "high" and "19:44A-20.4" in hits[0].explanation
    # Under the $300 statutory threshold → no flag
    contrib[0]["amount"] = 250.0
    assert not by_rule(rules(award(amount=50000.0, meeting_date=date(2025, 3, 4)), config=NJ_P2P,
                             contributions=contrib), "pay_to_play")


def test_oversight_findings_and_severity():
    doc = {"document_id": "a1", "title": "OSC Budget Review 2021", "entity_key": "ny",
           "text": "## Page 3\nThe City remains in significant fiscal stress. Departments did not comply with policy."}
    hits = by_rule(rules(oversight_docs=[doc]), "oversight_findings")
    assert hits[0].severity == "high" and hits[0].evidence[0]["page"] == 3


def test_fingerprints_stable_and_disabled_rules():
    a = award(amount=33000.0)
    assert [f.fingerprint for f in rules(a)] == [f.fingerprint for f in rules(dict(a))]
    assert not rules(a, config={**NY, "disabled_rules": ["near_threshold"]})


def test_agenda_end_to_end_flags(agenda):
    """Real agenda → rules: the emergency repair and the payout funding are surfaced with evidence."""
    rows = [dict(a.__dict__, document_id="agenda", entity_key="long_beach", vendor_norm=norm_name(a.vendor_name) or None,
                 fiscal_year="2025-26") for a in agenda.awards]
    flags = rules(*rows)
    kinds = {f.rule for f in flags}
    assert "emergency_procurement" in kinds and "payout" in kinds
    em = by_rule(flags, "emergency_procurement")[0]
    assert em.evidence[0]["quote"] and em.amount == 47850.0


# ── NJ bill lists ────────────────────────────────────────────────────────────

BILL_LIST = """## Page 12
Bill List By Vendor Name
vendor #
Name
SUBUR005
SUBURBAN DISPOSAL INC
26-01099 09/10/26 August 2026 recycling
1 August 2026 recycling
6-01-26-305-000-294
vendor Total:
69,497.40
## Page 13
PADUL005
PADULA LAW GROUP
26-01126 09/11/26 legal services august
6-01-20-155-000-294
Vendor Total: 2,423.50
Total Of All Funds:
71,920.90
"""


def test_bill_list_parser():
    from services.integrity.extract import extract_bill_list
    rows = extract_bill_list(BILL_LIST)
    assert [(r.vendor_name, r.amount) for r in rows] == [("SUBURBAN DISPOSAL INC", 69497.40), ("PADULA LAW GROUP", 2423.50)]
    assert rows[0].po_numbers == ["26-01099"] and rows[0].accounts == ["6-01-26-305-000-294"]
    assert rows[0].page == 12


def test_payments_over_threshold_unless_competitively_awarded():
    pay = award(action_type="payment", procurement="n/a", amount=69497.40, vendor_name="Suburban Disposal Inc",
                vendor_norm="suburban disposal", fiscal_year="2026")
    hits = by_rule(rules(pay, config={**NY, "bid_threshold": {"purchase": 44000}}), "payments_over_threshold")
    assert len(hits) == 1 and hits[0].severity == "medium"
    bid = award(document_id="bid", procurement="competitive_bid", vendor_norm="suburban disposal", amount=500000.0)
    assert not by_rule(rules(pay, bid, config={**NY, "bid_threshold": {"purchase": 44000}}), "payments_over_threshold")
