# olostep-batch-scraper

Scrape thousands of web pages to clean Markdown in minutes with Python and
[Olostep](https://www.olostep.com) Maps + Batches.

> **Demo video:** _Scraping 50,000 Pages in Under 20 Minutes with Python
> and Olostep_ (link coming soon)

## Features

- **URL discovery** with the Olostep **Maps** endpoint. You don't need a sitemap.
- **Several sites in one run.** Repeat `--site`, and each site is mapped in parallel.
- **Include/exclude filters**, same-host filtering, de-duplication and a hard
  cap (default 7,000).
- **One batch job** for every URL, each tagged with a deterministic `custom_id`.
- **Live progress bars** for scraping and downloading, with elapsed time.
- **Download while scraping.** Pages are fetched as soon as Olostep finishes
  them, so almost no download time is left once the batch completes. Downloads
  run in parallel with a concurrency limit, timeouts and retries.
- **Idempotent and resumable.** Existing files are skipped, writes are atomic,
  and `--resume` picks up an interrupted run.
- **Verified report:** `summary.json`, `manifest.csv` and `failures.csv`.

## Quickstart

```bash
git clone https://github.com/shaheeramjad/olostep-batch-scrapper
cd olostep-batch-scrapper
pip install -e .                       # Python 3.10+
cp .env.example .env                   # then add your OLOSTEP_API_KEY
python -m olostep_batch_scraper --site https://docs.djangoproject.com/en/5.1/ --include "/en/"
```

Get an API key from the [Olostep dashboard](https://www.olostep.com/dashboard).

> **Note:** new Olostep accounts can submit up to 100 URLs per batch. Ask Olostep
> support to raise the limit before you run thousands of pages.

## CLI options

| Option            | Default  | Description                                           |
| ----------------- | -------- | ----------------------------------------------------- |
| `--site URL`      | required | Website to map (repeatable), e.g. `https://gov.uk`    |
| `--limit N`       | `7000`   | Maximum number of URLs to scrape                      |
| `--include PAT`   | none     | Keep URLs matching a substring or path glob (repeat)  |
| `--exclude PAT`   | none     | Drop URLs matching a substring or path glob (repeat)  |
| `--out DIR`       | `output` | Output folder                                         |
| `--concurrency N` | `50`     | Parallel Markdown downloads                           |
| `--resume`        | off      | Resume the batch saved in `<out>/batch.json`          |
| `--dry-run`       | off      | Discover and filter URLs only. No batch, no scraping  |
| `-v, --verbose`   | off      | Debug logging                                         |

Patterns containing `*`, `?` or `[` are globs matched against the URL path
(`--include "/3/library/*"`). Other patterns are plain substrings of the URL
(`--include "/3/"`).

Only URLs on the same host as a `--site` are kept, because Maps can also
return links to other domains.

For large runs, raise `--concurrency` (e.g. `200`) so downloads keep up with
the batch.

## How it works

```
discover  ->  batch  ->  download  ->  verify
 (Maps)      (Batches)   (Retrieve)    (reports)
```

1. **Discover** (`discover.py`): `client.maps.create(url=...)` returns every URL
   Olostep finds on each site. URLs are normalized, de-duplicated, limited to
   the site's host, filtered, sorted and capped.
2. **Batch** (`batch.py`): all URLs go out in a single `client.batches.create`
   call. The batch id is written to `output/batch.json` straight away, and
   `client.batches.info` is polled to drive the progress bar.
3. **Download** (`download.py`): while the batch runs, completed items are
   listed every few seconds with `client.batches.items(status="completed")`
   and fetched straight away with `client.retrieve.get(...,
   formats=["markdown"])`. An asyncio semaphore limits concurrency, and each
   page gets a timeout and 2 retries with backoff.
4. **Verify** (`report.py`): every submitted URL is checked against the files on
   disk, and the reports are written.

## Output files

```
output/
├── pages/<custom_id>.md   # one Markdown file per page (front matter: source URL)
├── urls.json              # custom_id -> URL, exactly what was submitted
├── batch.json             # batch id + timings, used by --resume
├── manifest.csv           # custom_id, url, status, file, bytes
├── failures.csv           # custom_id, url, error
└── summary.json           # totals, success rate, timings, 50/90/99% milestones
```

## Sample summary

A real run of 50,000 pages from GOV.UK:

```
$ python -m olostep_batch_scraper --site https://www.gov.uk \
    --exclude "tribunal-decisions" --limit 50000 --concurrency 200

INFO     Maps returned 153,233 URLs
INFO     50,000 URLs after filtering (limit 50000)
INFO     Batch created: batch_jditn1pfsc97
50,000 pages scraped in 14m 36s
Scraping     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 50000/50000 100% elapsed 0:14:36
Downloading  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 50000/50000 100% elapsed 0:18:01

          Olostep batch scrape
┌────────────────────────────┬─────────┐
│ Pages submitted            │  50,000 │
│ Succeeded                  │  50,000 │
│ Failed                     │       0 │
│ Success rate               │  100.0% │
├────────────────────────────┼─────────┤
│ Discovery time             │   54.2s │
│ Scrape time                │ 14m 36s │
│   50% scraped at           │  8m 38s │
│   90% scraped at           │ 10m 03s │
│   99% scraped at           │ 13m 34s │
│ Download (after scrape)    │  3m 48s │
│ Total time                 │ 19m 20s │
└────────────────────────────┴─────────┘
```

A 7,000-page run of the Django docs took 13m 47s to scrape. Per the Olostep
docs, a batch takes up to about 20–25 minutes regardless of its size, so bigger
batches get far more pages done in that same time.

## Use cases

- **RAG knowledge bases.** Turn a docs site into clean Markdown chunks for a
  vector store.
- **AI datasets.** Collect large, consistent text corpora for fine-tuning or
  evaluation.
- **Docs search.** Index a whole documentation site for local or semantic
  search.
- **Monitoring.** Re-run on a schedule and diff the Markdown to catch content
  changes.

## Development

```bash
pip install -e ".[dev]"
ruff format . && ruff check .
pytest
```

## License

[MIT](LICENSE)
