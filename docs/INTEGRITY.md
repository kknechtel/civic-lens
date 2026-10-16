# Busting corruption with public records

Corruption in local government leaves a paper trail, and that trail is
almost always public. Every no-bid contract, emergency award, payout, hire,
variance and tax abatement passes through a resolution, a bill list or an
audit. The problem is that nobody reads them, and nobody connects them to
the campaign-finance, business-registry and property records that would
reveal *who benefits*.

Civic Lens reads them. This document describes what the integrity engine does
today, where it is going, and the guardrails that keep it credible.

---

## 1. The model: money ↔ people ↔ decisions

Nearly every local corruption scheme is a **relationship that a public
decision rewards**:

| Scheme | The decision | The relationship | Where both sides are recorded |
|---|---|---|---|
| Pay-to-play | Contract / professional appointment | Contributions from the vendor or its principals | Resolutions ↔ campaign-finance filings |
| Bid avoidance | Purchases split under the bid threshold; "emergencies" | Favoured vendor | Resolutions, bill lists |
| Bid rigging / steering | Single bidder, tailored specs, change-order recovery | Vendor ↔ procurement staff | Bid tabulations, change orders |
| Golden parachutes | Separation / terminal-leave payouts | Departing insiders | Budget transfers, personnel reports |
| Nepotism / patronage | Hires, promotions, overtime | Family / party of officials | Personnel reports ↔ rosters, disclosures |
| Land-use favours | Variances, PILOTs, redevelopment designations | Applicant ↔ donors / officials | Board decisions ↔ campaign finance, property records |
| Self-dealing | Payments to companies officials own | Official ↔ LLC | Bill lists ↔ financial disclosure statements, business registries |

So the engine works in five steps:

1. **Collect** every level of government: municipality, county, state and
   federal.
2. **Extract** structured facts.
3. **Link** entities: vendors, LLCs, principals, officials, donors and
   addresses.
4. **Detect** patterns.
5. **Verify** with a human before anything is published.

---

## 2. What runs today

`api/services/integrity/` runs after every nightly crawl
(`scripts/scheduled_scrape.py`) or on demand (`python -m scripts.run_integrity`).

**Extraction** is deterministic, with no LLM. Every fact keeps the page and a
quote it came from.

- **Spending actions** from resolutions: vendor, address, amount, account
  code, meeting date, action type (contract, purchase, payout, transfer,
  settlement, change order, grant, lease), procurement method (competitive
  bid, RFP, state or cooperative contract, emergency, professional services,
  no-bid, sole source, undocumented), and number of bids received.
- **Personnel actions** from personnel reports.
- **Officials** from proceedings roll calls.
- **Campaign contributions**, imported as CSV from any state filing system.

**Rules.** Thresholds and statutes are configured per state in
`jurisdictions/us/<st>/jurisdiction.yaml`; the code itself is state-neutral.

| Rule | Signal | NJ / NY basis |
|---|---|---|
| `near_threshold` | Non-competitive award at 85–100% of the bid threshold | N.J.S.A. 40A:11-3 / GML §103 |
| `split_purchases` | Sub-threshold awards to one vendor within 90 days that sum past the threshold | same |
| `emergency_procurement` | Emergency awards; repeated or large ones rank high | N.J.S.A. 40A:11-6 / GML §103(4) |
| `noncompetitive_concentration` | ≥3 professional-service, no-bid, sole-source or undocumented awards to one vendor in 24 months | |
| `single_bid` | A "competitive" process that drew one bid | |
| `duplicate_award` | Same vendor and amount authorized twice within 60 days | |
| `payout` / `payout_total` | Large separation or terminal-leave funding, and fiscal-year totals | |
| `change_order_growth` | Change orders more than 20% over the original award | N.J.A.C. 5:30-11 |
| `official_surname_hire` | A hire shares an *uncommon* surname with a sitting official | |
| `pay_to_play` | Vendor or its people contributed within 365 days before an award | N.J.S.A. 19:44A-20.4 et seq. (statutory in NJ) |
| `payments_over_threshold` | Bill-list payments to one vendor in a fiscal year above the bid threshold with no competitive award on record | N.J.S.A. 40A:11-3 (aggregation) |
| `emergency_after_planning` | An "emergency" award for a project already studied/engineered ≥30 days earlier (undated resolutions dated from their numbering, as a lower bound) | N.J.S.A. 40A:11-6, N.J.A.C. 5:34-6 |
| `nfo_missing_disclosure` | Non-fair-and-open award over $17,500 whose resolution cites no Business Entity Disclosure, and no term contract with one on file covers it | N.J.S.A. 19:44A-20.5 |
| `p2p_threshold_split` | Several non-fair-and-open awards to one vendor, each ≤ $17,500, together over it within a year | same |
| `successor_firm_nfo` | A professional appointed competitively changes firms mid-term and the new firm gets a non-fair-and-open contract | N.J.S.A. 19:44A-20.4–.7 |
| `elec_annual_disclosure` | A vendor over $50,000 in a year including non-fair-and-open awards: check its ELEC Form B-1 | N.J.S.A. 19:44A-20.27 |
| `professional_tenure` | Same professional appointed ≥4 years running | |
| `bill_list_spike` | A Payment-of-Bills resolution ≥3× the median | |
| `oversight_findings` | Adverse language in audits and State Comptroller reports ("significant fiscal stress", "did not comply", "unsupported", repeat findings…) | |

**Review.** Flags are stored with a stable fingerprint, so a reviewer's
*dismissed* or *confirmed* survives re-runs. The Integrity page shows each
flag with its explanation, the innocent explanations to rule out, and the
evidence documents. The chat assistant can query flags and spending actions,
and it is instructed to present them as leads, not conclusions.

---

## 3. Next detectors, ranked by payoff and effort

**New Jersey first.** It has the richest disclosure regime in the country,
and the most to find.

1. **Bill lists** (the "payment of bills" resolutions). Every NJ governing
   body approves every check by resolution. Parsing them gives a complete
   payments ledger. That enables duplicate invoices, round-dollar and
   Benford's-law tests, vendor-concentration analysis, and payments with no
   matching award.
2. **Reorganization-day professional appointments.** Every January, NJ towns
   re-appoint the borough attorney, engineer, auditor, bond counsel and
   planner, mostly through "non-fair and open" contracts. The step is to
   cross-reference those firms against ELEC contributions, **including
   county party committees**. "Wheeling" money through county committees is
   the classic way to evade local pay-to-play limits.
3. **ELEC Business Entity Annual Statements (Form BE).** Any business that
   receives ≥$50,000 in public contracts in a year must report its political
   contributions (N.J.S.A. 19:44A-20.27). The step is to compare reported
   contributions against the awards this engine extracted. Gaps mean either
   non-filers or under-reporting.
4. **Financial Disclosure Statements** (Local Government Ethics Law,
   N.J.S.A. 40A:9-22.6). Officials list their income sources and business
   interests every year. Matching those business names and addresses against
   vendors, payees and land-use applicants catches self-dealing directly.
5. **Business registries.** NJ DORES (paid) and NY DOS (free) list officers,
   registered agents and formation dates. A vendor formed weeks before its
   award, or one sharing a registered agent or address with an official, is
   a strong signal.
6. **Land use.** Parse Zoning and Planning Board decisions (applicant,
   address, variances granted) and cross-reference them with contributions
   and with officials' property and disclosures.
7. **PILOTs and tax abatements.** Redevelopment agreements, cross-referenced
   with developer contributions and with the professionals involved.
8. **Federal money.** OpenFEMA Public Assistance, HUD CDBG-DR and
   USAspending. Hurricane Sandy money flowed through both Atlantic Highlands
   and Long Beach, and disaster recovery is historically where procurement
   discipline breaks down. Reconcile the federal obligations against local
   awards and change orders.
9. **Debarment.** Check every vendor against SAM.gov exclusions and the
   state debarment lists.
10. **Transparency gaps.** Missing minutes, missing bill lists, dead links to
    budgets (the first Long Beach crawl hit several 404s on budget documents
    still linked from city pages), and late or absent audits. What isn't
    published is a signal in itself. Auto-generate OPRA/FOIL requests for
    each gap and track the statutory deadlines.

**Where an LLM helps.** Use it as a second-pass reader on flagged documents
only: confirming that a split is really one project, summarizing a bid
tabulation, extracting tables from scanned bill lists. Rules stay
deterministic and explainable. The LLM adds recall without adding
unexplainable accusations.

---

## 4. Thinking big: the network effect

One town's records show anomalies. **Hundreds of towns' records show
patterns.**

- **Entity graph across deployments.** The same engineering firm is town
  engineer in 23 Monmouth County towns and the largest donor to the county
  committee. The same paving contractor wins "emergency" work in six
  shore towns after every storm. Those patterns are invisible
  municipality-by-municipality and obvious statewide.
- **Deploy at any level.** The jurisdiction tree already supports deploying
  a city (`us/ny/nassau/long-beach`), a county (`us/ny/nassau`) or a state
  (`us/nj`). State-level source templates instantiate per town, so adding a
  town is a few lines of YAML and its pages, not new code.
- **Open dataset.** Publish extracted *facts* (awards, payments,
  appointments, contributions), not accusations, as an open dataset with
  provenance. Journalists, auditors, inspectors general and academics build
  on it.
- **Benchmarks.** Per-town indicators such as non-competitive share of
  spending, emergency-procurement rate, change-order growth, payout totals
  and transparency gaps, ranked against peer towns. Outliers get attention;
  the median sets expectations.
- **Partnerships.** NJ and NY have active local-news nonprofits and
  watchdogs, the NJ Office of the State Comptroller (procurement, Medicaid
  and local-government fraud units), the NY State Comptroller, county
  prosecutors and inspectors general. The tool generates leads; they have
  subpoena power.

**Scale target.** New Jersey has 564 municipalities, 21 counties, about 600
school districts and hundreds of authorities. The state-level templates
(DLGS user-friendly budgets and annual financial statements, ELEC, DOE
ACFRs, OSC) cover every one of them on day one. Per-town crawlers for minutes
and bill lists are the long tail, which makes them an ideal job for
volunteers, one town at a time.

---

## 5. Guardrails: how this stays credible and lawful

- **Flags are leads, not findings.** Nothing is published automatically.
  Every flag carries its innocent explanations. A human reviews the evidence
  and records a note before a flag is marked *confirmed*.
- **Defamation risk is real.** Truth and fair report of public records are
  strong defenses. Implying wrongdoing from a pattern is not. Publish
  documents and facts, give subjects a chance to respond, and get counsel to
  review anything that names a person.
- **Entity resolution errors are the main false-positive source.** Two
  different "John Smith"s, or two different "ABC Construction"s, look the
  same to a name matcher. Pay-to-play and self-dealing matches require a
  second identifier (address, employer, registered agent) before ranking
  high.
- **Crawling.** This deployment ignores robots.txt for government sites by
  operator choice (`crawl.respect_robots: false` in the NJ and NY state
  files). The crawler still identifies itself honestly, rate-limits itself
  and backs off from failing hosts. Public records are public. US case law
  (*Van Buren v. United States*, 2021; *hiQ Labs v. LinkedIn*, 9th Cir. 2022)
  has narrowed anti-hacking claims over scraping publicly accessible pages,
  but terms-of-service and contract theories remain. Third-party hosts (for
  example the school district's ParentSquare file host) are private
  companies, so treat them more carefully than government servers. This
  isn't legal advice.
- **Privacy.** Personnel reports name seasonal teenagers. The engine
  extracts them only to check for official surnames; the UI and exports
  should never surface bulk personnel data.

---

## 6. Roadmap

| Phase | Deliverable | Done when |
|---|---|---|
| 0 (now) | Jurisdiction tree, 20 rules (NJ resolution formats calibrated on Atlantic Highlands), review workflow, Long Beach test deployment | Long Beach flags reviewed by a human; false-positive rate measured |
| 1 | NJ bill-list parser; ELEC import (contributions and Form BE); reorganization-appointment extractor; FDS import | Atlantic Highlands and 5 Monmouth towns running on the full NJ pack |
| 2 | Entity graph (vendors ↔ principals ↔ donors ↔ officials ↔ addresses) using NY DOS and NJ registries; land-use and PILOT parsers | Cross-town vendor pages; county-level deployment for Monmouth |
| 3 | Statewide NJ deployment from templates; public facts dataset; peer benchmarks; OPRA/FOIL gap automation | All 564 NJ municipalities have budget, audit and ELEC coverage |
