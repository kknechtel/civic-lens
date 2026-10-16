# Adding a municipality

A municipality is one YAML file. This guide walks through writing one, using
`long-beach-ny` as the worked example.

## 1. Inventory the sources (about an hour)

For each government that publishes records about the town, find where the
documents live and how they are linked:

| Look for | Typical location |
|---|---|
| Council/board agendas and minutes | Town site "Meetings", "Agendas", "Clerk" pages; Granicus, Legistar, CivicPlus AgendaCenter, BoardDocs, Municode Meetings |
| Budgets, audits, financial statements | Finance or Comptroller pages; the state comptroller or local-finance agency |
| Code of ordinances | eCode360, Municode, American Legal, or a PDF on the town site |
| School district | District site board page; BoardDocs; state education department |
| Meeting video | YouTube channel ID (`UC…`) |
| Events | iCal feeds (libraries almost always have one) |

For each candidate site, check `https://<host>/robots.txt` first. If it
disallows crawlers (`User-agent: *` / `Disallow: /`), you need the site
owner's permission before collecting from it. See step 5.

Open the page source and note how documents are linked: plain `<a href="…pdf">`
links, links grouped under headings (accordions or year sections), or links to
detail pages.

## 2. Write the profile

Create `municipalities/<town>-<state>/municipality.yaml`:

```yaml
name: City of Long Beach            # legal name
short_name: Long Beach              # used in titles, prompts, UI
government_type: city               # city | town | village | borough | township
state: NY                           # loads municipalities/_states/ny.yaml
county: Nassau
fiscal_year:
  start_month: 7                    # 1 = calendar year
  school_start_month: 7
branding:
  app_name: Long Beach Civic Lens
  theme_color: "#1f4e79"
  public_origin: https://records.example.org

entities:                           # library groups; FIRST = the primary government
  - key: long_beach
    label: City of Long Beach
    short: City
    kind: municipal                 # municipal | school | county | state | special_district | regional | other
    description: City of Long Beach, NY — City Council, City Manager, ...   # shown to the AI
    aliases: [long beach, lbpd]     # letterhead names, acronyms

sources:
  - id: lbcsd_board                 # stable — stored on every document; don't rename later
    label: Board of Education agendas, minutes & notices
    adapter: html_index
    entity: lbcsd
    params: { ... }                 # see adapter reference below
```

Optional sections: `ai` (background facts and analytical notes for the chat
model), `search` (a glossary of local acronyms, department aliases, and
`earliest_year`), `events.venues`, and `features` (turn UI sections on or off).

## 3. Adapter reference

### `html_index`: crawl pages and collect document links

```yaml
params:
  start_urls: [https://www.lbeach.org/information-meetings-agendas-2]
  link_pattern: 'files\.smartsites\.parentsquare\.com/11147/.+\.pdf'  # default: common doc extensions
  allow_domains: [files.smartsites.parentsquare.com]  # file hosts besides the start page's own
  follow_pattern: 'index\.asp\?SEC=443D12D5.*&DE='    # sub-pages to crawl (e.g. one page per year)
  max_depth: 1
  max_pages: 200
  group_selector: li.ss-accordion-record              # sections whose heading prefixes titles
  group_heading_selector: h3.ss-accordion-heading
  doc_type_rules:                                     # first match wins; tested on heading + title + filename
    - {pattern: 'minutes', doc_type: minutes}
    - {pattern: 'agenda', doc_type: agenda}
  title_from: row                                     # use the table row/list item text when link text is "Download"
  min_year: 2020                                      # split recent vs archive sources with min_year/max_year
```

### `youtube_channel`

```yaml
params: {channel_id: UCQZnJLJW2wI83_FZRc53-Wg, title_pattern: 'council|board', meeting_body: City Council}
```

Needs `YOUTUBE_API_KEY`, because YouTube's robots.txt disallows the RSS feed.
Add `backfill: true` for the first run to list the whole channel.

### `rss`, `sitemap`, `ecode360`

```yaml
adapter: rss       # params: {url, link_pattern}
adapter: sitemap   # params: {url, link_pattern, max_urls}
adapter: ecode360  # params: {customer: AT0153}  (Cloudflare-protected; needs Chrome)
```

### Events (`events.venues`)

`ical` works for most library and municipal calendars. `squarespace`,
`tribe_events` and `bandsintown` cover common venue platforms. `html` and
`playwright` take a named parser for bespoke sites.

### Something else?

Add a `SourceAdapter` subclass in `api/services/sources/`, register it in
`registry.ADAPTERS`, and add a test with a recorded HTML fixture.

## 4. Check it

```bash
cd api
python -m scripts.check_profile long-beach-ny            # schema only
python -m scripts.check_profile long-beach-ny --live     # fetch index pages, no downloads
```

```
✓ lbcsd_board [html_index → lbcsd] 513 items in 5.9s  {'minutes': 180, 'notice': 172, 'agenda': 160}
! city_council [html_index → long_beach] 0 items
    robots.txt on www.longbeachny.gov disallows CivicLens; skipping. Ask the site owner for permission ...
```

Iterate until every source finds roughly what you counted by hand and the
doc_type mix looks right. Then do a sampled end-to-end run:

```bash
SCRAPER_MAX_DOCS_PER_SOURCE=10 python -m scripts.scheduled_scrape
```

## 5. When robots.txt says no

Ask. Most clerks are glad to help once you explain that you index public
records, crawl slowly, and identify yourself. Record the answer:

```yaml
  - id: city_council
    respect_robots: false
    permission_note: "City Clerk <name>, email 2026-10-20: OK to index /councilmeetings and /vertical/sites uploads"
```

The profile won't validate with `respect_robots: false` and no note. Don't spoof
a search-engine User-Agent to get around a block.

## 6. New state?

Add `municipalities/_states/<code>.yaml` with:

- the public-records law: name, citation and response deadlines;
- the oversight agencies (state comptroller, local-finance division,
  education department);
- `ai_notes`: accounting-basis differences, tax caps and statutory limits the
  assistant must respect;
- a `glossary` of agency acronyms.

See `nj.yaml` and `ny.yaml`.
