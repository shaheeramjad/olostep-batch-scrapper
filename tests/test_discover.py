"""Tests for URL filtering and de-duplication (no network)."""

from olostep_batch_scraper.discover import (
    filter_urls,
    host_of,
    matches,
    normalize_url,
)

BASE = "https://docs.python.org"


def test_normalize_strips_fragment_and_lowercases_host() -> None:
    url = normalize_url("  https://Docs.Python.org/3/os.html#path ")
    assert url == f"{BASE}/3/os.html"


def test_dedup_after_normalization() -> None:
    urls = [f"{BASE}/3/a.html", f"{BASE}/3/a.html#x", f"{BASE}/3/a.html"]
    assert filter_urls(urls) == [f"{BASE}/3/a.html"]


def test_include_substring() -> None:
    urls = [f"{BASE}/3/a.html", f"{BASE}/2/b.html"]
    assert filter_urls(urls, include=["/3/"]) == [f"{BASE}/3/a.html"]


def test_exclude_wins_over_include() -> None:
    urls = [f"{BASE}/3/a.html", f"{BASE}/3/whatsnew/x.html"]
    result = filter_urls(urls, include=["/3/"], exclude=["/whatsnew/"])
    assert result == [f"{BASE}/3/a.html"]


def test_glob_matches_path() -> None:
    assert matches(f"{BASE}/3/library/os.html", "/3/library/*")
    assert not matches(f"{BASE}/3/tutorial/x.html", "/3/library/*")


def test_skips_assets_and_non_http() -> None:
    urls = [
        f"{BASE}/3/a.html",
        f"{BASE}/3/archive/docs.zip",
        f"{BASE}/3/logo.PNG",
        "mailto:someone@example.com",
        "ftp://example.com/file",
    ]
    assert filter_urls(urls) == [f"{BASE}/3/a.html"]


def test_limit_is_applied_after_filtering_and_sorted() -> None:
    urls = [f"{BASE}/3/{name}.html" for name in "edcba"]
    urls.append(f"{BASE}/2/z.html")
    result = filter_urls(urls, include=["/3/"], limit=2)
    assert result == [f"{BASE}/3/a.html", f"{BASE}/3/b.html"]


def test_hosts_keeps_only_the_mapped_site() -> None:
    urls = [
        "https://www.gov.uk/guidance/a",
        "https://directdebit.taxdisc.service.gov.uk/vehicletax",
        "https://WWW.GOV.UK/guidance/b",
    ]
    result = filter_urls(urls, hosts=[host_of("https://www.gov.uk")])
    assert result == ["https://www.gov.uk/guidance/a", "https://www.gov.uk/guidance/b"]


def test_host_of_accepts_bare_domains() -> None:
    assert host_of("docs.djangoproject.com") == "docs.djangoproject.com"
    assert host_of("https://Docs.Python.org/3/") == "docs.python.org"
