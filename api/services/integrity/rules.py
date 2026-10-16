"""
Red-flag rules over extracted facts.

Each rule is a pure function: (Context) -> list[Flag]. They run on plain dicts
so they can be tested without a database. Thresholds and citations come from
the deployment's `integrity` config (set per state in jurisdictions/us/<st>/).

Flags are leads, not findings. Every flag says what pattern was seen, why it
matters, the common innocent explanations, and cites the documents.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

SEV_RANK = {"low": 1, "medium": 2, "high": 3}

COMPETITIVE = {"competitive_bid", "rfp", "state_contract"}
# prior_award (amends/continues an earlier contract) is neither: growth rules cover it.
NONCOMPETITIVE = {"professional_services", "no_bid", "sole_source", "unknown"}
SPEND = {"contract", "purchase", "change_order", "other"}
# Exempt from bidding by statute (NJ 40A:11-5, 40A:65; NY GML §103/§104-b/Art. 5-G), so bid-threshold
# rules (near_threshold, split_purchases) don't apply. Professional services have their own tests below.
BID_EXEMPT = COMPETITIVE | {"professional_services", "intergovernmental", "prior_award"}


def _professional(a: dict) -> bool:
    d = a.get("details") or {}
    return a.get("procurement") == "professional_services" or bool(d.get("non_fair_open") or d.get("fair_open"))

_PUBLIC_WORKS = re.compile(r"construct|reconstruct|repair|paving|install|renovat|rehabilitat|demolition|"
                           r"replace(?:ment)?\s+of|roof|sewer|water\s+main|drainage|bulkhead|boardwalk", re.I)

# Most frequent US surnames (Census 2010 top ~120) — a match on these says little.
COMMON_SURNAMES = set("""smith johnson williams brown jones garcia miller davis rodriguez martinez hernandez lopez
gonzalez wilson anderson thomas taylor moore jackson martin lee perez thompson white harris sanchez clark ramirez
lewis robinson walker young allen king wright scott torres nguyen hill flores green adams nelson baker hall rivera
campbell mitchell carter roberts gomez phillips evans turner diaz parker cruz edwards collins reyes stewart morris
morales murphy cook rogers gutierrez ortiz morgan cooper peterson bailey reed kelly howard ramos kim cox ward
richardson watson brooks chavez wood james bennett gray mendoza ruiz hughes price alvarez castillo sanders patel
myers long ross foster jimenez powell jenkins perry russell sullivan bell coleman butler henderson barnes gonzales
fisher vasquez simmons romero jordan patterson alexander hamilton graham reynolds griffin wallace moreno west cole
hayes bryant herrera gibson ellis tran medina aguilar stevens murray ford castro marshall owens harrison fernandez
mcdonald woods washington kennedy wells vargas henry chen freeman webb tucker guzman burns crawford olson simpson
porter hunter gordon mendez silva shaw snyder mason dixon munoz hunt hicks holmes palmer wagner black robertson
boyd rose stone salazar fox warren mills meyer rice schmidt garza daniels ferguson nichols stephens soto weaver
ryan gardner payne grant dunn kelley spencer hawkins arnold pierce vazquez hansen peters santos hart bradley knight
elliott cunningham duncan armstrong hudson carroll lane riley andrews alvarado ray delgado berry perkins hoffman
johnston matthews pena richards contreras willis carpenter lawrence sandoval guerrero george chapman rios estrada
ortega watkins greene nunez wheeler valdez harper burke larson santiago maldonado morrison franklin carlson austin
dominguez carr lawson jacobs obrien lynch singh vega bishop montgomery oliver jensen harvey williamson gilbert
dean sims espinoza howell li wong reid hanson le mccoy garrett burton fuller wang weber welch rojas lucas marquez
fields park yang little banks padilla day walsh bowman schultz luna fowler mejia davidson acosta brewer may holland
juarez newman pearson curtis cortez douglas schneider joseph barrett navarro figueroa keller avila wade molina
stanley hopkins campos barnett bates chambers caldwell beck lambert miranda byrd craig ayala lowe frazier powers
neal leonard gregory carrillo sutton fleming rhodes shelton schwartz norris jennings watts duran walters cohen
mcdaniel moran parks steele vaughn becker holt deleon barker terry hale leon hail benson haynes horton miles lyons
pham graves bush thornton wolfe warner cabrera mckinney mann zimmerman dawson lara fletcher page mccarthy love
robles cervantes solis erickson reeves chang klein salinas fuentes baldwin daniel simon velasquez hardy higgins
aguirre lin cummings chandler sharp barber bowen ochoa dennis robbins liu ramsey francis griffith paul blair oconnor
cardenas pacheco cross calderon quinn moss swanson chan rivas khan rodgers serrano fitzgerald rosales stevenson
christensen manning gill curry mclaughlin harmon mcgee gross doyle garner newton burgess reese walton blake trujillo
adkins brady goodman roman webster goodwin fischer huang potter delacruz montoya todd wu hines mullins castaneda
malone cannon tate mack sherman hubbard hodges zhang guerra wolf valencia saunders franco rowe gallagher farmer
hammond hampton townsend ingram wise gallegos clarke barton schroeder maxwell waters logan camacho strickland
norman person colon parsons frank harrington glover osborne buchanan casey floyd patton ibarra ball tyler suarez
bowers orozco salas cobb gibbs andrade bauer conner moody escobar mcguire lloyd mueller hartman french kramer
mcbride pope lindsey velazquez norton mccormick sparks flynn yates hogan marsh macias villanueva zamora pratt
stokes owen ballard lang brock villarreal charles drake barrera cain patrick pineda burnett mercado santana shepherd
bautista ali shaffer lamb trevino mckenzie hess beil olsen cochran morton nash wilkins petersen briggs shah roth
nicholson holloway lozano rangel flowers hoover short arias mora valenzuela bryan meyers weiss underwood bass
greer summers houston carson morrow clayton whitaker decker yoder collier zuniga carey wilcox melendez poole
roberson larsen conley davenport copeland massey lam huff rocha cameron jefferson hood monroe anthony pittman
huynh randall singleton kirk combs mathis christian skinner bradford richard galvan wall boone kirby wilkinson
bridges bruce atkinson velez meza roy vincent york hodge villa abbott allison tapia gates chase sosa sweeney
farrell wyatt dalton horn barron phelps yu dickerson heath foley atkins mathews bonilla acevedo benitez zavala
hensley glenn cisneros harrell shields rubio huffman choi boyer garrison arroyo bond kane hancock callahan dillon
cline wiggins grimes arellano melton oneill savage ho beltran pitts parrish ponce rich booth koch golden ware
brennan mcdowell marks cantu humphrey baxter sawyer clay tanner hutchinson kaur berg wiley gilmore russo villegas
hobbs keith wilkerson ahmed beard mcclain montes mata rosario vang walter henson oneal mosley mcclure beasley
stephenson snow huerta preston vance barry johns eaton blackwell dyer prince macdonald solomon guevara stafford
english hurst woodard cortes shannon kemp nolan mccullough merritt murillo moon salgado strong kline cordova
barajas roach rosas winters jacobson lester knox bullock kerr leach meadows orr davila whitehead pruitt kent
conway mckee barr david dejesus marin berger mcintyre blankenship gaines palacios cuevas bartlett durham dorsey
mccall odonnell stein browning stout lowery sloan mclean hendricks calhoun sexton chung gentry hull duarte ellison
nielsen gillespie buck middleton sellers leblanc esparza hardin bradshaw mcintosh howe livingston frost glass
morse knapp herman stark bravo noble spears weeks corona frederick buckley mcfarland hebert enriquez hickman
quintero randolph schaefer walls trejo house reilly pennington michael conrad giles benjamin crosby fitzpatrick
donovan mays mahoney valentine raymond medrano hahn mcmillan small bentley felix peck lucero boyle hanna pace
rush hurley harding mcconnell bernal nava ayers everett ventura avery pugh mayer bender shepard mcmahon landry
case sampson moses magana blackburn dunlap gould duffy vaughan herring mckay espinosa rivers farley bernard ashley
friedman potts truong costa correa blevins nixon clements fry delarosa best benton lugo portillo dougherty
crane haley phan villalobos blanchard horne finley quintana lynn esquivel bean dodson mullen xiong hayden cano
levy huber richmond moyer lim frye sheppard mccarty avalos booker waller parra woodward jaramillo krueger rasmussen
brandt peralta donaldson stuart faulkner maynard galindo coffey estes sanford burch maddox vo oconnell vu andersen
spence mcpherson church schmitt stanton leal cherry compton dudley sierra pollard alfaro hester proctor lu hinton
novak good madden mccann terrell jarvis dickson reyna cantrell mayo branch hendrix rollins rowland whitney duke
odom daugherty travis tang archer""".split())


@dataclass
class Ctx:
    awards: list[dict]
    personnel: list[dict] = field(default_factory=list)
    officials: list[dict] = field(default_factory=list)
    contributions: list[dict] = field(default_factory=list)
    oversight_docs: list[dict] = field(default_factory=list)  # {document_id, title, text, url, entity_key}
    config: dict = field(default_factory=dict)  # deployment.integrity
    docs: dict = field(default_factory=dict)  # document_id -> {title, url}

    def cfg(self, *path, default=None):
        node: Any = self.config
        for p in path:
            if not isinstance(node, dict) or p not in node:
                return default
            node = node[p]
        return node

    def threshold(self, award: dict) -> float:
        bt = dict(self.cfg("bid_threshold", default={}) or {})
        # Thresholds are re-indexed periodically; use the one in force on the award date.
        when = award.get("meeting_date")
        for step in sorted(bt.get("history") or [], key=lambda h: str(h["from"])):
            if when and str(when) >= str(step["from"]):
                bt.update({k: v for k, v in step.items() if k != "from"})
        if _PUBLIC_WORKS.search(f"{award.get('title') or ''} {award.get('quote') or ''}"):
            return float(bt.get("public_works") or bt.get("purchase") or 35000)
        return float(bt.get("purchase") or 35000)

    def cite(self, key: str) -> str:
        c = self.cfg(key, "citation", default="")
        return f" ({c})" if c else ""


@dataclass
class Flag:
    rule: str
    severity: str
    title: str
    explanation: str
    subject: str | None
    entity_key: str | None
    amount: float | None
    evidence: list[dict]
    score: float = 0.0
    scope: str = ""  # extra fingerprint discriminator

    @property
    def fingerprint(self) -> str:
        raw = f"{self.rule}|{self.entity_key}|{(self.subject or '').lower()}|{self.scope}"
        return hashlib.sha1(raw.encode()).hexdigest()


def _money(x: float | None) -> str:
    return f"${x:,.2f}" if x is not None else "an unstated amount"


def _ev(ctx: Ctx, a: dict) -> dict:
    d = ctx.docs.get(str(a.get("document_id")), {})
    return {"document_id": str(a.get("document_id")), "title": d.get("title"), "url": d.get("url"),
            "page": a.get("page"), "date": str(a.get("meeting_date") or ""), "quote": a.get("quote"),
            "amount": a.get("amount"), "item": a.get("item_no")}


def _vendor_label(group: list[dict]) -> str:
    names = [a.get("vendor_name") for a in group if a.get("vendor_name")]
    return max(set(names), key=names.count) if names else "Unknown vendor"


def _by_vendor(awards: list[dict], pred: Callable[[dict], bool]) -> dict[tuple, list[dict]]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for a in awards:
        if a.get("vendor_norm") and pred(a):
            groups[(a.get("entity_key"), a["vendor_norm"])].append(a)
    for g in groups.values():
        g.sort(key=lambda a: a.get("meeting_date") or date.min)
    return groups


# ── Rules ────────────────────────────────────────────────────────────────────

def near_threshold(ctx: Ctx) -> list[Flag]:
    ratio = float(ctx.cfg("near_threshold_ratio", default=0.85))
    out = []
    for a in ctx.awards:
        if a.get("action_type") not in SPEND or a.get("procurement") in BID_EXEMPT or _professional(a) or not a.get("amount"):
            continue
        # The threshold depends on the date; an unnamed "other" item is rarely a purchase.
        if not a.get("meeting_date") or (a.get("action_type") == "other" and not a.get("vendor_norm")):
            continue
        t = ctx.threshold(a)
        if ratio * t <= a["amount"] < t:
            out.append(Flag(
                "near_threshold", "medium",
                f"{_money(a['amount'])} award just under the {_money(t)} bid threshold — {a.get('vendor_name') or 'vendor not named'}",
                f"Awarded without documented competitive bidding at {a['amount'] / t:.0%} of the bid threshold"
                f"{ctx.cite('bid_threshold')}. Amounts parked just below a threshold can be a way to avoid "
                "advertising. Innocent explanations: a genuine quote, or bidding the record doesn't mention.",
                a.get("vendor_name"), a.get("entity_key"), a["amount"], [_ev(ctx, a)],
                score=a["amount"] / t, scope=f"{a.get('vendor_norm') or a.get('title')}:{a['amount']}",
            ))
    return out


def split_purchases(ctx: Ctx) -> list[Flag]:
    window = int(ctx.cfg("split_window_days", default=90))
    out = []
    groups = _by_vendor(ctx.awards, lambda a: a.get("action_type") in SPEND and a.get("procurement") not in BID_EXEMPT
                        and not _professional(a) and a.get("amount") and a.get("meeting_date"))
    for (entity, vnorm), g in groups.items():
        used = set()
        for i, a in enumerate(g):
            if i in used:
                continue
            t = ctx.threshold(a)
            if a["amount"] >= t:
                continue
            cluster = [j for j in range(i, len(g)) if g[j]["meeting_date"] - a["meeting_date"] <= timedelta(days=window)
                       and g[j]["amount"] < ctx.threshold(g[j])]
            total = sum(g[j]["amount"] for j in cluster)
            if len(cluster) >= 2 and total >= t:
                used.update(cluster)
                items = [g[j] for j in cluster]
                out.append(Flag(
                    "split_purchases", "high",
                    f"{len(items)} sub-threshold awards to {_vendor_label(items)} totalling {_money(total)} within {window} days",
                    f"Each award is under the {_money(t)} bid threshold{ctx.cite('bid_threshold')}, but together they "
                    "exceed it. Splitting one need into several purchases to avoid bidding is a classic procurement "
                    "red flag. Innocent explanations: genuinely separate needs, or an annual contract paid in parts.",
                    _vendor_label(items), entity, total, [_ev(ctx, x) for x in items],
                    score=total / t, scope=str(a["meeting_date"]),
                ))
    return out


def emergency_procurement(ctx: Ctx) -> list[Flag]:
    out = []
    groups = _by_vendor(ctx.awards, lambda a: a.get("procurement") == "emergency")
    unnamed = [a for a in ctx.awards if a.get("procurement") == "emergency" and not a.get("vendor_norm")]
    for (entity, vnorm), g in list(groups.items()) + [((a.get("entity_key"), f"unnamed:{a.get('document_id')}:{a.get('item_no')}"), [a]) for a in unnamed]:
        total = sum(a.get("amount") or 0 for a in g)
        t = ctx.threshold(g[0])
        sev = "high" if len(g) >= 2 or total >= 2 * t else "medium"
        out.append(Flag(
            "emergency_procurement", sev,
            f"{'Repeated e' if len(g) > 1 else 'E'}mergency procurement: {_vendor_label(g)} — {len(g)} award(s), {_money(total)}",
            f"Emergency procurement skips normal bidding{ctx.cite('emergency')}. It is legitimate for genuine "
            "emergencies; repeated or large emergency awards to the same vendor, or 'emergencies' that were "
            "foreseeable, deserve a look at the documentation and timeline.",
            _vendor_label(g), entity, total, [_ev(ctx, a) for a in g], score=SEV_RANK[sev] + total / t / 10,
        ))
    return out


_PROJECT_STOP = set("""a an the of for to and in on at by with from as is be this that improvements improvement repair repairs
emergency temporary contract award awarding awarded authorizing authorize resolution services service engineering
professional borough city town village county municipal project phase road street avenue ave rd st program""".split())


def _project_words(a: dict) -> set[str]:
    words = re.findall(r"[a-z][a-z'’]{2,}", (a.get("title") or "").lower())
    return {w for w in words if w not in _PROJECT_STOP}


def emergency_after_planning(ctx: Ctx) -> list[Flag]:
    """An 'emergency' award for a project the governing body had already been
    planning or engineering weeks earlier — the condition was known in time to
    get quotes or bid."""
    min_gap = int(ctx.cfg("emergency", "planning_gap_days", default=30))
    out = []
    for e in ctx.awards:
        if e.get("procurement") != "emergency" or not e.get("meeting_date"):
            continue
        ew = _project_words(e)
        if len(ew) < 2:
            continue
        prior = [a for a in ctx.awards if a is not e and a.get("meeting_date") and a.get("entity_key") == e.get("entity_key")
                 and timedelta(days=min_gap) <= e["meeting_date"] - a["meeting_date"] <= timedelta(days=730)
                 and len(ew & _project_words(a)) >= 2]
        if not prior:
            continue
        first = min(prior, key=lambda a: a["meeting_date"])
        gap = (e["meeting_date"] - first["meeting_date"]).days
        rng = (e.get("details") or {}).get("date_range") if (e.get("details") or {}).get("date_estimated") else None
        est = (f" at the earliest — the posted resolution is undated, numbered between actions of {rng[0]} and {rng[1]}"
               if rng else "")
        out.append(Flag(
            "emergency_after_planning", "high" if gap >= 45 else "medium",
            f"Emergency award of {_money(e.get('amount'))} to {e.get('vendor_name') or 'unnamed vendor'} "
            f"{'≥' if rng else '~'}{gap} days after the same project was already in the record",
            f"\"{first.get('title')}\" was acted on {first['meeting_date']}; the emergency award came {gap} days later{est}. "
            f"Emergency procurement{ctx.cite('emergency')} is for conditions that can't wait for quotes or bids. A "
            "condition already being studied or designed weeks earlier usually could. Check the emergency certification, "
            "how many quotes were sought, and whether the vendor does this kind of work.",
            e.get("vendor_name"), e.get("entity_key"), e.get("amount"), [_ev(ctx, first)] + [_ev(ctx, a) for a in prior if a is not first][:4] + [_ev(ctx, e)],
            score=4 + gap / 30, scope=f"{e.get('vendor_norm')}:{e.get('amount')}",
        ))
    return out


def noncompetitive_concentration(ctx: Ctx) -> list[Flag]:
    months = int(ctx.cfg("concentration_months", default=24))
    min_count = int(ctx.cfg("concentration_min_awards", default=3))
    out = []
    groups = _by_vendor(ctx.awards, lambda a: a.get("action_type") in SPEND and a.get("procurement") in NONCOMPETITIVE)
    for (entity, vnorm), g in groups.items():
        latest = max((a.get("meeting_date") or date.min) for a in g)
        cutoff = latest - timedelta(days=30 * months) if latest > date.min + timedelta(days=30 * months) else date.min
        recent = [a for a in g if (a.get("meeting_date") or date.min) >= cutoff]
        total = sum(a.get("amount") or 0 for a in recent)
        t = ctx.threshold(recent[0]) if recent else 35000
        if len(recent) >= min_count or total >= 3 * t:
            sev = "high" if total >= 5 * t else "medium"
            title = (f"{_vendor_label(recent)}: {len(recent)} awards without documented competition, {_money(total)} in {months} months"
                     if len(recent) > 1 else
                     f"{_vendor_label(recent)}: {_money(total)} award without documented competition")
            out.append(Flag(
                "noncompetitive_concentration", sev, title,
                "Repeated awards to one vendor through professional-services, no-bid, sole-source or undocumented "
                "procurement concentrate spending outside competition. Professional services are often legally "
                "exempt from bidding, so check whether the vendor was chosen through any RFP/RFQ process and "
                "whether its principals are connected to decision-makers or donors.",
                _vendor_label(recent), entity, total, [_ev(ctx, a) for a in recent],
                score=total / t, scope=f"{months}m",
            ))
    return out


def single_bid(ctx: Ctx) -> list[Flag]:
    out = []
    # Unit-price bids ($225 per tow) aren't contract values; skip tiny amounts.
    floor = float(ctx.cfg("single_bid_min_amount", default=10000))
    for a in ctx.awards:
        if (a.get("procurement") in ("competitive_bid", "rfp") and a.get("bids_received") == 1
                and (a.get("amount") or 0) >= floor):
            out.append(Flag(
                "single_bid", "medium",
                f"Only one bid received — {a.get('vendor_name') or 'vendor not named'}, {_money(a.get('amount'))}",
                "A competitive process that draws a single response may have been narrowly specified, "
                "poorly advertised, or steered. Compare the specs and the advertisement period.",
                a.get("vendor_name"), a.get("entity_key"), a.get("amount"), [_ev(ctx, a)],
                score=2, scope=f"{a.get('vendor_norm') or a.get('title')}:{a.get('amount')}",
            ))
    return out


def duplicate_award(ctx: Ctx) -> list[Flag]:
    out = []
    window = int(ctx.cfg("duplicate_window_days", default=60))
    groups = _by_vendor(ctx.awards, lambda a: a.get("action_type") in SPEND and (a.get("amount") or 0) >= 1000)
    for (entity, vnorm), g in groups.items():
        for i, a in enumerate(g):
            for b in g[i + 1:]:
                if (a["amount"] == b["amount"] and a.get("meeting_date") and b.get("meeting_date")
                        and a["meeting_date"] != b["meeting_date"]
                        and abs((b["meeting_date"] - a["meeting_date"]).days) <= window):
                    out.append(Flag(
                        "duplicate_award", "medium",
                        f"Same amount authorized twice for {_vendor_label([a, b])}: {_money(a['amount'])}",
                        f"Identical vendor and amount in two separate actions within {window} days. Could be a "
                        "duplicate payment, or a re-adopted/corrected resolution. Check the claims/warrant registers.",
                        _vendor_label([a, b]), entity, a["amount"], [_ev(ctx, a), _ev(ctx, b)],
                        score=2, scope=f"{a.get('meeting_date')}:{b.get('meeting_date')}:{a['amount']}",
                    ))
    return out


def payouts(ctx: Ctx) -> list[Flag]:
    review = float(ctx.cfg("payout_review_amount", default=25000))
    out = []
    by_fy: dict[tuple, list[dict]] = defaultdict(list)
    for a in ctx.awards:
        if a.get("action_type") != "payout" or not a.get("amount"):
            continue
        by_fy[(a.get("entity_key"), a.get("fiscal_year") or "unknown")].append(a)
        if a["amount"] >= review:
            out.append(Flag(
                "payout", "medium",
                f"{_money(a['amount'])} separation/termination payout funding",
                "Large separation, terminal-leave or accrued-time payouts are contractual in many places but "
                "have repeatedly been the vehicle for improper 'golden parachutes'. Check who is being paid, "
                "the contract clause, and whether the amount matches accrued balances.",
                None, a.get("entity_key"), a["amount"], [_ev(ctx, a)],
                score=a["amount"] / review, scope=f"{a.get('document_id')}:{a.get('item_no')}",
            ))
    for (entity, fy), g in by_fy.items():
        total = sum(a["amount"] for a in g)
        if total >= 4 * review and len(g) > 1:
            out.append(Flag(
                "payout_total", "high" if total >= 10 * review else "medium",
                f"Fiscal year {fy}: {_money(total)} moved to fund payouts across {len(g)} actions",
                "Aggregate payout funding for the year. Compare with prior years and with headcount changes.",
                f"FY {fy}", entity, total, [_ev(ctx, a) for a in g], score=total / review, scope=fy,
            ))
    return out


def change_order_growth(ctx: Ctx) -> list[Flag]:
    """Cumulative change orders vs. the original award. NJ change-order resolutions
    state the original amount themselves (details.base_amount); prefer that over
    guessing the base from the vendor's largest award."""
    cap = float(ctx.cfg("change_order", "cap_pct", default=20))
    out = []
    groups = _by_vendor(ctx.awards, lambda a: a.get("action_type") in ("contract", "purchase", "change_order"))
    for (entity, vnorm), g in groups.items():
        cos = [a for a in g if a["action_type"] == "change_order"]
        if not cos:
            continue
        stated = [(a.get("details") or {}).get("base_amount") for a in cos]
        stated = [b for b in stated if b]
        base = min(stated) if stated else max(
            (a.get("amount") or 0 for a in g if a["action_type"] != "change_order"), default=0)
        added = sum(a.get("amount") or 0 for a in cos)
        if base and added / base * 100 > cap:
            pct = added / base * 100
            out.append(Flag(
                "change_order_growth", "high" if pct > 2 * cap else "medium",
                f"{_vendor_label(g)}: change orders add {pct:.0f}% to a {_money(base)} contract",
                f"Change orders above {cap:.0f}% of the original award{ctx.cite('change_order')} can mean a "
                "low-ball bid recovered after award. Check whether the added work was foreseeable at bid time.",
                _vendor_label(g), entity, added, [_ev(ctx, a) for a in g], score=pct / cap,
            ))
    return out


def nepotism_surname(ctx: Ctx) -> list[Flag]:
    officials: dict[tuple, dict] = {}
    for o in ctx.officials:
        ln = (o.get("last_name") or "").lower()
        if ln and ln not in COMMON_SURNAMES:
            officials[(o.get("entity_key"), ln)] = o
    counts = defaultdict(int)
    for p in ctx.personnel:
        counts[(p.get("last_name") or "").lower()] += 1
    out = []
    for p in ctx.personnel:
        ln = (p.get("last_name") or "").lower()
        o = officials.get((p.get("entity_key"), ln))
        if not o or counts[ln] > 3:
            continue
        out.append(Flag(
            "official_surname_hire", "low",
            f"Personnel action for {p.get('first_name')} {p.get('last_name')} ({p.get('position') or p.get('department')}) "
            f"shares a surname with {o.get('role') or 'official'} {o.get('name')}",
            "A hire or promotion sharing an uncommon surname with a sitting official. Usually coincidence; "
            "worth confirming no family relationship (most ethics codes require recusal and disclosure).",
            f"{p.get('first_name')} {p.get('last_name')}", p.get("entity_key"), None,
            [{"document_id": str(p.get("document_id")), "title": ctx.docs.get(str(p.get("document_id")), {}).get("title"),
              "page": p.get("page"), "quote": p.get("quote")}],
            score=1, scope=f"{p.get('document_id')}",
        ))
    return out


def _tokens(s: str) -> set[str]:
    return {t for t in (s or "").split() if len(t) > 2}


def pay_to_play(ctx: Ctx) -> list[Flag]:
    if not ctx.contributions:
        return []
    p2p = ctx.cfg("pay_to_play", default={}) or {}
    window = int(p2p.get("window_days", 365))
    min_contrib = float(p2p.get("contribution_threshold", 0))
    min_contract = float(p2p.get("contract_threshold", 0))
    statutory = bool(p2p.get("statutory"))
    by_name: dict[str, list[dict]] = defaultdict(list)
    for c in ctx.contributions:
        for key in (c.get("contributor_norm"), c.get("employer_norm")):
            if key:
                by_name[key].append(c)
    out = []
    groups = _by_vendor(ctx.awards, lambda a: a.get("action_type") in SPEND and (a.get("amount") or 0) >= min_contract)
    for (entity, vnorm), g in groups.items():
        matches = list(by_name.get(vnorm, []))
        if not matches:  # token containment: "d and b engineers and architects" vs "d and b engineers"
            vt = _tokens(vnorm)
            for name, cs in by_name.items():
                nt = _tokens(name)
                if len(nt) >= 2 and nt <= vt or len(vt) >= 2 and vt <= nt:
                    matches += cs
        for a in g:
            if not a.get("meeting_date"):
                continue
            prior = [c for c in matches if c.get("date") and timedelta(0) <= a["meeting_date"] - c["date"] <= timedelta(days=window)]
            total = sum(c.get("amount") or 0 for c in prior)
            if prior and total > min_contrib:
                out.append(Flag(
                    "pay_to_play", "high" if statutory else "medium",
                    f"{_vendor_label(g)} (or its people) gave {_money(total)} within {window} days before a {_money(a.get('amount'))} award",
                    ("Contributions above the statutory limit within a year before a non-fair-and-open contract can "
                     "disqualify the vendor" if statutory else "Contributions from a vendor or its principals shortly "
                     "before an award") + f"{ctx.cite('pay_to_play')}. Verify the contributor is the same person/entity, "
                    "the recipients' roles, and whether a fair-and-open process was used.",
                    _vendor_label(g), entity, a.get("amount"),
                    [_ev(ctx, a)] + [{"title": f"{c.get('source')}: {c.get('contributor')} → {c.get('recipient')}",
                                      "date": str(c.get("date")), "amount": c.get("amount"), "quote": c.get("employer") or ""}
                                     for c in prior[:10]],
                    score=3 + total / 1000, scope=f"{a.get('document_id')}:{a.get('item_no')}",
                ))
    return out


_BOILERPLATE = re.compile(r"likelihood|risk of|could be subject|prevent|detect(?:ing|ion)?\s+(?:of\s+)?(?:and\s+)?|"
                          r"reduce|safeguard|designed to|reasonable assurance|categories|classif", re.I)
_OVERSIGHT_PATTERNS = [
    (r"(?:is|was|remains?|be|been|were|in)\s+(?:in\s+)?significant\s+fiscal\s+stress|significant\s+fiscal\s+stress\s+(?:designation|score)", "high"),
    (r"(?:identified|found|detected|resulted\s+in|evidence\s+of|instances?\s+of|alleged)\s+(?:\w+\s+){0,3}(?:fraud|theft|misappropriat\w*)", "high"),
    (r"improper(?:ly)?\s+(?:paid|payments?|used)", "high"), (r"did not (?:comply|follow|obtain|seek|competitively)", "medium"),
    (r"without (?:competitive|board|council) (?:bidding|approval)", "medium"), (r"unsupported", "medium"),
    (r"overpa(?:id|yments?)", "medium"), (r"moderate fiscal stress", "medium"), (r"operating deficits?", "medium"),
    (r"repeat(?:ed)? finding|prior[- ]year findings? (?:have|has) not been|not (?:been )?(?:fully )?implemented", "medium"),
    (r"material weakness", "medium"), (r"(?:was|were) not (?:adequately )?(?:monitored|reconciled|supported|approved)", "medium"),
]


def oversight_findings(ctx: Ctx) -> list[Flag]:
    out = []
    for d in ctx.oversight_docs:
        text = d.get("text") or ""
        hits, sev = [], "low"
        for pat, s in _OVERSIGHT_PATTERNS:
            m = next((m for m in re.finditer(pat, text, re.I)
                      if not _BOILERPLATE.search(text[max(0, m.start() - 80):m.end() + 40])), None)
            if m:
                quote = " ".join(text[max(0, m.start() - 160):m.end() + 160].split())
                page = None
                for pm in re.finditer(r"^## Page (\d+)\s*$", text[:m.start()], re.M):
                    page = int(pm.group(1))
                hits.append({"document_id": str(d["document_id"]), "title": d.get("title"), "url": d.get("url"),
                             "page": page, "quote": quote, "match": m.group(0)})
                if SEV_RANK[s] > SEV_RANK[sev]:
                    sev = s
        if hits:
            out.append(Flag(
                "oversight_findings", sev,
                f"Oversight report findings: {d.get('title') or 'audit'} ({', '.join(h['match'].lower() for h in hits[:4])})",
                "An auditor or state oversight body reported adverse findings. Track whether management's "
                "corrective action plan was implemented — repeat findings in later audits are the signal.",
                d.get("title"), d.get("entity_key"), None, hits[:6], score=SEV_RANK[sev] + len(hits) / 10,
            ))
    return out


def payments_over_threshold(ctx: Ctx) -> list[Flag]:
    """Bill-list payments to one vendor in a fiscal year exceed the bid threshold,
    but no competitively procured award to that vendor is on record."""
    competitive = {(a.get("entity_key"), a.get("vendor_norm")) for a in ctx.awards
                   if a.get("procurement") in COMPETITIVE and a.get("vendor_norm")}
    by_year: dict[tuple, list[dict]] = defaultdict(list)
    for a in ctx.awards:
        if a.get("action_type") == "payment" and a.get("vendor_norm") and a.get("amount"):
            by_year[(a.get("entity_key"), a["vendor_norm"], a.get("fiscal_year") or "unknown")].append(a)
    out = []
    for (entity, vnorm, fy), g in by_year.items():
        total = sum(a["amount"] for a in g)
        t = float((ctx.cfg("bid_threshold", default={}) or {}).get("purchase") or 35000)
        if total >= t and (entity, vnorm) not in competitive:
            out.append(Flag(
                "payments_over_threshold", "high" if total >= 2 * t else "medium",
                f"{_vendor_label(g)}: {_money(total)} paid in FY {fy} with no competitive award on record",
                f"Purchases from one vendor are aggregated over the contract year for bidding purposes"
                f"{ctx.cite('bid_threshold')}. Payments above the threshold need a bid, a state/cooperative contract, "
                "or a documented exception. Innocent explanations: a contract awarded before the records indexed here, "
                "a statutory exemption (utilities, other governments, insurance), or pass-through payments.",
                _vendor_label(g), entity, total, [_ev(ctx, a) for a in g[:10]], score=total / t, scope=fy,
            ))
    return out


# ── NJ-specific (enabled by the state's integrity config) ────────────────────

def _master_for(ctx: Ctx, a: dict) -> dict | None:
    """The vendor's term appointment/NFO contract with a disclosure on file that
    covers this award's date (project awards are often task orders under it)."""
    when = a.get("meeting_date")
    if not when or not a.get("vendor_norm"):
        return None
    for m in ctx.awards:
        d = m.get("details") or {}
        if (m is a or m.get("vendor_norm") != a["vendor_norm"] or m.get("entity_key") != a.get("entity_key")
                or not d.get("p2p_disclosure") or not m.get("meeting_date")):
            continue
        end = date.fromisoformat(d["term_end"]) if d.get("term_end") else date(m["meeting_date"].year, 12, 31)
        if m["meeting_date"] <= when <= end:
            return m
    return None


def nfo_missing_disclosure(ctx: Ctx) -> list[Flag]:
    """A non-fair-and-open contract over the pay-to-play threshold with no
    Business Entity Disclosure / 19:44A-20.5 language in the resolution."""
    if not ctx.cfg("pay_to_play", "statutory", default=False):
        return []
    limit = float(ctx.cfg("pay_to_play", "contract_threshold", default=17500))
    out = []
    for a in ctx.awards:
        d = a.get("details") or {}
        if (d.get("p2p_disclosure") is False and a.get("action_type") in SPEND
                and d.get("non_fair_open") and (a.get("amount") or 0) > limit and not _master_for(ctx, a)):
            out.append(Flag(
                "nfo_missing_disclosure", "medium",
                f"Non-fair-and-open award to {a.get('vendor_name') or 'unnamed vendor'}, {_money(a.get('amount'))}, "
                "with no pay-to-play disclosure cited",
                f"Contracts over {_money(limit)} awarded outside a fair-and-open process require the vendor's "
                f"Business Entity Disclosure Certification and a political-contribution certification{ctx.cite('pay_to_play')}. "
                "The resolution text doesn't reference them. The forms may be on file anyway; request them by OPRA.",
                a.get("vendor_name"), a.get("entity_key"), a.get("amount"), [_ev(ctx, a)],
                score=(a.get("amount") or 0) / limit, scope=f"{d.get('resolution_no')}:{a.get('amount')}",
            ))
    return out


def p2p_threshold_split(ctx: Ctx) -> list[Flag]:
    """Non-fair-and-open awards to one vendor, each at or under the pay-to-play
    contract threshold, that together exceed it within a year. Keeping every piece
    under $17,500 keeps the vendor out of the contribution ban."""
    if not ctx.cfg("pay_to_play", "statutory", default=False):
        return []
    limit = float(ctx.cfg("pay_to_play", "contract_threshold", default=17500))
    groups = _by_vendor(ctx.awards, lambda a: a.get("action_type") in SPEND and a.get("amount") and a.get("meeting_date")
                        and (a.get("details") or {}).get("non_fair_open"))
    out = []
    for (entity, vnorm), g in groups.items():
        used: set[int] = set()
        for i, a in enumerate(g):
            if i in used or a["amount"] > limit:
                continue
            cluster = [j for j in range(i, len(g)) if j not in used and g[j]["amount"] <= limit
                       and g[j]["meeting_date"] - a["meeting_date"] <= timedelta(days=365)]
            total = sum(g[j]["amount"] for j in cluster)
            if len(cluster) < 2 or total <= limit:
                continue
            used.update(cluster)
            items = [g[j] for j in cluster]
            same_night = len({x["meeting_date"] for x in items}) < len(items)
            undisclosed = [x for x in items if (x.get("details") or {}).get("p2p_disclosure") is False
                           and not _master_for(ctx, x)]
            out.append(Flag(
                "p2p_threshold_split", ("high" if same_night else "medium") if undisclosed else "low",
                f"{len(items)} non-fair-and-open awards to {_vendor_label(items)}, each ≤ {_money(limit)}, totalling {_money(total)}"
                + (" — some on the same night" if same_night else ""),
                f"Contracts over {_money(limit)} awarded without a fair-and-open process trigger the pay-to-play "
                f"contribution ban and disclosure{ctx.cite('pay_to_play')}. Several pieces under the line for the same "
                "vendor and the same kind of work can be a way around it. Get the vendor's ELEC filings and the "
                "certifications on file, and check whether the work was one engagement."
                + ("" if undisclosed else " A term contract with a disclosure on file covers these dates, which lowers the "
                   "concern; the aggregate still counts toward the vendor's annual ELEC disclosure."),
                _vendor_label(items), entity, total, [_ev(ctx, x) for x in items],
                score=total / limit + same_night, scope=str(a["meeting_date"]),
            ))
    return out


def successor_firm_nfo(ctx: Ctx) -> list[Flag]:
    """A professional appointed through a competitive/fair-and-open process changes
    firms mid-term, and the new firm gets a non-fair-and-open contract to keep them.
    The competition was for the old firm's proposal; the new firm never competed."""
    def last(n):
        return (n or "").split()[-1].lower() if n else ""
    appts = [a for a in ctx.awards if (a.get("details") or {}).get("principal") and a.get("meeting_date")]
    out = []
    for b in appts:
        db = b.get("details") or {}
        if not db.get("non_fair_open"):
            continue
        for a in appts:
            da = a.get("details") or {}
            if (a is b or last(da["principal"]) != last(db["principal"]) or a.get("vendor_norm") == b.get("vendor_norm")
                    or a["meeting_date"] >= b["meeting_date"] or a.get("entity_key") != b.get("entity_key")):
                continue
            later = [x for x in ctx.awards if x.get("vendor_norm") == b.get("vendor_norm") and x is not b
                     and x.get("meeting_date") and x["meeting_date"] > b["meeting_date"]]
            added = sum(x.get("amount") or 0 for x in later)
            out.append(Flag(
                "successor_firm_nfo", "medium",
                f"{db['principal']} moved from {a.get('vendor_name') or 'prior firm'} to {b.get('vendor_name')}; "
                f"new firm got a non-fair-and-open contract" + (f", then {_money(added)} more in {len(later)} award(s)" if later else ""),
                f"{a.get('vendor_name') or 'The prior firm'} won the appointment"
                f"{' through a fair-and-open process' if da.get('fair_open') else ''} on {a['meeting_date']}. When "
                f"{db['principal']} changed firms the governing body awarded {b.get('vendor_name')} a non-fair-and-open "
                f"contract{' through ' + db['term_end'] if db.get('term_end') else ''}, so the new firm never competed. "
                "Usually done for continuity and often legal, but it removes the competitive check for the rest of the term. "
                "Check the vote, the new firm's ELEC history and whether the work volume grew after the switch.",
                b.get("vendor_name"), b.get("entity_key"), added or None,
                [_ev(ctx, a), _ev(ctx, b)] + [_ev(ctx, x) for x in later[:8]],
                score=3 + added / 50000, scope=f"{a.get('vendor_norm')}>{b.get('vendor_norm')}",
            ))
            break
    return out


def elec_annual_disclosure(ctx: Ctx) -> list[Flag]:
    """Vendors receiving more than the annual threshold in public contracts must
    file an annual Business Entity Disclosure with ELEC — a check anyone can run."""
    if not ctx.cfg("pay_to_play", "statutory", default=False):
        return []
    limit = float(ctx.cfg("pay_to_play", "annual_disclosure_threshold", default=50000))
    by_year: dict[tuple, list[dict]] = defaultdict(list)
    for a in ctx.awards:
        if a.get("action_type") in SPEND and a.get("vendor_norm") and a.get("meeting_date") and a.get("amount"):
            by_year[(a.get("entity_key"), a["vendor_norm"], a["meeting_date"].year)].append(a)
    out = []
    for (entity, vnorm, yr), g in by_year.items():
        total = sum(a["amount"] for a in g)
        nfo = [a for a in g if (a.get("details") or {}).get("non_fair_open")]
        if total > limit and nfo:
            out.append(Flag(
                "elec_annual_disclosure", "low",
                f"{_vendor_label(g)}: {_money(total)} in {yr} awards ({len(nfo)} non-fair-and-open) — confirm ELEC annual disclosure",
                f"A business entity receiving more than {_money(limit)} in public contracts in a year must file an annual "
                "disclosure of its political contributions with ELEC (N.J.S.A. 19:44A-20.27, Form B-1). Search ELEC's "
                "business-entity filings for this vendor and year, and compare its contributions with the officials who voted.",
                _vendor_label(g), entity, total, [_ev(ctx, a) for a in g[:10]], score=total / limit, scope=str(yr),
            ))
    return out


def professional_tenure(ctx: Ctx) -> list[Flag]:
    """The same professional reappointed without competition year after year at
    reorganization. Legal, but entrenched no-bid relationships are where
    pay-to-play lives — pair with contribution data."""
    min_years = int(ctx.cfg("tenure_min_years", default=4))
    def professional(a: dict) -> bool:
        d = a.get("details") or {}
        return bool(a.get("meeting_date")) and a.get("action_type") in ("contract", "purchase") and (
            a.get("procurement") in ("professional_services", "no_bid")
            or d.get("appointment") or d.get("fair_open") or d.get("non_fair_open"))
    groups = _by_vendor(ctx.awards, professional)
    out = []
    for (entity, vnorm), g in groups.items():
        years = sorted({a["meeting_date"].year for a in g})
        if len(years) < min_years:
            continue
        nfo = [a for a in g if (a.get("details") or {}).get("non_fair_open")]
        total = sum(a.get("amount") or 0 for a in g)
        out.append(Flag(
            "professional_tenure", "medium" if nfo else "low",
            f"{_vendor_label(g)}: appointed {len(years)} years running ({years[0]}–{years[-1]})"
            + (f", {len(nfo)}× non-fair-and-open" if nfo else ""),
            "Professional appointments are exempt from bidding. A 'fair and open' RFQ whose criteria the same firm "
            "wins every January, or a run of non-fair-and-open awards, means the price has never really been "
            "market-tested. Compare the RFQ responses and cross-reference ELEC contributions from the firm and its principals.",
            _vendor_label(g), entity, total or None, [_ev(ctx, a) for a in g[-8:]],
            score=len(years) + len(nfo) / 2, scope="tenure",
        ))
    return out


def bill_list_spike(ctx: Ctx) -> list[Flag]:
    """A Payment-of-Bills resolution far above the entity's median — money moved
    in one vote with the detail usually in an unpublished attachment."""
    mult = float(ctx.cfg("bill_spike_multiple", default=3))
    bills = [a for a in ctx.awards if a.get("action_type") == "bill_payment" and a.get("amount")]
    out = []
    by_entity: dict = defaultdict(list)
    for a in bills:
        by_entity[a.get("entity_key")].append(a)
    for entity, g in by_entity.items():
        if len(g) < 8:
            continue
        amts = sorted(a["amount"] for a in g)
        med = amts[len(amts) // 2]
        for a in g:
            if a["amount"] >= mult * med:
                funds = (a.get("details") or {}).get("funds") or {}
                top = max(funds.items(), key=lambda kv: kv[1] or 0)[0] if funds else None
                out.append(Flag(
                    "bill_list_spike", "low",
                    f"Bill list of {_money(a['amount'])} on {a.get('meeting_date')} — {a['amount'] / med:.1f}× the median",
                    f"The median bill list here is {_money(med)}. Large lists are often debt service, school or county "
                    "tax remittances, or capital draws" + (f" (largest fund: {top})" if top else "") + ". Pull the "
                    "itemized bill list — it's rarely posted with the resolution — and check the big lines.",
                    None, entity, a["amount"], [_ev(ctx, a)], score=a["amount"] / med,
                    scope=f"{a.get('meeting_date')}:{a['amount']}",
                ))
    return out


RULES: dict[str, Callable[[Ctx], list[Flag]]] = {
    "near_threshold": near_threshold,
    "split_purchases": split_purchases,
    "emergency_procurement": emergency_procurement,
    "emergency_after_planning": emergency_after_planning,
    "noncompetitive_concentration": noncompetitive_concentration,
    "single_bid": single_bid,
    "duplicate_award": duplicate_award,
    "payouts": payouts,
    "change_order_growth": change_order_growth,
    "official_surname_hire": nepotism_surname,
    "pay_to_play": pay_to_play,
    "oversight_findings": oversight_findings,
    "payments_over_threshold": payments_over_threshold,
    "nfo_missing_disclosure": nfo_missing_disclosure,
    "p2p_threshold_split": p2p_threshold_split,
    "successor_firm_nfo": successor_firm_nfo,
    "elec_annual_disclosure": elec_annual_disclosure,
    "professional_tenure": professional_tenure,
    "bill_list_spike": bill_list_spike,
}


def run_rules(ctx: Ctx, only: list[str] | None = None) -> list[Flag]:
    disabled = set(ctx.cfg("disabled_rules", default=[]) or [])
    flags: list[Flag] = []
    for name, fn in RULES.items():
        if (only and name not in only) or name in disabled:
            continue
        flags += fn(ctx)
    seen, unique = set(), []
    for f in sorted(flags, key=lambda f: (-SEV_RANK[f.severity], -f.score)):
        if f.fingerprint not in seen:
            seen.add(f.fingerprint)
            unique.append(f)
    return unique
