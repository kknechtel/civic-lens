# Contributing

The most valuable contributions are **municipality profiles** and **state packs**.
See [docs/ADDING_A_MUNICIPALITY.md](docs/ADDING_A_MUNICIPALITY.md).

- Run `pytest` in `api/` and `npx tsc --noEmit && npm run build` in `web/` before opening a PR.
- New source adapters need a test with a recorded fixture; tests must not touch the network.
- Never hardcode a town in `api/` or `web/`. `tests/test_prompts.py` fails if you do.
- Do not add sources that bypass robots.txt without a documented `permission_note`.
- Do not commit credentials, cookies, or personal data scraped from public records.
