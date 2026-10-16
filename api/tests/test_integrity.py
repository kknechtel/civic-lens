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


def test_non_spending_amounts_are_classified_out():
    from services.integrity.extract import extract_awards
    text = """Item No. 1
Resolution Authorizing the Adoption of the Operating Budget for the Fiscal Year Commencing July 1, 2025
RESOLVED, that the budget appropriating sums of $113,843,910.00 be adopted.
Item No. 2
Resolution Authorizing Publication of an Amendatory Bond Ordinance
RESOLVED, that bonds in the amount of $169,252,981.00 be issued for the principal of and interest thereon.
Item No. 3
Resolution Authorizing Renewal of Property Insurance
WHEREAS, the City suffered flood damage and FEMA emergency declarations;
RESOLVED, that coverage with total insurable value of $1,860,106,700.00 be renewed with Insurance Services."""
    kinds = [(a.action_type, a.procurement) for a in extract_awards(text)]
    assert kinds == [("budget_adoption", "n/a"), ("bond", "n/a"), ("insurance", "n/a")]


def test_whereas_background_emergency_is_not_emergency_procurement():
    from services.integrity.extract import extract_awards
    text = """Item No. 4
Resolution Authorizing a Contract for Boardwalk Repairs
WHEREAS, after the Hurricane Sandy emergency the boardwalk was rebuilt;
WHEREAS, three (3) bids were received;
RESOLVED, that a contract be awarded to Shore Builders Inc., the lowest responsible bidder, for $250,000.00."""
    a = extract_awards(text)[0]
    assert a.procurement == "competitive_bid" and a.bids_received == 3


def test_dedupe_collapses_agenda_backup_minutes():
    from services.integrity.runner import dedupe_awards
    base = award(vendor_norm="acme supply", amount=47850.0)
    rows = dedupe_awards([dict(base, document_id="agenda"), dict(base, document_id="backup"), dict(base, document_id="minutes")])
    assert len(rows) == 1 and len(rows[0]["copies"]) == 2


def test_attached_audit_does_not_become_an_award_amount():
    from services.integrity.extract import extract_awards
    audit = " ".join(["Fund balance was $81,798,874.00 at year end."] * 200)
    text = f"""Item No. 7
Resolution Authorizing the City Manager to Purchase Software Services
RESOLVED, that the City Manager purchase software services from Tyler Technologies, Inc. at a cost of $48,500.00.
{audit}"""
    a = extract_awards(text)[0]
    assert a.amount == 48500.0 and a.vendor_name == "Tyler Technologies, Inc."


@pytest.mark.parametrize("name,ok", [
    ("Tully Environmental, Inc.", True),
    ("20 minute interview. Final selection of the Consultant", False),
    ("Section 103-D of the General Municipal law, the Contractor", False),
])
def test_plausible_vendor(name, ok):
    from services.integrity.extract import plausible_vendor
    assert plausible_vendor(name) is ok


def test_canonical_vendors_merge_variants_not_neighbours():
    from services.integrity.runner import canonical_vendors
    c = canonical_vendors(["a i i allen industries", "allen industries", "i i allen industries", "allen electric",
                           "thomas novelli contracting", "t novelli contracting"])
    assert c["a i i allen industries"] == c["allen industries"] == c["i i allen industries"]
    assert c["allen electric"] != c["allen industries"]
    assert c["t novelli contracting"] == c["thomas novelli contracting"]


def test_audit_boilerplate_is_not_a_fraud_finding():
    boiler = {"document_id": "a", "title": "Single Audit", "entity_key": "x",
              "text": "Internal controls reduce the likelihood that significant errors or fraud will occur. "
                      "Supplies could be subject to theft."}
    real = {"document_id": "b", "title": "OSC audit", "entity_key": "x",
            "text": "We found instances of misappropriation of cash receipts totalling $12,000."}
    hits = by_rule(rules(oversight_docs=[boiler, real]), "oversight_findings")
    assert [h.subject for h in hits] == ["OSC audit"] and hits[0].severity == "high"


def test_emergency_in_vendor_or_dispatch_names_is_not_emergency_procurement():
    from services.integrity.extract import extract_awards
    text = """Item No. 2
Resolution Authorizing Purchase of Dispatch Furniture for Emergency Communications
WHEREAS, two (2) bids were received;
RESOLVED, that furniture be purchased from AAA Emergency Supply Co., Inc. at a cost of $79,991.00."""
    assert extract_awards(text)[0].procurement == "competitive_bid"


def test_amendments_and_cooperative_purchases_are_not_noncompetitive():
    from services.integrity.extract import extract_awards
    text = """Item No. 3
Resolution Authorizing Additional Services
WHEREAS, pursuant to Resolution No. 104/14 the City retained D&B Engineers and Architects, P.C.;
RESOLVED, that additional services be authorized with D&B Engineers and Architects, P.C. at a cost of $884,283.00.
Item No. 4
Resolution Authorizing Purchase of Stretchers
WHEREAS, the City is a member of SAVVIK Buying Group, a cooperative;
RESOLVED, that stretchers be purchased from Stryker Sales, LLC at a cost of $112,889.38."""
    a, b = extract_awards(text)
    assert (a.action_type, a.procurement) == ("change_order", "prior_award")
    assert b.procurement == "state_contract"


def test_canonical_vendors_transitive_and_deterministic():
    from services.integrity.runner import canonical_vendors
    names = ["thomas novelli", "t novelli contracting", "thomas novelli contracting"]
    for order in (names, list(reversed(names))):
        c = canonical_vendors(order)
        assert len(set(c.values())) == 1 and c["thomas novelli"] == "thomas novelli contracting"


# ── NJ resolution formats (real Atlantic Highlands 2026 resolutions) ──────────

def _ah(name):
    facts = extract_all((FIXTURES / "ah" / f"{name}.txt").read_text())
    assert len(facts.awards) == 1
    return facts, facts.awards[0]


def test_ah_payment_of_bills_sums_funds():
    facts, a = _ah("bills")
    assert a.action_type == "bill_payment" and a.procurement == "n/a"
    assert a.amount == pytest.approx(1_589_422.32)
    assert a.details["funds"]["01 CURRENT FUND"] == pytest.approx(1_208_775.32)
    assert a.details["funds"]["30 HARBOR"] == pytest.approx(283_114.12)
    assert a.details["votes"]["moved"] == "Crowley" and len(a.details["votes"]["yes"]) == 6
    assert {o.name for o in facts.officials} >= {"Crowley", "Scatassa"}  # roll call, no Present: line


def test_ah_non_fair_and_open_contract():
    _, a = _ah("nfo")
    assert a.vendor_name.lower() == "the aubrey group"
    assert a.amount == 30_000 and a.action_type == "contract" and a.procurement == "no_bid"
    assert a.details["non_fair_open"] and a.details["p2p_disclosure"]
    assert a.details["meeting_type"] == "Reorganization" and a.meeting_date == date(2026, 1, 1)
    assert a.title.startswith("AUTHORIZING A NON-FAIR AND OPEN")


def test_ah_fair_and_open_appointment():
    _, a = _ah("attorney")
    assert a.vendor_name.startswith("Marguerite M. Schaffer")
    assert a.amount == 35_000 and a.procurement == "rfp"
    assert a.details["fair_open"] and a.details["appointment"]


def test_ah_change_order_increase_not_total():
    _, a = _ah("change_order")
    assert a.vendor_name == "FIORE PAVING" and a.action_type == "change_order"
    assert a.amount == pytest.approx(4_917.82)
    assert a.details["base_amount"] == pytest.approx(367_617.75)
    assert a.details["new_total"] == pytest.approx(372_533.57)


def _nj_award(**kw):
    base = {"document_id": "d", "entity_key": "ah", "vendor_name": "Acme Law", "vendor_norm": "acme law",
            "action_type": "contract", "procurement": "no_bid", "amount": 40000, "meeting_date": date(2026, 1, 1),
            "details": {}}
    base.update(kw)
    return base


def test_change_order_growth_uses_stated_base():
    awards = [_nj_award(action_type="change_order", amount=90000, details={"base_amount": 300000}),
              _nj_award(action_type="contract", amount=50000)]  # a smaller unrelated award must not be the base
    flags = run_rules(Ctx(awards=awards, config=NJ_P2P), only=["change_order_growth"])
    assert len(flags) == 1 and "30%" in flags[0].title


def test_nfo_missing_disclosure():
    ok = _nj_award(details={"non_fair_open": True, "p2p_disclosure": True})
    bad = _nj_award(vendor_norm="b", details={"non_fair_open": True, "p2p_disclosure": False})
    small = _nj_award(vendor_norm="c", amount=9000, details={"non_fair_open": True, "p2p_disclosure": False})
    flags = run_rules(Ctx(awards=[ok, bad, small], config=NJ_P2P), only=["nfo_missing_disclosure"])
    assert [f.subject for f in flags] == ["Acme Law"] and flags[0].amount == 40000
    assert not run_rules(Ctx(awards=[bad], config=NY), only=["nfo_missing_disclosure"])


def test_professional_tenure():
    awards = [_nj_award(meeting_date=date(y, 1, 1), details={"appointment": True, "non_fair_open": y > 2023})
              for y in range(2021, 2027)]
    flags = run_rules(Ctx(awards=awards, config=NJ_P2P), only=["professional_tenure"])
    assert len(flags) == 1 and "6 years running" in flags[0].title and flags[0].severity == "medium"
    assert not run_rules(Ctx(awards=awards[:3], config=NJ_P2P), only=["professional_tenure"])


def test_bill_list_spike():
    awards = [_nj_award(action_type="bill_payment", procurement="n/a", amount=100000 + i, vendor_norm=None,
                        meeting_date=date(2025, 1 + i, 1)) for i in range(10)]
    awards.append(_nj_award(action_type="bill_payment", procurement="n/a", amount=900000, vendor_norm=None,
                            meeting_date=date(2025, 12, 1), details={"funds": {"01 CURRENT FUND": 900000}}))
    flags = run_rules(Ctx(awards=awards, config=NJ_P2P), only=["bill_list_spike"])
    assert len(flags) == 1 and flags[0].amount == 900000


def test_rules_tolerate_undated_awards():
    awards = [_nj_award(meeting_date=None, amount=200000) for _ in range(3)]
    assert run_rules(Ctx(awards=awards, config=NJ_P2P))  # no OverflowError on date.min


def test_p2p_threshold_split_and_professional_exempt_from_bid_split():
    nfo = {"non_fair_open": True, "p2p_disclosure": False}
    awards = [_nj_award(amount=5000, meeting_date=date(2026, 2, 2), details=nfo),
              _nj_award(amount=10000, meeting_date=date(2026, 2, 2), details=nfo),
              _nj_award(amount=5000, meeting_date=date(2026, 5, 4), details=nfo),
              _nj_award(amount=40000, meeting_date=date(2026, 6, 15), details=nfo)]
    flags = run_rules(Ctx(awards=awards, config=NJ_P2P))
    rules = {f.rule for f in flags}
    assert "p2p_threshold_split" in rules and "split_purchases" not in rules and "near_threshold" not in rules
    split = next(f for f in flags if f.rule == "p2p_threshold_split")
    assert split.severity == "high" and split.amount == 20000  # the $40k award is over the line, not part of the split
    assert "nfo_missing_disclosure" in rules  # the $40k one


def test_nj_budget_mechanics_are_not_spending():
    from services.integrity.extract import extract_awards
    text = ("RESOLUTION 073-2018\nEMERGENCY TEMPORARY APPROPRIATION\nWHEREAS, an emergency condition has arisen ... "
            "NOW, THEREFORE, BE IT RESOLVED that an emergency temporary appropriation be made in the amount of $806,345.00")
    (a,) = extract_awards(text)
    assert a.action_type == "budget_adoption" and a.procurement == "n/a"


def test_successor_firm_and_master_coverage_from_real_resolutions():
    """CME's fair-and-open engineer (2025-004) moves to Colliers, which gets an NFO (2025-087)."""
    facts = [extract_all((FIXTURES / "ah" / f"{n}.txt").read_text()).awards[0] for n in ("engineer_2025", "nfo_engineer_2025")]
    from services.integrity.extract import norm_name
    awards = [{**{k: getattr(a, k) for k in ("vendor_name", "amount", "action_type", "procurement", "meeting_date", "details")},
               "vendor_norm": norm_name(a.vendor_name), "entity_key": "ah", "document_id": n}
              for n, a in enumerate(facts)]
    awards.append(_nj_award(vendor_name="Colliers Engineering & Design", vendor_norm=norm_name("Colliers Engineering & Design"),
                            amount=40000, meeting_date=date(2026, 6, 15),
                            details={"non_fair_open": True, "p2p_disclosure": False}))
    flags = run_rules(Ctx(awards=awards, config=NJ_P2P))
    by_rule = {f.rule: f for f in flags}
    s = by_rule["successor_firm_nfo"]
    assert "Rohmeyer" in s.title and "CME Associates" in s.title and s.amount == 40000
    assert "nfo_missing_disclosure" not in by_rule  # covered by 2025-087's disclosure through 2027-12-31


def test_emergency_after_planning_on_real_resolutions():
    """2025-036 engineered 'emergency temporary repairs' to the Upper Prospect wall in February;
    2025-068 (undated, numbered after 061 of March 17) awarded the 'emergency' work."""
    from services.integrity.extract import norm_name
    from services.integrity.runner import estimate_dates_from_numbering
    awards = []
    for n in ("wall_engineering", "sewer_emergency", "wall_emergency"):
        (a,) = extract_all((FIXTURES / "ah" / f"{n}.txt").read_text()).awards
        awards.append({**{k: getattr(a, k) for k in ("title", "vendor_name", "amount", "action_type", "procurement",
                                                     "meeting_date", "details")},
                       "vendor_norm": norm_name(a.vendor_name), "entity_key": "ah", "document_id": n})
    wall = awards[2]
    assert wall["meeting_date"] is None and wall["procurement"] == "emergency" and wall["amount"] == 237000
    awards.append(_nj_award(meeting_date=date(2025, 5, 19), details={"resolution_no": "087-2025"}))
    estimate_dates_from_numbering(awards)
    assert wall["meeting_date"] == date(2025, 3, 17) and wall["details"]["date_estimated"]
    flags = run_rules(Ctx(awards=awards, config=NJ_P2P), only=["emergency_after_planning"])
    assert len(flags) == 1 and flags[0].severity == "medium" and "≥42 days" in flags[0].title  # lower bound
    assert flags[0].evidence[0]["document_id"] == "wall_engineering"


def test_bid_threshold_follows_award_date():
    cfg = {**NJ_P2P, "bid_threshold": {"purchase": 53000, "history": [
        {"from": "2020-07-01", "purchase": 44000}, {"from": "2025-07-01", "purchase": 53000}]}}
    ctx = Ctx(awards=[], config=cfg)
    assert ctx.threshold({"meeting_date": date(2025, 6, 30)}) == 44000
    assert ctx.threshold({"meeting_date": date(2025, 7, 1)}) == 53000
    flags = run_rules(Ctx(awards=[_nj_award(procurement="no_bid", amount=46000, meeting_date=date(2026, 3, 1))], config=cfg),
                      only=["near_threshold"])
    assert flags and "$53,000.00" in flags[0].title  # 87% of $53k; under the old $44k it would be over
