"""
Configuration for Atlantic Highlands Town Repository Scraper.
Adapted for S3 storage (no Box dependency).
"""

# ─── Website sources ────────────────────────────────────────────────
SOURCES = {
    "ahnj": {
        "base_url": "https://www.ahnj.com",
        "description": "Borough of Atlantic Highlands Official Site",
        # `pages_to_crawl` are visited on every run (current activity).
        # `historical_pages` are only visited when historical=True (the default for
        # manual runs from the UI; recent_only mode skips them).
        "pages_to_crawl": [
            # Government & Administration
            "/ahnj/Government",
            "/ahnj/Government/Budget%20Information/",
            "/ahnj/Government/Borough%20Ordinances/",
            "/ahnj/Government/Government%20Records/",
            "/ahnj/Government/Government%20Records/Most%20Referenced%20Ordinances/",
            # Meetings & Agendas (current)
            "/ahnj/Meetings%20and%20Agendas/",
            "/ahnj/Agendas/",
            # Planning Board — current year + the two prior years (where revisions
            # to recent meetings still land). Older years are in historical_pages.
            "/ahnj/Departments/Planning%20Board/",
            "/ahnj/Departments/Planning%20Board/Agendas%2C%20Minutes%2C%20Resolutions%2C%20Recordings%20and%20Annual%20Reports/",
            "/ahnj/Departments/Planning%20Board/Agendas%2C%20Minutes%2C%20Resolutions%2C%20Recordings%20and%20Annual%20Reports/2024/",
            "/ahnj/Departments/Planning%20Board/Agendas%2C%20Minutes%2C%20Resolutions%2C%20Recordings%20and%20Annual%20Reports/2025/",
            "/ahnj/Departments/Planning%20Board/Agendas%2C%20Minutes%2C%20Resolutions%2C%20Recordings%20and%20Annual%20Reports/2026/",
            "/ahnj/Departments/Planning%20Board/_Agendas%20and%20Minutes/",
            "/ahnj/Departments/Planning%20Board/_Agendas%20and%20Minutes/2024/",
            "/ahnj/Departments/Planning%20Board/_Agendas%20and%20Minutes/2025/",
            "/ahnj/Departments/Planning%20Board/_Agendas%20and%20Minutes/2026/",
            # Departments
            "/ahnj/Departments",
            "/ahnj/Departments/Building%20Department/",
            "/ahnj/Departments/Finance%20Department/",
            "/ahnj/Departments/Zoning%20Office/",
            "/ahnj/Departments/Affordable%20Housing/",
            "/ahnj/Departments/Tax%20Assessor/",
            "/ahnj/Departments/Tax%20Collector/",
            "/ahnj/Departments/Police%20Department/",
            "/ahnj/Departments/Fire%20Department/",
            # Other
            "/ahnj/Community",
            "/ahnj/Court",
            "/ahnj/Emergency",
            "/ahnj/Events/",
            "/ahnj/Recreation/",
            "/ahnj/Forms/",
            "/ahnj/Forms/Planning%20and%20Zoning/",
            "/ahnj/Other%20Links/BIDS%20and%20RFPs/",
            "/ahnj/Announcements/",
            # Events calendar - has all meeting schedules with times
            "/ahnj/Upcoming%20Events/",
            "/ahnj/Upcoming%20Events/#gsc.tab=0",
            "/ahnj/Events/Community%20Events/",
        ],
        "historical_pages": [
            # 2005-2013 Council Agenda archives — frozen content. Skipped in
            # recent_only mode.
            "/ahnj/Agendas/Archives/",
            "/ahnj/Agendas/Archives/2005/",
            "/ahnj/Agendas/Archives/2006/",
            "/ahnj/Agendas/Archives/2007/",
            "/ahnj/Agendas/Archives/2008/",
            "/ahnj/Agendas/Archives/2009/",
            "/ahnj/Agendas/Archives/2010/",
            "/ahnj/Agendas/Archives/2011/",
            "/ahnj/Agendas/Archives/2012/",
            "/ahnj/Agendas/Archives/2013/",
            # Older Planning Board archive tree
            "/ahnj/Departments/Planning%20Board/Agendas%2C%20Minutes%2C%20Resolutions%2C%20Recordings%20and%20Annual%20Reports/2022/",
            "/ahnj/Departments/Planning%20Board/Agendas%2C%20Minutes%2C%20Resolutions%2C%20Recordings%20and%20Annual%20Reports/2023/",
            "/ahnj/Departments/Planning%20Board/_Agendas%20and%20Minutes/Archives/",
        ],
    },
    "ecode360": {
        "base_url": "https://ecode360.com",
        "description": "Atlantic Highlands Document Repository (Agendas, Minutes, Codes)",
        "pages_to_crawl": [
            "/AT0153/documents/Agendas",
            "/AT0153/documents/Budgets",
            "/AT0153/documents/Comprehensive_Plans",
            "/AT0153/documents/Legislation",
            "/AT0153/documents/Minutes",
            "/AT0153/documents/Misc._Documents",
            "/AT0153/documents/Resolutions",
            "/AT0153",
        ],
    },
    "tridistrict": {
        "base_url": "https://www.tridistrict.org",
        "description": "Henry Hudson Regional School District (Tri-District)",
        "pages_to_crawl": [
            "/",
            # BOE Agendas & Minutes — by school year
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2675269",  # 2025-2026
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2593279",  # 2024-2025
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2593280",  # Archive
            # Board of Education main + supporting pages
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2432532",  # BOE main
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2593229",  # Meeting Information
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2593225",  # Board Members
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2593533",  # Board Committees
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2593278",  # Board Policies
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2580048",  # School Budget Information
            # District-wide
            "/apps/pages/index.jsp?uREC_ID=3733525&type=d&pREC_ID=2432536",  # Strategic Planning Documents
            "/apps/pages/index.jsp?uREC_ID=3733531&type=d&pREC_ID=2432534",  # Curriculum & Instruction
            "/apps/pages/index.jsp?uREC_ID=3733531&type=d",                  # Curriculum dept root
        ],
        "school_sites": [
            "https://ahes.tridistrict.org",
            "https://hes.tridistrict.org",
            "https://hhrs.tridistrict.org",
        ],
        # Per-school deep-link landing pages (Performance Reports, etc.)
        "school_pages": [
            # Henry Hudson Regional (the operating consolidated district as of 7/1/2024)
            "https://hhrs.tridistrict.org/apps/pages/index.jsp?uREC_ID=3758135&type=d&pREC_ID=2439311",  # Performance Report
            # Atlantic Highlands Elementary (predecessor, AHSD/0130)
            "https://ahes.tridistrict.org/apps/pages/index.jsp?uREC_ID=3733534&type=d&pREC_ID=2432548",  # Performance Report
            # Highlands Elementary (predecessor, HSD)
            "https://hes.tridistrict.org/apps/pages/index.jsp?uREC_ID=3763022&type=d&pREC_ID=2440904",   # Performance Report
        ],
    },
    "highlands_borough": {
        "base_url": "https://highlandsnj.gov",
        "description": "Borough of Highlands NJ (HHRSD constituent town)",
        "pages_to_crawl": [
            "/",
            "/regionalization-survey/",
            "/category/council-agendas/",
        ],
        "direct_downloads": [
            "https://highlandsnj.gov/wp-content/uploads/2024/12/Public-Letter-on-Regionalization.pdf",
            "https://highlandsnj.gov/wp-content/uploads/2025/06/Decision-on-Sea-Bright-Amended-Petition-June-2025-2.pdf",
        ],
    },
    "highlands_meetings": {
        "base_url": "https://highlands-nj.municodemeetings.com",
        "description": "Highlands Borough Council — Municode meeting portal (agendas/packets)",
        "pages_to_crawl": ["/"],
    },
    "opra": {
        "base_url": "https://opramachine.com",
        "description": "OPRAmachine - Crowdsourced OPRA Requests for Atlantic Highlands",
        "api_url": "https://opramachine.com/body/atlantic_highlands.json",
        "pages_to_crawl": [
            "/body/atlantic_highlands",
        ],
    },
    "police": {
        "base_url": "https://spotcrime.com",
        "description": "Police/Crime Data - SpotCrime, CrimeMapping, Nixle",
        "pages_to_crawl": [
            # SpotCrime
            "https://spotcrime.com/nj/atlantic+highlands",
            # CrimeMapping (may need Selenium)
            "https://www.crimemapping.com/map/nj/atlantichighlands",
            # Nixle/Rave alerts for AH PD
            "https://www.nixle.com/atlantic-highlands-police-department/",
            # AH PD on borough website
            "https://www.ahnj.com/ahnj/Departments/Police%20Department/",
        ],
    },
    "fire": {
        "base_url": "https://web.pulsepoint.org",
        "description": "Fire/EMS Data - PulsePoint, Monmouth County OEM",
        "pages_to_crawl": [
            # PulsePoint web viewer
            "https://web.pulsepoint.org/",
            # AH Fire Department on borough site
            "https://www.ahnj.com/ahnj/Departments/Fire%20Department/",
            # Monmouth County OEM
            "https://www.co.monmouth.nj.us/page.aspx?ID=2434",
        ],
    },
    "county": {
        "base_url": "https://www.monmouthcountyclerk.com",
        "description": "Monmouth County Property Records, Tax Data, Clerk Archives",
        "pages_to_crawl": [
            # County clerk archives for AH
            "https://www.monmouthcountyclerk.gov/archives/record-groups/municipal-records/atlantic-highlands/",
            # Property records search
            "https://oprs.co.monmouth.nj.us/oprs/clerk/clerkhome.aspx?op=basic",
        ],
    },
    "census": {
        "base_url": "https://api.census.gov",
        "description": "US Census ACS Data for Atlantic Highlands",
        "api_config": {
            "state_fips": "34",
            "county_fips": "025",
            "place_fips": "01960",
            "full_fips": "3402501960",
            # Variables: population, median income, median home value, poverty rate, etc.
            "variables": [
                "B01003_001E",  # Total population
                "B19013_001E",  # Median household income
                "B25077_001E",  # Median home value
                "B17001_002E",  # Below poverty level
                "B01002_001E",  # Median age
                "B25001_001E",  # Total housing units
                "B25003_002E",  # Owner occupied
                "B25003_003E",  # Renter occupied
            ],
        },
    },
    "nj_state": {
        "base_url": "https://www.nj.gov",
        "description": "NJ State Data Sources (ACFR, DLGS, Court Opinions)",
        "direct_downloads": [
            # ── NJ DOE School ACFR reports — verified years 18-24 only.
            # 0130 = Atlantic Highlands SD (HHRSD predecessor)
            # 2120 = Henry Hudson Regional SD (HHRSD predecessor)
            # 2160 = Highlands SD (HHRSD third predecessor)
            # Years 15-17 return 404 — removed.
            *[f"https://www.nj.gov/education/finance/fp/acfr/search/{y}/0130.pdf" for y in (18, 19, 20, 21, 22, 23, 24)],
            *[f"https://www.nj.gov/education/finance/fp/acfr/search/{y}/2120.pdf" for y in (18, 19, 20, 21, 22, 23, 24)],
            *[f"https://www.nj.gov/education/finance/fp/acfr/search/{y}/2160.pdf" for y in (18, 19, 20, 21, 22, 23, 24)],
            # ── NJ DCA Division of Local Government Services
            # User-Friendly Budget Database (single .xlsm, all NJ municipalities incl. Atlantic Highlands)
            "https://www.nj.gov/dca/dlgs/programs/mc_budget_docs/UFB%20Database%20-%20FINAL.xlsm",
            # Sea Bright litigation court opinions
            "https://www.njcourts.gov/system/files/court-opinions/2024/a0716-23.pdf",
            "https://www.njcourts.gov/system/files/court-opinions/2025/a_68_24.pdf",
            # Community letters on Sea Bright
            "https://www.ahnj.com/ahnj/Announcements/LettertoCommnity-SeaBrightDecision.pdf",
            "https://highlandsnj.gov/wp-content/uploads/2024/12/Public-Letter-on-Regionalization.pdf",
            "https://highlandsnj.gov/wp-content/uploads/2025/06/Decision-on-Sea-Bright-Amended-Petition-June-2025-2.pdf",
            # Master Plan
            "https://www.ahnj.com/ahnj/Departments/Planning%20Board/Master%20Plan%20(Adopted%202019).pdf",
            "https://www.ahnj.com/ahnj/Departments/Planning%20Board/Zoning%20Map.pdf",
            # Housing Plan
            "https://www.ahnj.com/ahnj/_zumu_user_doc_cache/Housing_Plan_Element_and_Fair_Share_Plan___Atlantic_Highlands___Adopted_June_12_2025.pdf",
        ],
    },
}

# ─── File types to download ────────────────────────────────────────
DOWNLOAD_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".csv", ".txt", ".rtf",
}

# Skip these - too large or not useful as documents
SKIP_EXTENSIONS = {
    ".wav", ".wma", ".mp3", ".mp4", ".avi", ".mov", ".wmv",
    ".zip", ".rar", ".7z", ".tar", ".gz",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".ico",
}

# Meeting recording audio/video — the *recordings* crawlers explicitly look for
# these (the general document crawlers still skip them via SKIP_EXTENSIONS).
RECORDING_EXTENSIONS = {
    ".wma", ".wav", ".m4a", ".mp3", ".mp4", ".mov", ".dcr",
}

# ─── Meeting-recording sources ─────────────────────────────────────
# Last-12-months filter is applied by the crawler at scrape time so we don't
# pull every back-year on every run. Set RECORDINGS_BACKFILL=full to disable.
RECORDING_SOURCES = {
    "ah_recordings": {
        "base_url": "https://www.ahnj.com",
        "description": "Atlantic Highlands meeting recordings (Council, Planning, Harbor)",
        "bodies": [
            {
                "name": "Borough Council",
                "path": "/ahnj/Administration/Council%20Recordings/",
                "doc_type": "recording_council",
            },
            {
                "name": "Planning Board",
                # Year folders live under _Agendas and Minutes/<YYYY>/Recordings/;
                # the parent index page lists them. Crawler walks year subfolders.
                "path": "/ahnj/Departments/Planning%20Board/_Agendas%20and%20Minutes/",
                "doc_type": "recording_planning",
                "year_subfolders": True,
            },
            {
                "name": "Harbor Commission",
                "path": "/ahnj/AH%20Harbor/Harbor%20Meeting%20Recordings/",
                "doc_type": "recording_harbor",
                "year_subfolders": True,
            },
        ],
    },
    "hhrsd_recordings": {
        "base_url": "https://www.tridistrict.org",
        "description": "Henry Hudson Regional School District BOE meeting recordings (YouTube)",
        # The "Board Meeting Recordings" page on tridistrict.org is a flat list
        # of anchors → youtu.be/<id>, grouped by school year. We scrape anchors.
        "pages_to_crawl": [
            "/apps/pages/index.jsp?uREC_ID=3733528&type=d&pREC_ID=2596745",
        ],
        "doc_type": "recording_school_board",
    },
}

# ─── Scraper settings ──────────────────────────────────────────────
REQUEST_DELAY = 1.5
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
# Identify ourselves honestly. Site operators can find the project and
# contact the deployer; robots.txt rules are matched on ROBOTS_AGENT.
import os as _os
ROBOTS_AGENT = "CivicLens"
USER_AGENT = _os.getenv(
    "SCRAPER_USER_AGENT",
    "CivicLens/0.1 (+https://github.com/kknechtel/civic-lens; public-records indexer)",
)
# robots.txt is advisory and these are public records, so it is not consulted
# by default. A deployment can opt in (crawl.respect_robots: true, or
# RESPECT_ROBOTS_TXT=true). Either way we identify honestly and rate-limit.
RESPECT_ROBOTS_TXT = _os.getenv("RESPECT_ROBOTS_TXT", "false").lower() == "true"
