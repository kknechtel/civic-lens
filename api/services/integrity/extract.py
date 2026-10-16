"""
Pull integrity facts out of a document's text — deterministically, no LLM.

  awards     resolutions that spend money: vendor, amount, procurement method,
             bids received, account code (WHEREAS… RESOLVED… blocks)
  personnel  hires / promotions / separations from personnel reports
  officials  roll call ("Present: Council President Finn and Council Members …")

The parsers are tuned on Long Beach NY council packets and NJ borough
resolutions but only rely on conventions common to US municipal records.
Everything extracted carries the page and a short quote as evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable

_PAGE_RE = re.compile(r"^## Page (\d+)\s*$", re.M)
_MONEY_RE = re.compile(r"\$\s?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?)(?!\d)")
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_LONG_DATE_RE = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}}),\s*(20\d{{2}}|19\d{{2}})\b", re.I)
_NUM_DATE_RE = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2}|\d{2})\b")

# Item boundaries inside agenda packets / minutes.
_ITEM_RE = re.compile(
    r"(?im)^[ \t]*(?:item\s+no\.?\s*(?P<a>\d+[a-z]?)\b|resolution\s+(?:no\.?|number|#)\s*(?P<b>[\w\-/]*\d[\w\-/]*)?"
    # NJ header line: "RESOLUTION 039-2026" / "RESOLUTION NO. 2026-039" on its own line
    r"|resolution\s+(?:no\.?\s*)?(?P<c>\d{1,4}\s*[-–]\s*\d{2,4})[ \t]*$)"
)


def normalize(text: str) -> str:
    """PDF text quirks that silently defeat regexes: soft hyphens (NON\u00adFAIR),
    non-breaking spaces, ligature dashes."""
    return (text or "").replace("\u00ad", "-").replace("\u00a0", " ").replace("\u2011", "-")


# Clerk's certification: "…at its Reorganization Meeting held January 1, 2026."
_CERT_RE = re.compile(rf"at\s+its\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+Meeting\s+held\s+(({_MONTHS})\s+\d{{1,2}},\s*\d{{4}})", re.I)
_TITLE_WORDS = r"(?:Council(?:man|woman|member|\s+President)?|Mayor|Deputy\s+Mayor|Commissioner|Trustee|Member)"
_VOTE_FIELDS = {"yes": r"Yes|Ayes?", "no": r"No|Nays?", "abstain": r"Abstain(?:ed)?", "absent": r"Absent"}
_P2P_DISCLOSURE_RE = re.compile(r"19:44A-20\.(?:5|8|26|27)|Business\s+Entity\s+Disclosure|Political\s+Contribution\s+Disclosure", re.I)
_DOCKET_RE = re.compile(r"Docket\s+No\.?\s*([A-Z]{3}-[A-Z]-\d{2,6}-\d{2})")
_CO_FROM_BY_RE = re.compile(r"from\s+\$\s?([\d,]+(?:\.\d{2})?)\s+by\s+\$\s?([\d,]+(?:\.\d{2})?)", re.I)
_CO_TOTAL_RE = re.compile(r"(?:new|revised|adjusted|final)\s+(?:project\s+|contract\s+)?total\s+of\s+\$\s?([\d,]+(?:\.\d{2})?)", re.I)
_CO_BASE_RE = re.compile(r"(?:in\s+the\s+amount\s+of|original\s+contract\s+(?:amount|price)\s+of)\s+\$\s?([\d,]+(?:\.\d{2})?)", re.I)
_FUND_TOTAL_RE = re.compile(r"Total\s+to\s+be\s+paid\s+from\s+Fund\s+(\d+)\s+([A-Z][A-Z /&–\-]+?)\s+\$?\s*([\d,]+\.\d{2})", re.I)
# NJ titles are often ALL CAPS: "…NON-FAIR AND OPEN WITH THE AUBREY GROUP FOR GRANT…", "…FOR FIORE PAVING FOR THE…"
_TITLE_VENDOR_RES = [re.compile(r"\bWITH\s+(?P<v>[A-Z][A-Z0-9&.'’\- ]{2,60}?)\s+FOR\b"),
                     re.compile(r"\bFOR\s+(?P<v>[A-Z][A-Z0-9&.'’\- ]{2,40}?)\s+FOR\s+THE\b"),
                     re.compile(r"\bTO\s+(?P<v>[A-Z][A-Z0-9&.'’\- ]{2,60}?)\s+FOR\b")]
_NJ_VENDOR_RE = re.compile(
    r"(?:designated\s+as|(?:execute\s+an?\s+)?agreement\s+with|contract\s+to|awarded\s+to)\s+"
    r"(?P<v>[A-Z][\w&.'’\- ]{2,70}?)(?=,\s*(?:\d|Esq|P\.?\s?O\.)|\s+for\b|\s+as\b|\s+in\s+the\s+amount|\s+at\s+a\b|\.\s)")


def parse_votes(seg: str) -> dict:
    """'Moved by Councilman Crowley; Seconded by Councilman Dougherty … Yes: … No: None Abstain: … Absent: …'"""
    out: dict = {}
    mv = re.search(rf"Moved\s+by\s+{_TITLE_WORDS}?\s*([A-Z][\w'’\-]+)", seg)
    sc = re.search(rf"Seconded\s+by\s+{_TITLE_WORDS}?\s*([A-Z][\w'’\-]+)", seg)
    if mv:
        out["moved"] = mv.group(1)
    if sc:
        out["seconded"] = sc.group(1)
    labels = "|".join(_VOTE_FIELDS.values())
    for key, lab in _VOTE_FIELDS.items():
        m = re.search(rf"\b(?:{lab})\s*:\s*(.*?)(?=\b(?:{labels})\s*:|\bI,\s|$)", seg)
        if not m:
            continue
        names = [re.sub(rf"^{_TITLE_WORDS}\s+", "", n.strip(" .")).strip()
                 for n in re.split(r",|\band\b", m.group(1))]
        names = [n for n in names if re.fullmatch(r"[A-Z][\w'’\-]+(?:\s[A-Z][\w'’\-]+)?", n) and n.lower() != "none"]
        out[key] = names
    return out if ("yes" in out or "moved" in out) else {}

_RESOLVED_RE = re.compile(r"\b(?:be it\s+)?resolved\b", re.I)

_ENTITY_SUFFIX = (r"(?:Inc\.?|Incorporated|L\.?L\.?C\.?|L\.?L\.?P\.?|Corp\.?|Corporation|Company|Co\.|P\.?C\.?|"
                  r"P\.?A\.?|Ltd\.?|Associates|Group|Solutions|Services|Engineers?|Engineering|Construction|"
                  r"Contracting|Contractors?|Consultants?|Consulting|Partners|Enterprises|Agency|Architects?|"
                  r"Esq\.?|Paving|Electric|Industries|Plumbing|Landscaping|Excavating|Law\s+Group|Law\s+Firm)")
_VENDOR_RE = re.compile(
    rf"(?:\bwith|\bto|\bfrom|\bretain|\bengage|\baward(?:ed)?\s+to|\bfirm\s+of)\s+(?:the\s+(?:firm|company)\s+of\s+)?"
    rf"(?P<v>[A-Z0-9][\w&.,'’\-/ ]{{1,90}}?\b{_ENTITY_SUFFIX})(?=[\s,.;:)]|$)"
)
# "… from ICC Community Development Solutions, P.O. Box 772511, Detroit MI 48277" / "… X, 123 Main St, Town, NY 11701"
_VENDOR_ADDR_RE = re.compile(
    r"(?:\bwith|\bto|\bfrom|\bretain|\bpay|\bWHEREAS,)\s+(?:the\s+firm\s+of\s+)?"
    r"(?P<v>[A-Z][\w&.'’\- ]{2,80}?(?:,\s*(?:Inc|LLC|L\.L\.C|LLP|Corp|P\.C|Ltd)\.?)?),\s*(?P<a>(?:P\.?\s?O\.?\s+Box\s+\d+|\d{1,6}\s+[A-Z][\w.' ]{2,40})"
    r"[^$]{0,80}?\b(?:[A-Z]{2}|New York|New Jersey)\.?\s+\d{5}(?:-\d{4})?)"
)
_BIDS_RE = re.compile(
    r"\b(?P<w>one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d+)\s*(?:\((?P<n>\d+)\))?\s*"
    r"(?:sealed\s+|responsive\s+)?(?P<kind>bids?|proposals?|quotes?|quotations?|responses?)\s+(?:were|was)\s+received",
    re.I,
)
_WORDNUM = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve".split())}
_ACCOUNT_RE = re.compile(
    r"\b(?:Account|Acct\.?|Budget\s+Code|Line)\s*(?:No\.?|Number|#)?\s*:?\s*(?P<c>[A-Z]{0,2}\d[\w.\-]{3,24})"
    r"|\b(?P<c2>[A-Z]{1,2}\d{4}\.\d{3,5})\b"  # NY uniform system of accounts (A1680.54445)
    r"|\b(?P<c3>\d-\d{2}-\d{2}-\d{3}-\d{3})\b"  # NJ municipal account format
)

_PROCUREMENT = [  # first match wins
    ("emergency", re.compile(r"\bemergenc(?:y|ies)\b(?!\s+(?:Services|Management|Medical|Operations|Response|Communications|Preparedness|Planning|Supply|Dispatch|"
                             r"Vehicles?|Equipment|Generators?|Lighting|Notification|Alert|Call|Medical|Shelter|Contact))|N\.?J\.?S\.?A\.?\s*40A:11-6\b|General Municipal Law\s*§?\s*103\s*\(4\)", re.I)),
    ("intergovernmental", re.compile(r"shared\s+services?\s+agreement|inter-?local\s+(?:services\s+)?agreement|"
                                     r"40A:65|inter-?municipal\s+agreement|General\s+Municipal\s+Law\s+Article\s+5-G", re.I)),
    ("sole_source", re.compile(r"\bsole[\s-]source\b|\bsingle[\s-]source\b|\bproprietary\b", re.I)),
    ("no_bid", re.compile(r"non-?\s*fair\s+and\s+(?:non-?\s*)?open|without\s+(?:public\s+|competitive\s+)?bid|\bno[\s-]bid\b|"
                          r"exempt(?:ion)?\s+from\s+(?:public\s+|competitive\s+)?bidding|extraordinary,?\s+unspecifiable", re.I)),
    ("state_contract", re.compile(r"\bstate\s+contract\b|\bOGS\b|\bNYS\s+contract|\bcounty\s+contract\b|piggy-?back|"
                                  r"cooperative\s+(?:purchas|pricing|contract)|Sourcewell|BuyBoard|\bGSA\b|SAVVIK|HGAC|Omnia|NASPO|E&I\s+Cooperative|\bTIPS\b|"
                                  r"preferred\s+source|Industries\s+for\s+the\s+Disabled|NYSID|Corcraft|"
                                  r"\bN\.?J\.?\s*START\b|Educational\s+Services\s+Commission", re.I)),
    ("competitive_bid", re.compile(r"lowest\s+(?:responsible\s+|responsive\s+)*bidder|sealed\s+bids|bids?\s+(?:were|was)\s+received|"
                                   r"public(?:ly)?\s+(?:bid|advertised)", re.I)),
    ("rfp", re.compile(r"request\s+for\s+proposals?|\bRFP\b|\bRFQ\b|proposals?\s+(?:were|was)\s+received|competitive\s+contracting|(?<!non-)(?<!non)(?<!non )fair\s+and\s+open\s+process", re.I)),
    # Continues/amends a contract procured earlier — competition (if any) happened then.
    ("prior_award", re.compile(r"pursuant\s+to\s+Resolution\s+No\.?\s*\d|previously\s+(?:authorized|awarded|approved|retained)|"
                               r"continued\s+retention|extend(?:ing|sion\s+of)?\s+(?:the\s+)?(?:current\s+|existing\s+)?(?:agreement|contract)|"
                               r"renew(?:al|ing)?\s+(?:of\s+)?(?:the\s+)?(?:agreement|contract)", re.I)),
    ("professional_services", re.compile(r"professional\s+services?", re.I)),
]

_ACTION = [  # first match wins
    # Not spending: budgets, debt authorizations and insured values carry huge amounts.
    ("budget_adoption", re.compile(r"adoption\s+of\s+the\s+(?:operating\s+|annual\s+|capital\s+)?budget|appropriating\s+sums|"
                                   r"tax\s+levy|adopt(?:ing|ion\s+of)\s+the\s+\d{4}", re.I)),
    ("bond", re.compile(r"bond\s+ordinance|amendatory\s+bond|bond\s+anticipation|issuance\s+of\s+(?:serial\s+)?(?:bonds|notes)|"
                        r"bond\s+resolution|financing\s+(?:the\s+)?costs?\s+of|principal\s+of\s+and\s+interest", re.I)),
    # NJ budget mechanics: emergency/temporary appropriations (N.J.S.A. 40A:4-19/-20), Ch.159 items, tax policy.
    ("budget_adoption", re.compile(r"temporary\s+(?:appropriation|budget)|emergency\s+(?:temporary\s+)?appropriation|"
                                   r"special\s+item\s+of\s+revenue|N\.?J\.?S\.?A\.?\s*40A:4-87|grace\s+period|interest\s+rates?\s+"
                                   r"(?:and|for)\s+(?:policy\s+for\s+)?(?:delinquent\s+)?taxes|cancel(?:ling|lation\s+of)\s+.*?(?:taxes|balances)", re.I)),
    ("refund", re.compile(r"\breturn\s+of\s+(?:the\s+)?(?:performance\s+|maintenance\s+|street\s+opening\s+)?(?:bond|deposit|escrow|cash)|"
                          r"\brefund(?:ing|s)?\b|redemption\s+of\s+(?:tax\s+sale\s+)?certificate|overpayment", re.I)),
    ("insurance", re.compile(r"\binsurance\b|\bpremium|\bcoverage\b|insurable\s+value|limits?\s+of\s+liability", re.I)),
    ("employment", re.compile(r"employment\s+(?:contract|agreement)|appoint\w*\s+[A-Z][a-z]+\s+[A-Z][a-z]+\s+as\s+(?:the\s+)?"
                              r"(?:Corporation\s+Counsel|City\s+Manager|Comptroller|Commissioner|Director|Chief)", re.I)),
    ("employment", re.compile(r"\bLOSAP\b|length\s+of\s+service\s+award|"
                              r"\b(?:appoint(?:ment|ing)\s+(?:of\s+)?|reassignment\s+of\s+|re-?distributing\s+duties\s+for\s+)"
                              r"(?!.*\b(?:attorney|counsel|auditor|engineer|planner|appraiser|consultant|services?|bond\s+counsel|"
                              r"agent\s+for|prosecutor|public\s+defender)\b)", re.I)),
    ("payout", re.compile(r"separation\s+pay|termination\s+pay|terminal\s+leave|pay[\s-]?outs?\b|accrued\s+(?:vacation|sick|leave)|"
                          r"unused\s+(?:sick|vacation)|retirement\s+incentive|severance", re.I)),
    ("settlement", re.compile(r"\bsettle(?:ment|d)?\b|\bjudgment\b|\bstipulation\b", re.I)),
    ("change_order", re.compile(r"change\s+order|contract\s+amendment|increase\s+(?:the\s+)?(?:amount|contract|purchase\s+order)|"
                                r"previously\s+authorized\s+purchase\s+order|additional\s+(?:cost|services|fees?|work)\b", re.I)),
    ("transfer", re.compile(r"transfer\s+of\s+funds|budget\s+(?:amendment|transfer)|appropriation\s+transfer", re.I)),
    ("grant", re.compile(r"\bgrant\b|accept\s+(?:funds|funding)|reimbursement\s+from", re.I)),
    ("lease", re.compile(r"\blease\b|license\s+agreement|concession", re.I)),
    ("purchase", re.compile(r"\bpurchase\b|\bacquire\b|\bprocure\b", re.I)),
    ("contract", re.compile(r"\bcontract\b|\bagreement\b|\bretain\b|\bservices\b|\brepairs?\b|\bconstruction\b|"
                           r"\bdesign\b|\bmaintenance\b|\binstall", re.I)),
]

_APPOINT_RE = re.compile(r"\bappoint(?:ment|ed|ing)?\b|designated\s+as", re.I)
_PROF_ROLE_RE = re.compile(r"\b(?:attorney|counsel|auditor|engineer|planner|appraiser|consultant|architect|surveyor|"
                           r"prosecutor|public\s+defender|judge|professional)\b", re.I)
_TERM_END_RE = re.compile(rf"(?:through|ending|until|terminat\w*\s+on)\s+(({_MONTHS})\s+\d{{1,2}},\s*\d{{4}})", re.I)
# "appoint Doug Rohmeyer of Colliers Engineering & Design to provide …" → the firm is the vendor.
_PERSON_OF_FIRM_RE = re.compile(
    r"(?:appoint(?:ed)?|contract\s+to)\s+(?:and\s+appoint\s+)?(?P<p>(?:[A-Z][a-z]+\.?\s+){1,2}(?:[A-Z]\.\s+)?[A-Z][a-z]+)"
    r"(?:,\s*[A-Z]{2,4}(?:,\s*[A-Z]{2,4})*)?\s+(?:of|with)\s+(?:the\s+firm\s+)?"
    r"(?P<v>[A-Z][\w&.'’\- ]{2,60}?)(?=\s+(?:\[|\(|to|for|as|is)\b|,)")
_PERSON_OF_FIRM2_RE = re.compile(  # "that Douglas M. Rohmeyer of CME Associates is hereby appointed"
    r"(?P<p>(?:[A-Z][a-z]+\.?\s+){1,2}(?:[A-Z]\.\s+)?[A-Z][a-z]+)\s+(?:of|with)\s+(?:the\s+firm\s+)?"
    r"(?P<v>[A-Z][\w&.'’\- ]{2,60}?)\s+(?:is|are)\s+hereby\s+appointed")
_RATE_RE = re.compile(r"\$\s?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?)\s*(?:per|an|/|a)\s*(?:hour|hr\b)|"
                      r"hourly\s+rate\s+(?:of\s+)?\$\s?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?)", re.I)
_PROF_CONTRACT_RE = re.compile(r"non-?\s*fair\s+and\s+(?:non-?\s*)?open|professional\s+services?\s+contract|"
                               r"(?:consultant|consulting|writer|writing)\s+services|award\s+of\s+(?:a|the)\s+contract", re.I)

_TITLE_RE = re.compile(r"(Resolution\s+(?:Authorizing|Approving|for|Awarding|Accepting|Setting|Amending|to|Ratifying)\b[^\n]{3,200}"
                       r"(?:\n(?!\s*(?:WHEREAS|NOW|RESOLVED))[^\n]{3,160})?)", re.I)

# "$X" right after a price phrase is the award amount; other dollar figures in a
# resolution are often budgets, prior awards or grant totals.
_PRICED_RE = re.compile(
    r"(?:cost|price|amount|sum|total|fee|not\s+to\s+exceed|NTE|bid|proposal|quote)\s*(?:of|is|was|:)?\s*"
    r"(?:not\s+to\s+exceed\s+|up\s+to\s+|approximately\s+)?\$\s?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?)(?!\d)", re.I)
_NOT_VENDOR = re.compile(r"(?i)\b(section|law|interview|page|resolution|whereas|council|file|minutes|agenda|selection|"
                         r"program|modification|activities|regularly|scheduled|meeting|extend|current|agreement for)\b")


def plausible_vendor(name: str) -> bool:
    """Reject sentence fragments the patterns sometimes catch in scanned memos."""
    words = name.split()
    return 1 <= len(words) <= 8 and not _NOT_VENDOR.search(name) and sum(c.isdigit() for c in name) <= 4 \
        and not name.lower().endswith((" the", " of", " and"))


_SUFFIX_NORM = re.compile(r"\b(the|inc|incorporated|llc|l l c|llp|corp|corporation|company|co|pc|p c|pa|ltd|limited|dba|d b a)\b")


def norm_name(name: str | None) -> str:
    """Comparable vendor/person name: 'ICC Community Development Solutions, Inc.' → 'icc community development solutions'."""
    if not name:
        return ""
    s = name.lower().replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    s = _SUFFIX_NORM.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_money(s: str) -> float:
    return float(s.replace(",", ""))


def _page_at(text: str, pos: int) -> int | None:
    page = None
    for m in _PAGE_RE.finditer(text, 0, pos + 1):
        page = int(m.group(1))
    return page


def find_date(text: str) -> date | None:
    m = _LONG_DATE_RE.search(text or "")
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
        except ValueError:
            pass
    m = _NUM_DATE_RE.search(text or "")
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def _quote(seg: str, anchor: re.Match | None, width: int = 320) -> str:
    if not anchor:
        return " ".join(seg[:width].split())
    start = max(0, anchor.start() - width // 2)
    return " ".join(seg[start:start + width].split())


@dataclass
class AwardFact:
    item_no: str | None
    page: int | None
    title: str
    vendor_name: str | None
    vendor_address: str | None
    amount: float | None
    action_type: str
    procurement: str
    bids_received: int | None
    account_code: str | None
    meeting_date: date | None
    quote: str
    details: dict = field(default_factory=dict)  # resolution_no, meeting_type, votes, base_amount, p2p_disclosure, docket…


@dataclass
class PersonnelFact:
    last_name: str
    first_name: str
    department: str
    action: str
    position: str
    salary: str
    effective: str
    page: int | None
    quote: str


@dataclass
class OfficialFact:
    name: str
    last_name: str
    role: str


@dataclass
class Facts:
    awards: list[AwardFact] = field(default_factory=list)
    personnel: list[PersonnelFact] = field(default_factory=list)
    officials: list[OfficialFact] = field(default_factory=list)


def split_items(text: str) -> list[tuple[str | None, int, str]]:
    """[(item_no, start_offset, segment)] for every block that contains a RESOLVED clause."""
    # A bare "Resolution No." (number left blank on the agenda) isn't a boundary.
    marks = [m for m in _ITEM_RE.finditer(text) if m.group("a") or m.group("b") or m.group("c")]
    segs: list[tuple[str | None, int, str]] = []
    if marks:
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            seg = text[m.start():end]
            if _RESOLVED_RE.search(seg):
                segs.append(((m.group("a") or m.group("b") or re.sub(r"\s", "", m.group("c") or "")), m.start(), seg))
    if not segs:
        # No numbered items: treat each WHEREAS…RESOLVED run as one resolution.
        for m in re.finditer(r"(?is)(WHEREAS\b.*?RESOLVED\b.*?)(?=\bWHEREAS\b(?!.*RESOLVED.*\bWHEREAS)|\Z)", text):
            segs.append((None, m.start(), m.group(1)))
    return segs


def _classify(patterns, text: str, default: str) -> tuple[str, re.Match | None]:
    for label, rx in patterns:
        m = rx.search(text)
        if m:
            return label, m
    return default, None


def extract_awards(text: str, fallback_date: date | None = None) -> list[AwardFact]:
    text = normalize(text)
    out: list[AwardFact] = []
    for item_no, start, raw in split_items(text):
        # Collapse whitespace so names/addresses wrapped across lines still match.
        seg = " ".join(_PAGE_RE.sub(" ", raw).split())
        resolved = _RESOLVED_RE.search(seg)
        if resolved:
            # A resolution is WHEREAS…RESOLVED plus a paragraph or two. Anything
            # beyond that (an attached audit, a contract) isn't this item's text.
            seg = seg[max(0, resolved.start() - 3000):resolved.start() + 1500]
            resolved = _RESOLVED_RE.search(seg)
        body = seg[resolved.start():] if resolved else seg
        # Prefer the RESOLVED clause; the WHEREAS background often quotes bond
        # totals, grant awards and prior contracts.
        amounts = ([parse_money(m.group(1)) for m in _PRICED_RE.finditer(body)]
                   or [parse_money(m.group(1)) for m in _MONEY_RE.finditer(body)]
                   or [parse_money(m.group(1)) for m in _PRICED_RE.finditer(seg)]
                   or [parse_money(m.group(1)) for m in _MONEY_RE.finditer(seg)])
        rates = {parse_money(m.group(1) or m.group(2)) for m in _RATE_RE.finditer(seg)}
        amounts = [a for a in amounts if a >= 1 and a not in rates]
        rates = rates or set()
        title_m = _TITLE_RE.search(raw)
        title = _caps_title(raw) or (" ".join(title_m.group(1).split())[:240] if title_m else " ".join(seg[:160].split()))
        action, _ = _classify(_ACTION, title, "")
        whole, _ = _classify(_ACTION, title + " " + seg[:1500], "other")
        # Generic title verbs defer to the body when it says what the money is for (a transfer funding separation pay).
        if not action or (action in ("transfer", "contract", "other") and whole in ("payout", "settlement")):
            action = whole
        if action == "grant" and _PROF_CONTRACT_RE.search(title + " " + seg[:1500]):
            action = "contract"
        procurement, proc_m = _classify(_PROCUREMENT, seg, "unknown")
        if procurement == "emergency" and not re.search(r"emergenc", title + " " + body, re.I):
            # "Emergency" only in the WHEREAS background (FEMA, Sandy…) — not an emergency procurement.
            procurement, proc_m = _classify(_PROCUREMENT[1:], seg, "unknown")
        details: dict = {}
        if item_no:
            details["resolution_no"] = item_no
        cert = _CERT_RE.search(seg) or _CERT_RE.search(" ".join(raw.split()))
        cert_date = None
        if cert:
            details["meeting_type"] = cert.group(1).title()
            cert_date = find_date(cert.group(2))
        votes = parse_votes(seg)
        if votes:
            details["votes"] = votes
        dk = _DOCKET_RE.search(seg)
        if dk:
            details["docket"] = dk.group(1)
        if re.search(r"^\W*WITHDRAWN\b", raw.strip()[:40], re.I) or re.search(r"(?m)^\s*WITHDRAWN\s*$", raw[:400]):
            details["withdrawn"] = True
        funds = {f"{m.group(1)} {' '.join(m.group(2).split()).strip(' -–')}": parse_money(m.group(3))
                 for m in _FUND_TOTAL_RE.finditer(seg)}
        if funds or re.search(r"payment\s+of\s+(?:the\s+)?bills", title, re.I):
            action = "bill_payment"
            if funds:
                details["funds"] = funds
        if action == "change_order":
            fb = _CO_FROM_BY_RE.search(body) or _CO_FROM_BY_RE.search(seg)
            if fb:
                details["base_amount"], amounts = parse_money(fb.group(1)), [parse_money(fb.group(2))]
            else:
                base = _CO_BASE_RE.search(seg)
                if base:
                    details["base_amount"] = parse_money(base.group(1))
            tot = _CO_TOTAL_RE.search(seg)
            if tot:
                details["new_total"] = parse_money(tot.group(1))
        rates = sorted(rates)
        professional_appt = (action in ("contract", "purchase", "other") and _APPOINT_RE.search(title + " " + body[:800])
                             and _PROF_ROLE_RE.search(title + " " + body[:800]))
        if professional_appt:
            action = "contract" if action == "other" else action
            details["appointment"] = True
            if rates:
                details["hourly_rates"] = rates
        te = _TERM_END_RE.search(body) or _TERM_END_RE.search(seg)
        if te and action in ("contract", "purchase"):
            d_end = find_date(te.group(1))
            if d_end:
                details["term_end"] = d_end.isoformat()
        if not amounts and action not in ("payout", "settlement", "change_order") and not professional_appt:
            continue
        if action in ("transfer", "grant", "payout", "settlement", "budget_adoption", "bond", "insurance", "employment",
                      "bill_payment", "refund"):
            procurement, proc_m = "n/a", None  # not a purchase; procurement rules don't apply
        vm = _VENDOR_ADDR_RE.search(body) or _VENDOR_ADDR_RE.search(seg)
        vendor, address = (vm.group("v").strip(" ,"), " ".join(vm.group("a").split())) if vm else (None, None)
        pf = (_PERSON_OF_FIRM_RE.search(body) or _PERSON_OF_FIRM2_RE.search(body) or _PERSON_OF_FIRM_RE.search(seg)
              ) if action in ("contract", "purchase") else None
        if pf and plausible_vendor(pf.group("v")):
            vendor, details["principal"] = pf.group("v").strip(" ,"), pf.group("p")
        if not vendor:
            vm2 = _VENDOR_RE.search(body) or _VENDOR_RE.search(seg)
            vendor = vm2.group("v").strip(" ,") if vm2 else None
        if not vendor:
            vm3 = _NJ_VENDOR_RE.search(body) or _NJ_VENDOR_RE.search(seg)
            vendor = vm3.group("v").strip(" ,") if vm3 else None
        if not vendor and title.isupper():
            for rx in _TITLE_VENDOR_RES:
                tm = rx.search(title)
                if tm and plausible_vendor(tm.group("v")):
                    vendor = tm.group("v").strip()
                    break
        if vendor and (re.match(r"(?i)^(the\s+)?(city|town|village|borough|county|state|council|board|department)\b", vendor)
                       or not plausible_vendor(vendor)):
            vendor = None  # "with the City of …" is the counterparty, not a vendor
        bm = _BIDS_RE.search(seg)
        bids = None
        if bm:
            bids = int(bm.group("n")) if bm.group("n") else _WORDNUM.get(bm.group("w").lower(), None)
            if bids is None and bm.group("w").isdigit():
                bids = int(bm.group("w"))
        am = _ACCOUNT_RE.search(seg)
        account = (am.group("c") or am.group("c2") or am.group("c3")) if am else None
        money_m = _MONEY_RE.search(body) if amounts else None
        quote = _quote(seg, money_m or proc_m or resolved)
        if action == "bill_payment" and funds:
            amount = round(sum(funds.values()), 2)
        elif action == "change_order" and "base_amount" in details and amounts:
            amount = amounts[0]  # the increase, not the new total
        else:
            amount = max(amounts) if amounts else None
        if action in ("contract", "purchase", "change_order") and procurement in ("no_bid", "professional_services", "unknown"):
            details["p2p_disclosure"] = bool(_P2P_DISCLOSURE_RE.search(seg))
        if action in ("contract", "purchase", "change_order"):
            if re.search(r"non-?\s*fair\s+and\s+(?:non-?\s*)?open", seg, re.I):
                details["non_fair_open"] = True
            elif re.search(r"fair\s+and\s+open", seg, re.I):
                details["fair_open"] = True
            elif procurement in ("professional_services", "no_bid") and re.search(r"40A:11-5\s*\(\s*1\s*\)\s*\(\s*a\s*\)", seg):
                details["non_fair_open"] = True  # LPCL professional-services exemption, no fair-and-open process recited
            if re.search(r"\bappoint(?:ment|ed|ing)?\b|designated\s+as", title + " " + body[:600], re.I):
                details["appointment"] = True
        out.append(AwardFact(
            item_no=item_no, page=_page_at(text, start), title=title, vendor_name=vendor, vendor_address=address,
            amount=amount, action_type=action, procurement=procurement, bids_received=bids, account_code=account,
            meeting_date=cert_date or fallback_date or find_date(seg[:400]), quote=quote, details=details,
        ))
    return out


_PERSONNEL_ACTIONS = re.compile(
    r"^(Appointed|Hired|Promoted|Reappointed|Re-appointed|Transferred|Reinstated|Resigned|Retired|Terminated|"
    r"Separated|Laid Off|Salary (?:Adjustment|Increase)|Title Change|Rehired|Seasonal|Provisional)\b", re.I)


def extract_personnel(text: str) -> list[PersonnelFact]:
    """Column-per-line personnel reports (LAST / FIRST / DEPARTMENT / ACTION / POSITION / SALARY / EFFECTIVE)."""
    if not re.search(r"\bLAST\b.*\bFIRST\b", text[:600], re.S | re.I) and "personnel" not in text[:2000].lower():
        return []
    lines = [l.strip() for l in text.splitlines()]
    out: list[PersonnelFact] = []
    for i, line in enumerate(lines):
        if not _PERSONNEL_ACTIONS.match(line) or i < 3:
            continue
        last, first, dept = lines[i - 3], lines[i - 2], lines[i - 1]
        if not last or not first or len(last) > 40 or any(c.isdigit() for c in last + first):
            continue
        rest = " ".join(lines[i + 1:i + 4])
        sal = re.search(r"\$[\d,]+(?:\.\d{2})?(?:\s*/\s*(?:hr|hour|yr|year|annually))?", rest)
        eff = re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", rest)
        out.append(PersonnelFact(
            last_name=last.title(), first_name=first.title(), department=dept, action=line,
            position=lines[i + 1] if i + 1 < len(lines) else "", salary=sal.group(0) if sal else "",
            effective=eff.group(0) if eff else "", page=_page_at(text, text.find(last)),
            quote=" / ".join(x for x in (last, first, dept, line, lines[i + 1] if i + 1 < len(lines) else "") if x),
        ))
    return out


_ROLE_WORDS = r"(?:Council\s+President|Council\s+Vice\s+President|Council\s*(?:Members?|women|men|persons?)|Councilm[ae]n|" \
              r"Councilwoman|Councilmembers?|Deputy\s+Mayor|Mayor|President|Trustees?|Commissioners?|Members?)"


def extract_officials(text: str) -> list[OfficialFact]:
    """Roll call: 'Present: Council President Finn and Council Members Bendo, Fiumara, Lester, and Reinhart.'"""
    m = re.search(r"(?is)\bPresent\s*:\s*(.{5,400}?)(?:\n\s*\n|\bAbsent\b|\bAlso\s+Present\b|$)", text[:6000])
    if not m:
        return []
    # Mark role phrases left-to-right (longest alternatives first) so "Council Members" stays one token.
    marked = re.sub(rf"\b({_ROLE_WORDS})\b", lambda r: f"|{' '.join(r.group(1).split())}|", " ".join(m.group(1).split()))
    out: list[OfficialFact] = []
    role = "Member"
    for i, part in enumerate(marked.split("|")):
        if i % 2 == 1:
            role = part
            continue
        for n in re.split(r",|\band\b|;", part):
            n = n.strip(" .")
            if re.fullmatch(r"[A-Z][a-zA-Z'’\-]+(?:\s+[A-Z][a-zA-Z'’\-]+){0,2}", n or ""):
                out.append(OfficialFact(name=n, last_name=n.split()[-1].title(), role=role))
    return out


def _caps_title(raw: str) -> str:
    """NJ resolutions: the lines between 'RESOLUTION 054-2026' and the first WHEREAS are the title
    (ALL-CAPS or not; may wrap and contain dashes)."""
    m = re.search(r"(?m)^\s*RESOLUTION\s+(?:NO\.?\s*)?\d{1,4}\s*-\s*\d{2,4}\s*$", raw)
    if not m:
        return ""
    lines = []
    for line in raw[m.end():].splitlines():
        line = line.strip()
        if not line:
            if lines and len(lines) >= 1 and sum(map(len, lines)) > 40:
                continue
            continue
        if re.match(r"(?i)(WHEREAS|NOW,?\s|BE\s+IT|RESOLVED|Motion|##\s*Page)", line) or len(lines) >= 5:
            break
        lines.append(line)
    title = " ".join(" ".join(lines).split())
    return title[:240] if 8 <= len(title) <= 400 else ""


def extract_all(text: str, fallback_date: date | None = None) -> Facts:
    text = normalize(text)
    # Meeting date: the document's own date, else the first date in its header
    # ("REGULAR MEETING … held OCTOBER 7, 2025"), never a date quoted mid-resolution.
    fallback_date = fallback_date or find_date(text[:1500])
    awards = extract_awards(text, fallback_date)
    officials = extract_officials(text)
    if not officials:
        # NJ resolutions carry the roll call instead of a 'Present:' line.
        seen = set()
        for a in awards:
            v = a.details.get("votes") or {}
            for key in ("yes", "no", "abstain", "absent"):
                for n in v.get(key, []):
                    if n not in seen:
                        seen.add(n)
                        officials.append(OfficialFact(name=n, last_name=n.split()[-1].title(), role="Governing Body"))
    return Facts(awards=awards, personnel=extract_personnel(text), officials=officials)


def iter_snippets(text: str, patterns: Iterable[str], width: int = 260, limit: int = 3) -> list[tuple[int | None, str]]:
    """Up to `limit` (page, quote) pairs around the first hits of any pattern."""
    out = []
    for p in patterns:
        for m in re.finditer(p, text, re.I):
            out.append((_page_at(text, m.start()), " ".join(text[max(0, m.start() - width // 2):m.start() + width // 2].split())))
            break
        if len(out) >= limit:
            break
    return out


# ── NJ bill lists ("Bill List By Vendor Name", Edmunds/GMIS-style exports) ────

@dataclass
class PaymentFact:
    vendor_name: str
    vendor_code: str
    amount: float
    po_numbers: list[str]
    accounts: list[str]
    descriptions: list[str]
    page: int | None
    quote: str


_VENDOR_TOTAL_RE = re.compile(r"(?im)^\s*vendor\s+total\s*[:;]?\s*$|vendor\s+total\s*[:;]\s*(?P<inline>[\d,]+\.\d{2})")
_PO_RE = re.compile(r"\b(\d{2}-\d{5})\b(?:\s+(\d{2}/\d{2}/\d{2,4}))?\s*(.*)")
_NJ_ACCT_RE = re.compile(r"\b\d-\d{2}-\d{2}-\d{3}-\d{3}-\d{3}\b|\b[A-Z]?-?\d{2}-\d{2}-\d{3}-\d{3}\b")
_AMOUNT_LINE_RE = re.compile(r"^\s*(-?[\d,]{1,12}\.\d{2})\s*$")
_CODE_LINE_RE = re.compile(r"^[A-Z0-9'’`~.\-]{5,10}$")
_HEADER_WORDS = re.compile(r"bill list|vendor #|^name$|^first$|^rcvd|chk/void|stat/chk|^invoice|^1099|^po date|^description|"
                           r"^po #|^contract|^po type|^amount|^charge account|^acct type|^excl|^item description|"
                           r"^date$|^last$|^to$|^range|^format:|^vendors:|include non|^void:|^held:|^aprv:|^bid:|^state:|"
                           r"^other:|^exempt:|p\.o\. type|^page\s*\d+|^\d+/\d+/\d+\s+\d+:\d+|^[A-Z]?\s?[A-Z&]+:\s|^\d{1,2}\s", re.I)
# Vendor codes are ~5–10 chars ending in a sequence number that OCR mangles (005 → OOS, 0Q5, 00S).
_GLUED_CODE_RE = re.compile(r"^[A-Za-z0-9'’`~.\-]{3,8}?(?:0{1,2}[0-9OQSs]|O{1,2}[0-9OQSs5]|0Q5)[0-9OQSs5]?(?=[A-Z'’ ])")


def _looks_like_name(l: str) -> bool:
    if re.match(r"(?i)^(item|page|line)\s*\d|^[A-Z]\s+[A-Z&]+[:\s]|^\d", l):  # line-item / budget-code rows
        return False
    words = [w for w in re.split(r"[\s']+", l) if sum(c.isalpha() for c in w) >= 2]
    return (len(words) >= 2 or (len(words) == 1 and len(l) >= 6 and not _CODE_LINE_RE.match(l))) \
        and not _PO_RE.match(l) and not _NJ_ACCT_RE.search(l) and not re.search(r"\d{2}/\d{2}/\d{2}", l)


def _clean_vendor(name: str) -> str:
    name = re.sub(r"[^\w&.,'\- ]", "", name)  # OCR debris: ~ ` ''
    return re.sub(r"\s+", " ", name).strip(" .,'-")


def extract_bill_list(text: str) -> list[PaymentFact]:
    """Per-vendor payment totals from a bill list. Tolerates OCR noise; skips
    vendors whose total can't be read rather than guessing."""
    if not re.search(r"vendor\s+total", text, re.I):
        return []
    lines = text.splitlines()
    out: list[PaymentFact] = []
    seg_start = 0
    i = 0
    offsets = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    while i < len(lines):
        m = re.match(r"(?i)^\s*vendor\s+total\s*[:;]?\s*([\d,]+\.\d{2})?\s*$", lines[i])
        if not m:
            i += 1
            continue
        total_s = m.group(1)
        j = i + 1
        while not total_s and j < min(i + 4, len(lines)):
            am = _AMOUNT_LINE_RE.match(lines[j])
            if am:
                total_s = am.group(1)
                break
            j += 1
        seg = [l.strip() for l in lines[seg_start:i] if l.strip()]
        body = [l for l in seg if not _HEADER_WORDS.search(l)]
        seg_start = max(i + 1, j + 1)
        i = seg_start
        if not total_s or not body:
            continue
        # Vendor = the line after the vendor-code line (first code-like line in the segment).
        code, name = "", ""
        for k, l in enumerate(body[:6]):
            if _CODE_LINE_RE.match(l) and k + 1 < len(body) and _looks_like_name(body[k + 1]):
                code, name = l, body[k + 1]
                break
        if not name:
            name = next((l for l in body[:6] if _looks_like_name(l)), "")
        if not name:
            name = code or "Unreadable vendor (see page)"
        glued = _GLUED_CODE_RE.match(name)
        if glued and len(name) - glued.end() >= 4:
            code, name = code or glued.group(0), name[glued.end():]
        name = _clean_vendor(name)
        if len(name) < 3 or re.fullmatch(r"[\d,.\s/-]+", name):
            continue
        pos_, descs = [], []
        for l in body:
            pm = _PO_RE.match(l)
            if pm:
                pos_.append(pm.group(1))
                if pm.group(3):
                    descs.append(pm.group(3).strip()[:120])
        accounts = sorted(set(_NJ_ACCT_RE.findall(" ".join(body))))
        out.append(PaymentFact(
            vendor_name=name, vendor_code=code, amount=parse_money(total_s), po_numbers=pos_, accounts=accounts,
            descriptions=descs[:5], page=_page_at(text, offsets[min(i, len(offsets) - 1)]),
            quote=f"{name} — POs {', '.join(pos_[:4]) or 'n/a'} — {'; '.join(descs[:2])} — Vendor Total {total_s}",
        ))
    return out
