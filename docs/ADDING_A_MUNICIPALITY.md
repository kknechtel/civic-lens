# Adding a town, county or state

Each jurisdiction is one YAML file in a tree. This guide walks through adding
a town, using Long Beach, NY (`us/ny/nassau/long-beach`) as the worked example.

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

Government sites often disallow crawlers in robots.txt. That file is advisory
and these are public records, so CivicLens doesn't consult it by default (step 5).
What does matter is technical blocking (Cloudflare challenges, rate limits): use
the `ecode360`-style browser adapter for the first, a slower `crawl.min_delay_seconds`
for the second.

Open the page source and note how documents are linked: plain `<a href="…pdf">`
links, links grouped under headings (accordions or year sections), or links to
detail pages.

## 2. Write the jurisdiction file

Profiles form a tree: `jurisdictions/us/<state>/<county>/<place>/jurisdiction.yaml`.
A town inherits its county's and state's rules (records law, bid thresholds,
accounting notes, crawl policy) and any **source templates** they define.
Create the county and state files first if they don't exist yet; a county
file can be as small as its name and level.

```yaml
# jurisdictions/us/ny/nassau/long-beach/jurisdiction.yaml
name: City of Long Beach            # legal name
short_name: Long Beach              # used in titles, prompts, UI
level: municipality                 # state | county | municipality
government_type: city               # city | town | village | borough | township
aliases: [long-beach-ny]            # optional DEPLOYMENT shortcuts
include: []                         # neighbours to index too (e.g. us/nj/monmouth/highlands)
fiscal_year:
  start_month: 7                    # 1 = calendar year (inherited from the state if omitted)
branding:
  theme_color: "#1f4e79"
template_vars:                      # fills {placeholders} in state/county source_templates
  osc_search: Long%20Beach
  osc_slug: long-beach

entities:                           # governments whose records live here; FIRST = this jurisdiction
  - key: long_beach                 # omit entities entirely to get one derived from the name
    label: City of Long Beach
    short: City
    kind: municipal                 # municipal | school | county | state | special_district | regional | other
    description: City of Long Beach, NY — City Council, City Manager, ...   # shown to the AI
    aliases: [long beach, lbpd]
  - key: lbcsd
    label: Long Beach City School District
    short: LBCSD
    kind: school

sources:
  - id: city_council                # stable — stored on every document; don't rename later
    label: City Council agendas & proceedings
    adapter: html_index
    entity: long_beach
    params: { ... }
```

Optional sections: `ai` (background facts and notes for the chat model),
`search` (glossary, department aliases), `events.venues`, `features`,
`crawl`, and `integrity` (rule thresholds; usually set at state level).

**Deploying.** Set `DEPLOYMENT` to the path (or an alias). Root it at
`us/ny/nassau` to cover the county plus every town beneath it, or at `us/ny`
for the whole state.

**Source templates** (state or county level) run once per municipality that
supplies their variables:

```yaml
source_templates:
  - id: osc_audits
    adapter: html_index
    entity_kind: municipal          # which of the town's entities owns the documents
    params:
      start_urls: ["https://www.osc.ny.gov/local-government/audits?combine={osc_search}"]
      link_pattern: 'osc\.ny\.gov/files/local-government/audits/.+{osc_slug}.*\.pdf'
```

`{name}`, `{short_name}`, `{slug}`, `{state}` and `{county}` are always
available. A town missing a variable simply doesn't get the source, and
`check_profile` reports it.

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
python -m scripts.check_profile --all                    # every jurisdiction, as a deployment root
python -m scripts.check_profile long-beach-ny --live     # fetch index pages, no downloads
```

```
✓ lbcsd_board [html_index → lbcsd] 513 items in 5.9s  {'minutes': 180, 'notice': 172, 'agenda': 160}
! city_council [html_index → long_beach] 0 items
    HTTP 403 on www.longbeachny.gov … (blocked; try the browser adapter)
```

Iterate until every source finds roughly what you counted by hand and the
doc_type mix looks right. Then do a sampled end-to-end run:

```bash
SCRAPER_MAX_DOCS_PER_SOURCE=10 python -m scripts.scheduled_scrape
```

## 5. Crawl policy

robots.txt is not consulted by default: public records are public. A deployment
can opt in with `crawl.respect_robots: true` (inherited down the tree, overridable
per source) or `RESPECT_ROBOTS_TXT=true`. Whatever the policy, the crawler sends
its honest User-Agent, rate-limits (`crawl.min_delay_seconds`) and backs off on
429s. `permission_note` remains available to record any arrangement with a site owner.

## 6. New state?

Create `jurisdictions/us/<st>/jurisdiction.yaml` with `level: state`, `code`, and:

- `records_law`: name, citation and response deadlines;
- `oversight`: state comptroller, local-finance division, education department;
- `ai_notes`: accounting basis, tax caps and statutory limits the assistant must respect;
- `glossary`: agency acronyms;
- `integrity`: bid thresholds, emergency-procurement statute, change-order
  cap, pay-to-play rules, each with a `citation`;
- `source_templates`: state-hosted sources that exist for every town (audits,
  budgets, campaign finance);
- `crawl`: the default crawl policy.

See `us/nj` and `us/ny`.
