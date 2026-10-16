# Long Beach — private site on the Atlantic Highlands EC2 box

`https://longbeach.ahnj.info`, invite-only. Its own database, process, secrets
and files; it shares only the machine (and, by default, the RDS instance) with
Atlantic Highlands. Every Long Beach service is memory-capped so it is killed
before `ah-api` ever is.

| | |
|---|---|
| Code | `/opt/long-beach` (this repo) |
| Config | `/etc/long-beach.env` (created by `setup.sh`; root, 600) |
| API | `lb-api.service` → 127.0.0.1:8102 (≤700 MB) |
| Web | `lb-web.service` → 127.0.0.1:3102 (≤400 MB) |
| Database | `civiclens_lb` on the existing RDS instance, as its own role `civiclens_lb` (no access to AH's data; AH password rotations don't affect it) |
| Files | `/var/lib/long-beach/storage` |
| Access | `access.mode: invite_only` — no signup; every `/api` call needs a signed-in user |

## 1. DNS (once)

Add an **A record**: `longbeach.ahnj.info` → the EC2 instance's public IP
(the same address `api.ahnj.info` points to).

## 2. Copy the data to the box

`long-beach-corpus.jsonl.gz` (12 MB): all 1,020 Long Beach documents with
their text already extracted. No OCR runs on the box.

```bash
scp long-beach-corpus.jsonl.gz ubuntu@<ec2-ip>:/tmp/
```

## 3. Install

The repo is private, so clone with a token that can read it:

```bash
ssh ubuntu@<ec2-ip>
git clone https://<token>@github.com/kknechtel/civic-lens.git /tmp/civic-lens
sudo REPO_URL=https://<token>@github.com/kknechtel/civic-lens.git \
     bash /tmp/civic-lens/deploy/long-beach/setup.sh /tmp/long-beach-corpus.jsonl.gz
```

About 10–15 minutes, mostly the web build (capped at 1.8 GB). It prints:
- the **temporary password** for `karl@rkc.llc` (you'll be asked to change it on first sign-in);
- confirmation that the API answers and TLS is set up.

Run it outside the nightly AH scrape (midnight ET), so the web build doesn't
compete with OCR.

## 4. Invite your friend

```bash
sudo bash /opt/long-beach/deploy/long-beach/lb.sh invite
# or lock it to their address:
sudo bash /opt/long-beach/deploy/long-beach/lb.sh invite friend@example.com
```

Send them the printed link privately. It works once and expires in 7 days;
they choose their own password. Anyone else who opens the site sees a
"Private site" sign-in page and nothing else.

## Day to day

```bash
sudo bash /opt/long-beach/deploy/long-beach/lb.sh status    # services + memory
sudo bash /opt/long-beach/deploy/long-beach/lb.sh logs
sudo bash /opt/long-beach/deploy/long-beach/lb.sh update    # pull, rebuild, restart
```

- **Original PDFs** download in the background after setup
  (`lb-rehydrate.service`, network only, a few hours). Search and chat work
  before that; only "open the PDF" waits for it.
- **AI chat** needs a key: Admin → AI. You can add your own key there or in
  Settings; your friend can add theirs in Settings. Nothing is paid by the site
  until you tick it in Admin → AI.
- **New documents**: there is deliberately no nightly Long Beach scrape on this
  box (OCR is what took AH down on Oct 15). To refresh, run a crawl elsewhere
  and import the export, or enable `scheduled_scrape` with the same caps as
  `ah-scraper.service`.
- **Semantic search** (optional): add a Voyage key in Admin → AI, set
  `EMBEDDINGS_PROVIDER=voyage` in `/etc/long-beach.env`, `lb.sh restart`, then
  `python -m scripts.generate_embeddings`. Keyword search alone scores 93% on
  the Long Beach eval.

## Remove it

```bash
sudo systemctl disable --now lb-api lb-web; sudo rm /etc/systemd/system/lb-*.service
sudo rm /etc/nginx/sites-enabled/longbeach && sudo systemctl reload nginx
# database: DROP DATABASE civiclens_lb;  files: /var/lib/long-beach
```
