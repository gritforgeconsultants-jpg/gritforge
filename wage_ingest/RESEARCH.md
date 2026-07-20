# Wage Rate Ingestion — Research Pass (FIRST STEP)

**Status: research only. No scraper code written yet — awaiting confirmation on the open decisions below.**

This document is the output of the required "confirm the actual current structure before
building" step. It records what was verified about the two data sources, a hard environment
blocker discovered during the pass, and the decisions that need a human answer before the
pipeline is built.

Date of pass: 2026-07-20.

---

## 1. SAM.gov — federal Davis-Bacon determinations

### Access model: HTML web app, **no official public API**

- SAM.gov has been the official Davis-Bacon source since June 2019 (`sam.gov/wage-determinations`).
- The documented GSA APIs (`open.gsa.gov/api/`) cover **Opportunities, Entity, Federal
  Hierarchy, etc. — wage determinations are NOT among them.** There is no authoritative
  JSON API for Davis-Bacon wage determinations.
- The site is a JavaScript SPA (Angular). Filtering is **state → county → construction type**
  (building, residential, highway, heavy); this county+type model has been in place since
  Jan 2022.

### URL / identifier structure

- Individual determination page: `sam.gov/wage-determination/{NUMBER}/{revision}`
  - Example: `sam.gov/wage-determination/CO20260022/0` — El Paso County, building, dated 01/02/2026.
  - `{NUMBER}` = `{STATE}{YEAR}{seq}` (e.g. `CO20260022`).
  - `{revision}` = the trailing integer = the **modification number**.
- Colorado determinations are `CO`-prefixed and refresh roughly **twice a year (January and July)**.

### Brittleness / "human intervenes" reality

- Programmatic extraction means either driving the SPA with a headless browser to reach the
  internal backend it calls, or downloading the per-WD PDF / plain-text export and parsing that.
  Both are **structure-dependent**.
- SAM.gov's WD **formatting changed again in 2026** (some SCA WDs published 4/29/2026 shipped
  with incomplete location data). Structure change is the norm, not the exception — this is the
  exact risk the task flagged.

### Fit with the stated non-goals

- Determinations are **per-county + per-construction-type**. A county with no separate
  determination maps naturally to the required **"not covered"** output — no interpolation
  needed. Consistent with the non-goals (no statewide-average inference for Colorado Springs /
  Fort Collins / etc.).

---

## 2. Denver Auditor's Office — city ordinance (separate data source)

### Access model: landing page + linked PDFs

- Landing page: `denvergov.org/…/Auditors-Office/Denver-Labor/Prevailing-Wage`
- Current 2026 determination PDFs live under a pattern like:
  `https://www.denvergov.org/files/assets/public/v/3/auditor/documents/denver-labor/2026/prevailing-wage-determinations/<file>.pdf`

Observed files:

| File | Meaning |
|------|---------|
| `building-mod-0_1_2_26.pdf` | Building General Wage Decision (cumulative mods 0-1-2, dated 1/2/26) |
| `heavy-mod-0_1_2_26.pdf` | Heavy General Wage Decision |
| `res_co20260004_1_2_2026.pdf` | Residential (**different naming** — embeds a Davis-Bacon-style number) |
| `pw-adm-mod-177-2_11_2026.pdf` | Prevailing Wage Administrator schedule (mod 177); carries 2026 Denver minimum wage $19.29 |

### Key structural facts

- **The PDF URL is NOT deterministically constructable.** The filename encodes the cumulative
  mod number and a date, and the path has a CMS version segment (`/v/3/`, `/v/2/`). The landing
  page must be scraped each run and the current link followed — exactly as the task anticipated.
- Denver's three trade categories (Building / Heavy / Residential) plus the Administrator
  schedule confirm the separate-schema / separate-table decision. Denver even references
  Davis-Bacon classifications internally, so the conflation risk the task wants to prevent is real
  → keep `denver_wage_rates` distinct from the federal table and tag every row
  `source = 'denver_ordinance'` vs `source = 'federal_dbra'`.

---

## 3. BLOCKER — environment egress policy denies both sources

This remote session's network policy blocks all three hosts. Confirmed via the agent proxy
status endpoint — `sam.gov`, `www.denvergov.org`, and `open.gsa.gov` all return **403 on
CONNECT** (organization policy denial). Only `WebSearch` works, because it is proxied through
Anthropic's backend rather than making a direct outbound connection.

Consequences that hit the deliverables directly:

1. A **real determination PDF cannot be downloaded here** to commit as the required test fixture.
2. `python -m wage_ingest --run` **cannot reach either data source from this environment** — the
   pipeline can be built here but not run or verified here until the policy allowlists these hosts.

Do not route around this (per the proxy README). It needs either an allowlist change or
locally-supplied fixtures.

---

## 4. Open decisions — need confirmation before building

### D1. How to handle the network block (blocking)

- **(a) You commit sample PDFs** — one Denver determination PDF + one SAM.gov CO determination
  (PDF or saved HTML). Parser is built and tested against those real fixtures offline. Cleanest
  given the block. *(recommended)*
- **(b) Allowlist the hosts** — get the egress policy updated to allow `sam.gov`,
  `www.denvergov.org` (and `api.sam.gov`); then fetch, verify, download a fixture, and run
  end-to-end from here.
- **(c) Build blind, verify elsewhere** — build against the documented structure with no live
  verification and a skipped fixture test. Higher risk of being wrong against the real page.

### D2. SAM.gov extraction strategy (no official API)

- **(a) Download the per-WD file** (PDF / plain-text export) and parse that — most stable
  surface, mirrors the document-parsing the Denver route already needs. *(recommended)*
- **(b) Headless-browser the SPA** with Playwright — more powerful, far more brittle/heavy.
- **(c) Third-party API** (paid, non-authoritative) — avoids scraping but adds a dependency/cost.

### D3. The 9 target counties

The task references "the 9 target counties" and a fixed rejection list, and names Colorado
Springs (El Paso) and Fort Collins (Larimer) as examples. **The actual list of 9 counties has
not been provided.** It is needed for the county cross-check (reject non-matches, no fuzzy
correction). Please supply the exact 9 county names.

---

## Sources

- https://sam.gov/wage-determinations
- https://sam.gov/wage-determination/CO20260022/0
- https://www.dol.gov/agencies/whd/government-contracts/prevailing-wage-resource-book/db-wage-determinations
- https://www.dol.gov/sites/dolgov/files/WHD/Obtaining-WDs.pdf
- https://open.gsa.gov/api/
- https://denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Auditors-Office/Denver-Labor/Prevailing-Wage
- https://osa.colorado.gov/state-buildings/prevailing-wage-and-apprenticeship/wage-determinations
