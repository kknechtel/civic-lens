# Long Beach, NY — profile status

**Live test, 2026-10-16** (`scripts/check_profile --live` plus a sampled end-to-end run)

| Source | Result |
|---|---|
| `lbcsd_board` (lbeach.org) | ✓ 513 agendas/minutes/notices since 2020 (`lbcsd_board_archive` has 595 more, 2007–2019). **Files are blocked**: they live on `files.smartsites.parentsquare.com`, whose robots.txt disallows all crawlers |
| `lbcsd_budget` | ✓ 204 budget documents discovered. Same file-host block |
| `library_board` | ✓ 172 documents, downloaded, extracted and indexed |
| `osc_audits` (State Comptroller) | ✓ 9 audits/budget reviews, downloaded, extracted and indexed |
| `city_*` (longbeachny.gov) | ✗ robots.txt: `User-agent: *` / `Disallow: /`. Needs the City Clerk's permission |
| `city_youtube`, `lbcsd_youtube` | Need `YOUTUBE_API_KEY` (YouTube's robots.txt disallows the RSS feed) |
| Library events (iCal) | ✓ Feed parses |

Sampled pipeline: 12 PDFs downloaded → text layer + page-level OCR → chunks →
keyword search, all in about 75 seconds with no API keys. The largest file was a
121-page library packet: 20 pages needed OCR, done in 7 seconds.

**To unlock the rest:**

1. Email the City Clerk (FOIL@longbeachny.gov or the clerk's office) asking
   permission to index `/councilmeetings`, `/budget`, `/audit`, `/zoning`,
   `/planning` and `/vertical/sites/.../uploads/`. Record the answer as a
   `permission_note`.
2. Ask the school district (or ParentSquare) the same about
   `files.smartsites.parentsquare.com/11147/`.
3. Set `YOUTUBE_API_KEY`.

Open gaps: Nassau County sources aren't configured yet (CivicPlus AgendaCenter
RSS is available), and the OSC bulk financial CSVs need a `json_api`/bulk
adapter.

---

# Long Beach, NY (Nassau County, 11561): public data source inventory

Verified 2026-10-15 from the agent sandbox, with curl through the proxy and `-A 'Mozilla/5.0'` unless noted. HTTP codes are what I saw.
Scope: City of Long Beach, **New York** only. This file excludes Long Beach CA, the Township of Long Beach NJ (ecode360 10301496/10301497 is NJ) and Long Beach MS. Watch for these in search results.

---

## 0. Crawling policy: read this first

| Host | robots.txt | Implication |
|---|---|---|
| www.longbeachny.gov | Named bots (Googlebot, bingbot, ia_archiver, archive.org_bot, W3C-checklink, CCBot) may crawl everything except `/admin/`, `/manager/`, `/*month*` and `/*GUESTBOOK*`. **`User-agent: *` → `Disallow: /`**. `Sitemap: https://www.longbeachny.gov/sitemap.xml` | A generic scraper is disallowed by robots. Decide on policy before you run the scraper. Options: a low-rate, targeted fetch of a few index pages; asking the City Clerk or PR office for permission; or treating the city as a manual source. Never crawl `*month*` calendar URLs, which Googlebot is also told to skip. |
| www.lbeach.org | `User-agent: *` disallows `/admin`, `/*lesson_plan` and `/userFiles`. **Crawl-delay: 5**. | Crawling is allowed. Wait at least 5 s between requests. |
| www.lbpd.com | `User-Agent: *` / `Disallow:` (empty) | Allow all. |
| liherald.com | Disallows only system paths. Sitemaps are listed. | Returns **403 unless the User-Agent looks like a full browser**: plain `Mozilla/5.0` gets 403, a Chrome UA string gets 200. |
| www.bidnetdirect.com | Crawl-delay 5. Disallows `?page=`, `?sort=`, `/private/` and others. | |
| www.nassaucountyny.gov | `User-agent: *` / `Allow: /` | Allow all. |

---

## 1. City of Long Beach: longbeachny.gov

**Platform:** Catalis GovOffice (formerly "Vision"-style GovOffice). The template is `/repository/designs/templates/GO_longbeach-ny_2024_resp/` and the footer credit links to catalisgov.com. Classic ASP pages use the pattern `index.asp?SEC={section-guid}&DE={document-entry-guid}`, and `Type=B_BASIC`, `Type=B_EV` (event calendar) and `Type=B_LIST` all appear. Pretty aliases such as `/councilmeetings` resolve to a SEC.
**Uploads:** `https://www.longbeachny.gov/vertical/sites/%7BC3C1054A-3D3A-41B3-8896-814D00B86D2A%7D/uploads/<filename>`. The case varies (`/vertical/Sites/` also appears). A sample PDF download returned 200 `application/pdf`.
**Gotcha:** many `href`s contain leading whitespace or newlines, as in `href="&#xA;                  /vertical/sites/%7B...%7D/uploads/10.6.26_Agenda_NS.pdf"`. Html-unescape and `.strip()` before you resolve them. Hrefs mix absolute `https://`, protocol-relative `//www.longbeachny.gov/...` and relative `/...` forms.
**Generic PDF regex:** `(?i)/vertical/sites/(%7B|\{)C3C1054A-3D3A-41B3-8896-814D00B86D2A(%7D|\})/uploads/[^"'\s]+\.pdf`
**Sitemap:** `https://www.longbeachny.gov/sitemap.xml` returns 200 and gives exactly **300 `<loc>` entries with `<lastmod>`**. It looks truncated at 300. It holds HTML pages only, with no PDFs. The council year pages do carry fresh lastmod values (the 2026 Proceedings page shows 2026-10-13), so the sitemap is useful for change detection on known pages.
**RSS:** none found. `/rss.asp`, `/rss.xml` and `/feed` return 404. `index.asp?Type=B_RSS&SEC=...` returns 200 but serves the normal HTML page, not RSS. **iCal:** none found. Event detail pages have no .ics or "add to calendar" link.

### 1a. City Council agendas, backup packets, proceedings, minutes (PRIMARY)
- **Index (hub):** https://www.longbeachny.gov/councilmeetings (200). It links one DE page per year and type, all under `SEC=443D12D5-7EB6-47F2-B2CE-BA33F15CF0E2`:

| Page | URL | Status | PDFs |
|---|---|---|---|
| Agendas 2026 | https://www.longbeachny.gov/index.asp?SEC=443D12D5-7EB6-47F2-B2CE-BA33F15CF0E2&DE=A4D83CA4-206E-470A-A3C2-145490BD37D0 | 200 | 45 |
| Proceedings 2026 (proceedings, minutes, personnel reports) | https://www.longbeachny.gov/index.asp?SEC=443D12D5-7EB6-47F2-B2CE-BA33F15CF0E2&DE=B76545F3-2AED-4FFB-9625-CF43FB30451E | 200 | 49 |
| 5/29/26 Special Meeting info | ...&DE=5628983D-5E58-410F-8A0F-D4654D626AB5 | | |
| Agendas 2025 | ...&DE=0C6F4CAB-E125-4C60-947A-3426F1EF70F7 | | |
| Proceedings 2025 | ...&DE=2E887D49-E1D0-44D4-8C96-411AD48E7F36 | | |
| Agendas 2024 / Proceedings 2024 | ...&DE=8A6B9B9D-4C7A-4D41-90A1-4006D9C93E8C / ...&DE=E762374F-0CFA-40CB-B0A9-72D98027BC5A | | |
| Agendas 2023 / Proceedings 2023 | ...&DE=9F719967-5367-45D5-A671-C6E542CCA4A7 / ...&DE=271FF6B3-00F5-471D-91C8-EB649D2B2034 | | |
| Agendas 2022 / Proceedings 2022 | ...&DE=D33D8436-95A6-4020-A030-D3DBC76B7B3F / ...&DE=721F3A58-F630-4E71-A865-179969B160DE | | |
| Archived Video/Audio (points to YouTube) | ...&DE=486BC724-4F0B-4E6E-96F2-F1EBDAFC6103 | 200 | 0 |
| YouTube transcripts (.txt, 2020 to 2021) | ...&DE=87A063C1-0EFD-4123-83D9-076BDC2A262E | 200 | .txt files |

- **How documents are linked:** each year page is a flat list of `<a>` elements pointing to `/vertical/sites/%7B…%7D/uploads/*.pdf`, with the link text as the label ("Agenda 4.7.26", "Backup Documents 4.21.26", "Minutes 3.3.26", "Proceedings for 1.6.26", "Personnel Report …"). There is no pagination. Filenames are inconsistent (`1.6.26_FINAL_Agenda.pdf`, `4.7.26_Agenda_NS.pdf`, `Back_Up_Binder(1).pdf`, `Back_up_Docs.pdf`), so parse the meeting date from the **link text** first and the filename second. Date formats are `M.D.YY` and "January 20, 2026". Other documents also land on these pages; for example `COLB_2025_audit_(1).pdf` sits on the Proceedings page.
- **Discovering new year pages:** re-crawl `/councilmeetings` and collect `index.asp?SEC=443D12D5-7EB6-47F2-B2CE-BA33F15CF0E2&DE=…` links whose text matches `City Council (Agendas|Proceedings)`.
- **Adapter:** `html_index` (two levels: hub → year pages → PDFs).
  - params: `start_url=https://www.longbeachny.gov/councilmeetings`, `follow_regex=index\.asp\?SEC=443D12D5-7EB6-47F2-B2CE-BA33F15CF0E2&(amp;)?DE=[0-9A-F-]{36}`, `follow_text_regex=(?i)council (agendas|proceedings)`, `doc_regex=` the generic uploads PDF regex, `doc_type_from_text={agenda:/agenda/i, backup:/back ?up/i, minutes:/minutes/i, proceedings:/proceedings/i, personnel:/personnel/i}`, `strip_href_whitespace=true`.
- **Schedule:** 1st and 3rd Tuesday at 7pm. Agendas are posted about the Friday before.
- **Confidence:** high.

### 1b. City meeting portal and calendar
- https://www.longbeachny.gov/meetingsandagendas (200). It links council, zoning and Civil Service meetings, plus an upcoming-events list of DE links under `SEC=3B2FBA1A-DF24-4F0C-9964-B8F658D4F579` (title plus date text).
- Council calendar: `index.asp?SEC=D78F124F-BC14-4D71-A79E-6794FF6243D5` (event DE pages give only "When:").
- Main events calendar: `index.asp?Type=B_EV&SEC={3B2FBA1A-DF24-4F0C-9964-B8F658D4F579}` (200). The grid view is mostly empty in the static HTML. Month navigation uses `&month=&year=` parameters, which robots disallows (`/*month*`).
- Civil Service meetings: `index.asp?Type=B_EV&SEC=%7B2319E37C-17E9-4082-842F-4E94FF312EE3%7D` (200; small page).
- **Adapter:** `html_index` on `/meetingsandagendas` (and the homepage) for event DE links `index\.asp\?SEC=3B2FBA1A-DF24-4F0C-9964-B8F658D4F579&(amp;)?DE=[0-9A-F-]{36}`, parsing the "Weekday, Month D, YYYY at H:MM PM" text. **No iCal.** Confidence: medium.
- There is also a downloadable annual calendar at `/2026calendar` (not fetched).

### 1c. Budgets, capital plans, audits, financial reports
The hub is https://www.longbeachny.gov/financials (200) and it links these subpages, all 200:

| Page | URL | Unique PDFs | Notes |
|---|---|---|---|
| Audited financial statements | https://www.longbeachny.gov/audit | 34 | FY2025 FS (`City_of_Long_Beach_New_York_2025_FS_Final.pdf`), Uniform Guidance/single audit, DOT assistance, Bonadio & Co required communications; grouped under "Financial YYYY" headings |
| Budgets (proposed/adopted) | https://www.longbeachny.gov/budget | 61 | Also links ClearGov digital budget books: https://city-long-beach-ny-cleardoc.cleargov.com/25654 (FY2027 Adopted) and https://city-long-beach-ny-cleardoc.cleargov.com/25476/1011644/d (FY2027 Proposed) |
| Capital Improvement Plans | https://www.longbeachny.gov/capitalplan | 17 | 5-year capital plans and slide decks |
| Financial & other reports | https://www.longbeachny.gov/reports | 15 | Fiscal conditions analysis, OSC reports, tax-levy override, water rates |
| Policies & procedures | https://www.longbeachny.gov/policies | 19 | Procurement, debt, fund balance, ADA, and others |
| Budget status reports | https://www.longbeachny.gov/scorecards | 42 | Mixed hosts: city uploads plus `http://debtdash.org/wp-content/uploads/2024/10/Long-Beach-Budget-Status-Report-*.pdf` |
| Strategic planning reports | https://www.longbeachny.gov/strategicplanning | 5 | 2020 to 2021 only; stale |

- **Adapter:** `html_index`, one source per page. `doc_regex` = uploads PDF regex, OR `https?://(www\.)?debtdash\.org/wp-content/uploads/.+\.pdf` for /scorecards. Fiscal year comes from the preceding heading text ("2026-2027", "Financial 2025"). Confidence: high.
- **ClearGov transparency center:** https://longbeachny.cleargov.com/ (200). It is a JS single-page app. Adapter: `manual`, or a later study of its JSON calls. **Debt Dash:** https://debtdash.org/long-beach/ (200, WordPress) is a third-party debt and budget dashboard. Use `manual` or `html_index`.

### 1d. Transparency portal, contracts, collective bargaining
- https://www.longbeachny.gov/transparency (200) is a hub with links to budget, audit, scorecards, contracts, policies, the FOIL form, the RFP list and the OSC corrective action plan.
- **Executed contracts:** https://www.longbeachny.gov/contracts (200 on retry; one attempt hit a connection reset). It lists contract PDFs, each with text fields for vendor, period begin/end, projected new solicitation and department. Adapter: `html_index` with the uploads PDF regex, and parse the trailing text into metadata. Confidence: high.
- Collective bargaining agreements: `index.asp?SEC=8086AF8B-66A2-42F1-AFC2-C9BB243627FA` (not fetched).

### 1e. RFPs / bids
- **City-hosted RFP list:** https://www.longbeachny.gov/index.asp?Type=B_EV&SEC={CA0462DB-C2CA-4A7A-A447-6FB4C8E1C5B5} (200). There are about 105 DE detail links (`index.asp?SEC=CA0462DB-C2CA-4A7A-A447-6FB4C8E1C5B5&DE=…`), with titles like "RFP#2025-159 ATM MACHINES (re-proposal)". Detail pages hold the RFP text and attachments. Adapter: `html_index` (list → DE pages → uploads PDFs). Confidence: medium-high.
- **Purchasing page:** https://www.longbeachny.gov/purchasing (200). It links the procurement policy PDF and BidNet Direct.
- **BidNet Direct:** https://www.bidnetdirect.com/new-york/city-of-long-beach-new-york/solicitations/open-bids?selectedContent=BUYER (200), plus `/closed-bids`. The static HTML showed **no solicitation links**: either nothing was open or the list is rendered client-side. Full documents need vendor registration. Adapter: `manual` (or a headless browser). Confidence: low.
- **Bid openings are also streamed on YouTube.** See section 3; the RSS feed shows titles like "Bid Opening: 10/8/2026 - 2027 Calendars".

### 1f. Public notices / legal notices
- https://www.longbeachny.gov/notices (200). It lists DE links `index.asp?SEC=5EDED959-D0CC-489B-9C46-A0A4BBBB838E&DE=…` with titles: emergency declarations, OSC "Report of Examination" legal notices, SWMP annual reports, water quality reports. It is **sparse and stale** (latest seen 06-14-2024). Adapter: `html_index` (follow DE links, then collect uploads PDFs). Confidence: medium. Note that legal notices are also published in the Herald (the paper of record).

### 1g. News / announcements
- `/news2` is **not** news; it is a "Welcome" page.
- The real news list is https://www.longbeachny.gov/index.asp?SEC=33BD7D65-42BC-45CB-ABAB-A6E2B4FB1BFB (200, "More Announcements" on the homepage). It shows about 10 latest items as `<h3><a class="item" href="/index.asp?SEC=33BD7D65-42BC-45CB-ABAB-A6E2B4FB1BFB&DE={guid}">Title</a></h3>` followed by a date and a teaser. There is **no pagination and no RSS**.
- The homepage (https://www.longbeachny.gov/) also shows the same items, plus a carousel of uploads JPG/PDF links.
- **Adapter:** `html_index` with `item_regex=index\.asp\?SEC=33BD7D65-42BC-45CB-ABAB-A6E2B4FB1BFB&(amp;)?DE=[0-9A-F-]{36}`. Dedupe on the DE GUID. Poll daily, because items roll off. Confidence: high.
- /public-relations (200) holds only contact info.

### 1h. Zoning Board of Appeals and Planning Board / ARB
- **ZBA:** https://www.longbeachny.gov/zoning (200; 46 unique PDFs on page). It has a "Search Meetings" list with one block per meeting, a DE link `index.asp?SEC=1D59BF10-5A3A-471C-83C8-AC8DB4231FF7&DE=…` ("Zoning Board Meeting Thursday, October 22, 2026 at 7:00 PM"), and inline PDFs `Calendar_M-D-YYYY*.pdf` (agenda, called the "calendar") and `Decisions_M-D-YYYY.pdf`.
  - Archive: https://www.longbeachny.gov/zoningarchive (200). DE pages under `SEC=9837D86B-6429-4ACA-8A32-2EB963C7E138` hold "ZONING BOARD CALENDAR AGENDAS 2024", "ZONING BOARD DECISIONS 2024", 2023 and so on.
  - Related: https://www.longbeachny.gov/530wbroadway (a contested case).
- **Planning Board (also covers ARB):** https://www.longbeachny.gov/planning (200; 46 unique PDFs). Meeting DEs are under `SEC=80F79735-C48C-4F39-8524-7B580B093312`. PDFs include `10-1-2026_ARB_Agenda.pdf`, `Decisions_10-1-2026.pdf`, `9-3-2026.pdf` (agenda) and `9-3-2026_PB_Minutes.pdf`.
- **Adapter:** `html_index`. Collect the uploads PDFs on the page and associate each with the nearest preceding meeting DE link or date text. Also crawl /zoningarchive → DE pages. Confidence: high.
- Other boards (/boards, DEs under `SEC=51A86353-…`) are mostly descriptions and vacancies, not minutes.

### 1i. Charter and code of ordinances
- **Hosted as a city PDF, not on ecode360, amlegal or Municode.** No ecode360 customer ID for Long Beach NY was found; the ecode360 hits were the Township of Long Beach **NJ**.
- Page: https://www.longbeachny.gov/index.asp?SEC=1A40DC3F-AB22-4EF5-8107-35B30A446295&DE=46CE5630-0337-4BAF-9880-017D43D760BB (200). It says "current as of April 7, 2026", and its PDFs are `Code_of_Ord_Sup_86b(1).pdf` and `Long_Beach_Code_of_Ordinances_-_Sup_87.pdf` (a bookmarked PDF). New legislation not yet codified is listed at the bottom of the page. The council page notes that the full text of new ordinances and local laws is in the meeting agendas.
- **Adapter:** `html_index` (watch for a new "Sup NN" filename). Not `ecode360`. Confidence: high.

### 1j. Other city sources
- FOIL online form: https://www.longbeachny.gov/index.asp?SEC=3C841025-39C7-416B-800A-E2AD05B85CC7 (200). See section 11.
- Genasys alerts (formerly Zonehaven/"Alert Long Beach"): https://alertlongbeachny.genasys.com/portal/en (200). It is a sign-up single-page app only, with **no public feed found**. Adapter: `manual`.
- Event-update newsletter: a Constant Contact signup (link on /events).

---

## 2. Charter / Code: see 1i
- **ecode360:** not used by Long Beach NY. `ecode360.com` returns 403 to curl (Cloudflare).

## 3. City YouTube channel
- Channel: https://www.youtube.com/channel/UCQZnJLJW2wI83_FZRc53-Wg. **Confirmed** as linked from /councilmeetings and the homepage. Legacy URL https://www.youtube.com/c/cityoflongbeachNYOFFICIAL (200). Channel title: "City of Long Beach, NY - OFFICIAL".
- **RSS works:** https://www.youtube.com/feeds/videos.xml?channel_id=UCQZnJLJW2wI83_FZRc53-Wg returned 200 `text/xml` with **15 entries**, the latest 2026-10-08. Content includes "City Council Meeting - 10/06/2026", "Planning Board Meeting - 10/01/2026" and several "Bid Opening: …" videos.
- **Adapter:** `youtube_channel` with params `channel_id=UCQZnJLJW2wI83_FZRc53-Wg`. Classify by title regex: `City Council Meeting - (\d{2}/\d{2}/\d{4})`, `Planning Board Meeting`, `Zoning Board`, `Bid Opening`. RSS only shows the last 15, so backfill needs the YouTube Data API or yt-dlp. Confidence: high.

## 4. Long Beach City School District: lbeach.org
**Platform:** **ParentSquare Smart Sites.** Assets come from `cdn.smartsites.parentsquare.com`, files from `files.smartsites.parentsquare.com/11147/`, and accordions use `ss-accordion` markup. The school-site subdomains are lbhs, lbms, east, west, lido and lindell .lbeach.org. Content is server-rendered in the HTML (accordion panels). It is **not** BoardDocs.
**Sitemap:** https://www.lbeach.org/sitemap.xml (200, 234 URLs; note the `https://www.sitemaps.org` namespace). **RSS/iCal:** none found on the main site.
**File URLs:** `https://files.smartsites.parentsquare.com/11147/<name>.pdf`. A sample download returned 200 `application/pdf`. Some URLs appear JSON-escaped (`files.smartsites.parentsquare.com\/11147\/…`), so unescape `\/`.

### 4a. Board of Education agendas, minutes, notices (PRIMARY)
- **Index page:** **https://www.lbeach.org/information-meetings-agendas-2** (200, about 474 KB, **1,108 PDF links on a single page**).
  - Its accordion sections have `ss-accordion-btn` headings: "2026-2027 Public Notices" … "2013-2014 Public Notices"; "2026-2027 Agendas" … "2017-2018 Agendas"; "2026-2027 Minutes" … "2007-2008 Minutes".
  - Each panel contains `<a href="https://files.smartsites.parentsquare.com/11147/….pdf">October 13, 2026</a>`. Link text is the meeting date, sometimes with a suffix such as "– Public Hearing".
  - Filenames are inconsistent: new ones look like `100926_10-13-26_boe_meeting_notice.pdf`, `041326_4-14-26_boe_agenda.pdf` and `090926_8-11-2026_board_of_ed_meeting_minutes.pdf`, while older ones are bare dates such as `june_26_2025_1.pdf` or `march_21_2012_executive_session.pdf`. **Doc type must come from the enclosing accordion heading**, not the filename.
  - https://www.lbeach.org/information-meetings-agendas (without -2) is the parent page (200), with no documents.
- **Adapter:** `html_index` with section-aware parsing: `start_url=https://www.lbeach.org/information-meetings-agendas-2`, `section_selector=.ss-accordion-record`, `section_title_selector=.ss-accordion-btn`, `doc_regex=https://files\.smartsites\.parentsquare\.com/11147/[^"'\s]+\.pdf`, `doc_type_from_section={notice:/Public Notices/, agenda:/Agendas/, minutes:/Minutes/}`, `date_from_link_text=true`, `crawl_delay=5`. Confidence: high.
- Board members page: https://www.lbeach.org/school-board (200).

### 4b. BOE meeting video
- Page: https://www.lbeach.org/boe-meeting-videos (200). It holds about 125 YouTube links (`youtube.com/watch?v=…`, `youtu.be/…`, `youtube.com/live/…`).
- **District YouTube channel: `UCzst8qfrB66Ur7JrZHfodyw`** (@longbeachschooldistrict5852). I resolved this through oEmbed `author_url` and the channel page `externalId`.
- RSS: https://www.youtube.com/feeds/videos.xml?channel_id=UCzst8qfrB66Ur7JrZHfodyw returned 200, with titles like "Long Beach School District BOE Meeting 10-13-2026".
- **Adapter:** `youtube_channel` with `channel_id=UCzst8qfrB66Ur7JrZHfodyw`, plus an optional `html_index` of /boe-meeting-videos for backfill. Confidence: high.

### 4c. Budget
- https://www.lbeach.org/budget-info-2025-2026 (200; it returned the same content as https://lbeach.org/budget-information-4, about 16 PDFs). It covers the 2026-27 budget: prelim budget, adopted budget, budget-prep presentations, property tax report card, three-part budget, administrative salary disclosure and tax-exempt property report.
- https://www.lbeach.org/budget-information and /budget-information-2 (200) are hub pages. https://www.lbeach.org/budget-archive-2 (200) is the archive and was not deeply inspected.
- **Adapter:** `html_index` with the ParentSquare file regex. Confidence: medium-high.
- District FOIL: https://www.lbeach.org/foil-requests embeds a PDF form at `https://files.smartsites.parentsquare.com/11147/012425_foil_request_form.pdf`. There is no online form.

## 5. NY State Comptroller (OSC)

### 5a. Audits (city plus school district)
- **Search listing:** https://www.osc.ny.gov/local-government/audits?combine=Long%20Beach (200). It is Drupal and server-rendered. The results include:
  - City: `/local-government/audits/city/2013/07/11/city-long-beach-financial-condition-and-cash-receipts-recreational-activities-2013m`, `/city/2019/12/11/city-long-beach-financial-condition-2019m-133`, `/city/2019/12/11/city-long-beach-payments-unused-leave-accruals-2019m-68`, annual **Budget Reviews** from 2014 through 2024 (`/city/2024/05/10/city-long-beach-budget-review-b24-7-4` …), and `/city/2025/02/21/city-long-beach-physical-accessibility-programs-and-services-s9-24-36`.
  - School: `/local-government/audits/school-district/2016/08/05/long-beach-city-school-district-controls-over-cash-receipts-2016m-145` and `/school-district/2024/08/09/long-beach-city-school-district-financial-management-2023m-147`.
- Detail pages link PDFs like `https://www.osc.ny.gov/files/local-government/audits/2024/pdf/long-beach-city-school-district-2023-147.pdf`.
- **Adapter:** `html_index` with `start_url=https://www.osc.ny.gov/local-government/audits?combine=Long%20Beach`, `follow_regex=^/local-government/audits/(city|school-district)/\d{4}/\d{2}/\d{2}/(city-long-beach|long-beach-city-school-district)[^"]*`, `doc_regex=/files/local-government/audits/.+\.pdf`. **Filter out** Long Beach in other states; the slug prefix handles that. Confidence: high.

### 5b. Fiscal Stress Monitoring System (FSMS)
- Current scores page: https://www.osc.ny.gov/local-government/fiscal-monitoring/local-government-and-school-fsms-scores (200).
  - Municipalities: https://www.osc.ny.gov/files/local-government/fiscal-monitoring/2025/excel/2025-munis-summary.xlsx (200 xlsx) and `/2025/excel/2025-munis-all-data-worksheet.xlsx`
  - Schools: https://www.osc.ny.gov/files/local-government/fiscal-monitoring/excel/2025-schools-summary.xlsx (200 xlsx) and `/excel/2025-schools-all-data-worksheet.xlsx`. Note that the schools path has **no year directory**.
- Historical: https://www.osc.ny.gov/local-government/fiscal-monitoring/historical-fiscal-stress-monitoring-system-data (200). It has xls/xlsx for 2012 to 2024, and the path patterns vary by year.
- Interactive lookup: http://wwe1.osc.state.ny.us/localgov/fiscalmonitoring/fsms.cfm (200).
- **Adapter:** `html_index` on the scores page, collecting `\.xlsx?$` links, then filtering rows for entity "Long Beach" (city, Nassau) and "Long Beach City School District". Confidence: high.

### 5c. Financial data for local governments (Open Book–style bulk CSV)
- Form page: https://wwe1.osc.state.ny.us/localgov/findata/financial-data-for-local-governments.cfm (200).
- **Bulk download:** POST to `https://wwe1.osc.state.ny.us/localgov/findata/detailedzip.cfm` returns a **302 to a static file**. The static files can be fetched directly:
  - **Cities, all years (rev/exp/balance sheet):** https://wwe1.osc.state.ny.us/localgov/findata/level3zip/city_all_years.zip (200, about 12.5 MB, last-modified 2026-10-02)
  - **School districts, all years:** https://wwe1.osc.state.ny.us/localgov/findata/level3zip/schooldistrict_all_years.zip (200, about 111 MB)
  - Counties, all years: https://wwe1.osc.state.ny.us/localgov/findata/level3zip/county_all_years.zip (200, about 18.9 MB)
  - All classes, single year: `level3zip/{YYYY}_all_classes.zip` (for example 2024_all_classes.zip; years 1995 to 2025)
  - Debt data: the POST with `radRebDebt=debt` redirects to `level3zip/_debtdetail.csv`
  - POST form fields: `radRebDebt=reb|debt`, `radSingleAllYear=scay|acsy|acay`, `selMuniClass=city|county|firedistrict|schooldistrict|town|village`, `selSingYear=YYYY`.
- **Adapter:** `json_api`/bulk file (closest fit: `manual` scheduled download). Fetch the zip, use `If-Modified-Since`, then filter the CSV by the municipality code or name for Long Beach. Confidence: high.
- Open Book New York (https://www.openbooknewyork.com/, 200) is the OSC transparency portal for state and local spending and contracts. It is an interactive app; use `manual`.

## 6. Nassau County
**Platform:** **CivicPlus** (www.nassaucountyny.gov: AgendaCenter, AlertCenter, RSSFeed.aspx, iCalendar).
- **RSS directory:** https://www.nassaucountyny.gov/rss.aspx (200). Feed URL pattern: `https://www.nassaucountyny.gov/RSSFeed.aspx?ModID={mod}&CID={cat}`.
  - News Flash, all: `RSSFeed.aspx?ModID=1&CID=All-newsflash.xml` (200, 107 items)
  - Legislative District news: `ModID=1&CID=Legislative-District-NN-…` (01-31 … 19-50). Long Beach is in a south-shore LD that I did not verify (likely LD 4, `ModID=1&CID=Legislative-District-04-34`, which had 0 items).
  - **Alert Center:** `ModID=63&CID=All-0`, `Emergency-Alerts-3`, `Health-Alerts-5`, `County-Home-Page-Alerts-7`, `Legislature-Meetings-4`, `County-Clerk-Alerts-6`, `Sheriffs-Department-8`. All returned 200 with 0 items at check time.
  - **Agenda Center:** `ModID=65&CID=All-0` (1 item), `County-Legislative-Full-Meetings-13`, `County-Legislative-Committee-Meetings-14`, `County-Legislature-Special-Meetings-17`, `Finance-Committee-8`, `Planning-Commission-12`, `Budget-Review-Committee-18`, and others.
  - Calendar: `ModID=58&CID=All-calendar.xml` (200, 15 items).
- **Agenda Center (Legislature):** https://www.nassaucountyny.gov/agendacenter (200). Categories are `id="catNN"`: 13 Full Meetings, 14 Committee, 17 Special, 18 Budget Review, 8 Finance, 12 Planning Commission, and others. File URLs:
  - agenda `https://www.nassaucountyny.gov/AgendaCenter/ViewFile/Agenda/_MMDDYYYY-ID`, with `?html=true` and `?packet=true` variants
  - minutes `/AgendaCenter/ViewFile/Minutes/_MMDDYYYY-ID`
  - search `/AgendaCenter/Search/?term=&CIDs=13` (200)
  - previous versions `/AgendaCenter/PreviousVersions/_MMDDYYYY-ID`
- Older years are in the Archive Center (not inspected).
- **Adapter:** `rss` for AgendaCenter/AlertCenter/NewsFlash, plus `html_index` on /agendacenter with `doc_regex=/AgendaCenter/ViewFile/(Agenda|Minutes)/_\d{8}-\d+`. Confidence: high.
- **iCal:** `https://www.nassaucountyny.gov/common/modules/iCalendar/iCalendar.aspx?feed=calendar&catID={id}` (catID=14 returned 200 `text/calendar`). Adapter: `ical`.
- Legistar: **not used**. The web API returns "LegistarConnectionString setting is not set up … nassaucountyny". Granicus was also not found (404).
- **Property / assessment:** Land Record Viewer https://lrv.nassaucountyny.gov/ (200, "LandRecord Lookup"; interactive). https://www.mynassauproperty.com/ (200). Adapter: `manual` (interactive). GIS: https://gis.nassaucountyny.gov/ (200).
- Open data: `data.nassaucountyny.gov` failed (proxy 502, probably nonexistent). I did not confirm an ArcGIS Hub URL; the guessed hub URL returns a generic "ArcGIS Hub" shell.

## 7. Long Beach Police Department: lbpd.com
- https://www.lbpd.com/ (200). Title: "City of Long Beach New York Police Department". Platform: **WordPress**.
- **Press releases RSS:** https://www.lbpd.com/category/press-releases/feed/ (200 `application/rss+xml`, 5 items; **the latest press release was May 23, 2024, so it is stale**). Site-wide feed: https://www.lbpd.com/feed/ (200).
- **WP REST API:** https://www.lbpd.com/wp-json/wp/v2/posts?per_page=100&_fields=date,link,title (200 JSON). The latest post is "Community Announcement – September 18, 2025". Sitemap: https://www.lbpd.com/wp-sitemap.xml (200).
- Press page: https://www.lbpd.com/press/. Category page: https://www.lbpd.com/category/press-releases/.
- **FOIL:** https://www.lbpd.com/foil-requests/ (200). Requests go by email to **FOIL@LONGBEACHNY.GOV** (subject "FOIL request Attn: Police Department"); Records Division phone is (516) 705-7313. The page cites §89(3)(a).
- **Adapter:** `rss` (site feed) or `json_api` (wp-json). Confidence: high (low freshness).

## 8. Events
- **City events:** see 1b. There is no iCal, so use `html_index`. The page also links a Constant Contact newsletter and an annual PDF calendar (/2026calendar).
- **Long Beach Public Library:** https://longbeachlibrary.org/ (200, WordPress). The events platform is **LibraryMarket "LibraryCalendar"** at https://longbeachpl.librarycalendar.com/ (200).
  - **iCal:** https://longbeachpl.librarycalendar.com/events/feed/ical (200 `text/calendar`, **927 VEVENTs**)
  - **RSS:** https://longbeachpl.librarycalendar.com/events/feed/rss (200)
  - **JSON:** https://longbeachpl.librarycalendar.com/events/feed/json (200). It even includes city events such as "City of Long Beach Bayfront Planning Community Meeting".
  - Adapter: `ical` (preferred) or `json_api`. Confidence: high.
  - Library board minutes and budget: https://longbeachlibrary.org/library-board-of-trustees/ (200; **176 PDF links** of the form `wp-content/uploads/YYYY/MM/YYYY_MMDD-MINUTES-DRAFT.pdf`). Budget: https://longbeachlibrary.org/budget-information/ (for example `FY-2027-APPROVED-budget.pdf`). Adapter: `html_index` with `doc_regex=https://longbeachlibrary\.org/wp-content/uploads/\d{4}/\d{2}/[^"]+\.pdf`. WP feed: https://longbeachlibrary.org/feed/.
- **Long Beach Chamber of Commerce:** https://thelongbeachchamber.com/events-calendar (200). The generator is "Cyber-CMS 3.0.1" (Cybernet agency). Events are server-rendered links `/events-calendar/<slug>` with date and venue text. There is no iCal or RSS. Adapter: `html_index` with `item_regex=^/events-calendar/[a-z0-9-]+$`. Confidence: medium.

## 9. Local news
- **Long Beach Herald (Herald Community Newspapers):** https://liherald.com/longbeach (200 **only with a full Chrome UA string**; 403 with a minimal UA). Platform: Creative Circle Media (story URLs `/longbeach/stories/<slug>,<id>`).
  - **RSS:** https://liherald.com/longbeach/browse.rss (200 `application/rss+xml`, 50 items, links prefixed `/longbeach/stories/`). It may contain all-paper items with Long Beach-prefixed links; I did not verify the section filtering. `https://liherald.com/browse.rss?section=longbeach` returned the same item set with generic links.
  - Google News sitemaps: `https://www.liherald.com/sitemaps/sitemaps-r2-googlenews-all-{1,2,3}.xml` (200).
  - Adapter: `rss` (browser UA header required), filtered by `/longbeach/stories/` or keyword. Confidence: medium.
- **Patch, Long Beach:** https://patch.com/new-york/longbeach (200). **RSS:** https://patch.com/feeds/aol/new-york/longbeach (200 `application/rss+xml`). `/new-york/longbeach/rss` returns 404. Adapter: `rss`. Confidence: high.

## 10. Transit / alerts / weather
- **LIRR Long Beach Branch:** GTFS `route_id=6` ("Long Beach Branch"). Stops: `113 LBH Long Beach` (40.59018,-73.66482), `99 IPK Island Park`, `155 ODE Oceanside`.
  - Static GTFS: https://rrgtfsfeeds.s3.amazonaws.com/gtfslirr.zip (200 zip, about 1.7 MB). http://web.mta.info/developers/data/lirr/google_transit.zip is identical.
  - GTFS-RT trip updates: https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/lirr%2Fgtfs-lirr (200 protobuf; **no API key needed**)
  - **Service alerts:** protobuf https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Flirr-alerts (200) and **JSON** https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Flirr-alerts.json (200). Filter `informed_entity.route_id=="6"` or `stop_id in {113, 99, 155}`. All-agency alerts: `camsys%2Fall-alerts.json`.
  - Adapter: `json_api`. Confidence: high.
- City NICE/Long Beach bus: only a PDF brochure on the city site (`1119-4013_City_of_Long_Beach_TRANSPO_BROCHURE_UPDATED.pdf`).
- **NWS:** the point 40.5884,-73.6579 resolves to the OKX office, grid 46,38, **forecast zone NYZ179 "Southern Nassau"**, county zone NYC059 and fire zone NYZ213.
  - Active alerts: https://api.weather.gov/alerts/active?zone=NYZ179 (200 GeoJSON), Atom at https://api.weather.gov/alerts/active.atom?zone=NYZ179 (200). Use `?zone=NYC059` for county-based products.
  - Forecast: https://api.weather.gov/gridpoints/OKX/46,38/forecast
  - Send a descriptive User-Agent with contact info. The legacy `alerts.weather.gov/cap/wwaatmget.php` failed through the proxy (502), so use api.weather.gov.
  - Adapter: `json_api` / `rss` (Atom). Confidence: high.
- **NY-Alert:** https://alert.ny.gov/ (200; registration portal). `www.nyalert.gov` failed through the proxy. No public feed was found. Use NWS plus the Nassau AlertCenter RSS instead. Adapter: `manual`.
- **City Genasys:** registration only (see 1j).

## 11. NY FOIL specifics
- **Public Officers Law §89(3)(a)** (verified text at nysenate.gov/legislation/laws/PBO/89): within **5 business days** of receiving a written request, the agency must make the record available, deny it in writing, or acknowledge receipt with an approximate date. That date is generally **within 20 business days** of the acknowledgement. If more time is needed, the agency must state in writing why and give a date certain within a reasonable time. Failure to comply counts as a denial.
- **§89(4)(a):** the requester may appeal a denial in writing **within 30 days**. The agency head or appeals officer must decide **within 10 business days** of receiving the appeal.
- Committee on Open Government (Dept. of State) gives guidance. The city links `https://www.dos.ny.gov/coog/foil2.html`.
- **City online FOIL form: YES.** https://www.longbeachny.gov/index.asp?SEC=3C841025-39C7-416B-800A-E2AD05B85CC7 (200, "Freedom of Information Request Form"). It has a department dropdown (Building, City Clerk, Comptroller, City Manager, Civil Service, Community Development, Corporation Counsel, Fire, IT, Police, Public Works, Purchasing, Recreation, Transportation, Zoning Board, Other) and a response-format option (paper/email/fax/media/inspection). There is also email: FOIL@longbeachny.gov (the LBPD page).
- **School district:** PDF form only (see 4c). The library had no FOIL page checked.

---

## Adapter summary

| # | Source | Adapter | Key URL |
|---|---|---|---|
| 1 | City Council agendas/backup | html_index (2-level) | https://www.longbeachny.gov/councilmeetings → DE A4D83CA4… (2026 agendas) |
| 2 | City Council proceedings/minutes | html_index (2-level) | DE B76545F3… (2026 proceedings) |
| 3 | City budget / capital / audit / reports / policies / scorecards / contracts | html_index | /budget /capitalplan /audit /reports /policies /scorecards /contracts |
| 4 | City RFPs | html_index | index.asp?Type=B_EV&SEC={CA0462DB-C2CA-4A7A-A447-6FB4C8E1C5B5} |
| 5 | City news | html_index | index.asp?SEC=33BD7D65-42BC-45CB-ABAB-A6E2B4FB1BFB |
| 6 | City notices | html_index | /notices |
| 7 | ZBA / Planning / ARB | html_index | /zoning, /zoningarchive, /planning |
| 8 | City code | html_index (watch PDF) | index.asp?SEC=1A40DC3F…&DE=46CE5630… |
| 9 | City sitemap (change detection) | sitemap | /sitemap.xml (300 URLs, lastmod) |
| 10 | City YouTube | youtube_channel | UCQZnJLJW2wI83_FZRc53-Wg |
| 11 | LBCSD BOE agendas/minutes/notices | html_index (section-aware) | https://www.lbeach.org/information-meetings-agendas-2 |
| 12 | LBCSD YouTube | youtube_channel | UCzst8qfrB66Ur7JrZHfodyw |
| 13 | LBCSD budget | html_index | /budget-info-2025-2026, /budget-archive-2 |
| 14 | OSC audits | html_index | osc.ny.gov/local-government/audits?combine=Long%20Beach |
| 15 | OSC FSMS | html_index (xlsx) | …/local-government-and-school-fsms-scores |
| 16 | OSC financial data | manual/bulk zip | …/findata/level3zip/city_all_years.zip, schooldistrict_all_years.zip |
| 17 | Nassau Agenda Center / alerts / news | rss + html_index | RSSFeed.aspx?ModID=65/63/1… |
| 18 | Nassau calendar | ical | common/modules/iCalendar/iCalendar.aspx?feed=calendar&catID=… |
| 19 | LBPD | rss / json_api | lbpd.com/feed/, wp-json |
| 20 | Library events | ical | longbeachpl.librarycalendar.com/events/feed/ical |
| 21 | Library board minutes/budget | html_index | longbeachlibrary.org/library-board-of-trustees/ |
| 22 | Chamber events | html_index | thelongbeachchamber.com/events-calendar |
| 23 | Herald | rss (browser UA) | liherald.com/longbeach/browse.rss |
| 24 | Patch | rss | patch.com/feeds/aol/new-york/longbeach |
| 25 | LIRR alerts | json_api | api-endpoint.mta.info/…/camsys%2Flirr-alerts.json (route 6) |
| 26 | NWS | json_api / rss | api.weather.gov/alerts/active?zone=NYZ179 |
| 27 | ClearGov, BidNet, Genasys, NY-Alert, Nassau LRV, Open Book | manual | |

---

## Gaps / could not verify
1. **longbeachny.gov robots.txt disallows all generic user agents** (`User-agent: *` / `Disallow: /`). Every city adapter above technically conflicts with it. This needs a policy decision: request permission or whitelisting from the city, or keep fetches minimal and targeted.
2. No RSS or iCal on longbeachny.gov. The news list shows about 10 items with no pagination, so the history is not reachable from the list. The city sitemap is capped at 300 URLs and holds no PDFs.
3. The main city event calendar (`Type=B_EV`) renders an almost empty grid in static HTML. Month navigation URLs contain "month" and are robots-disallowed. Upcoming events are available only through the list on /meetingsandagendas and the homepage.
4. **BidNet Direct** open-bids page showed no solicitation links in static HTML (JS-rendered or none open). Documents need registration.
5. ClearGov (longbeachny.cleargov.com and cleardoc budget books) is a single-page app. I did not reverse-engineer its JSON API.
6. **Nassau County:** no Legislature video archive was found (not Granicus, not Legistar). I did not confirm which Legislative District covers Long Beach (likely LD 4). No open-data portal was confirmed. Property and assessment data are interactive only (LRV, mynassauproperty.com). AlertCenter feeds were empty at check time, so their format with items is unverified.
7. **Herald RSS** at /longbeach/browse.rss may not be section-filtered. It needs a browser-like UA, or it returns 403.
8. NY-Alert and the city Genasys portal: no public machine-readable feed found.
9. LBPD press releases are stale (latest May 2024). wp-json posts run through Sept 2025.
10. I did not test the OSC FSMS spreadsheets for the exact Long Beach row names or codes, or the OSC financial-data CSV entity codes. The zip contents were not downloaded (only HEAD checked, except for the LIRR GTFS).
11. Other city boards (Ethics, Civil Service Commission, ARB minutes outside Planning, and others) appear to post few or no minutes online.
12. lbeach.org /budget-archive-2 and older budget pages were not fully enumerated. School district audits by external CPAs (not OSC) were not located.
