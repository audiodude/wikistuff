#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "requests",
#     "typer",
# ]
# ///
"""Small read-only CLI for Wikidata and English Wikipedia lookups."""

from __future__ import annotations

import json
import re
import sys
from typing import Optional

import requests
import typer


WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
ENWIKI_API_URL = "https://en.wikipedia.org/w/api.php"
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
USER_AGENT = "wikistuff+audiodude/0.2.1 <User:Audiodude>"
QID_RE = re.compile(r"^Q[1-9]\d*$")

HEADERS = {"User-Agent": USER_AGENT}

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Read-only Wikidata and English Wikipedia lookup helpers.",
)


def api_get(api_url: str, params: dict) -> dict:
    response = requests.get(api_url, params=params, headers=HEADERS, timeout=15)
    response.raise_for_status()
    return response.json()


def normalize_qid(qid: str) -> str:
    normalized = qid.upper()
    if not QID_RE.fullmatch(normalized):
        raise ValueError(f"invalid QID {qid!r}; expected something like Q42")
    return normalized


def fetch_label(qid: str, language: str) -> str:
    payload = api_get(
        WIKIDATA_API_URL,
        {
            "action": "wbgetentities",
            "ids": qid,
            "props": "labels",
            "languages": language,
            "languagefallback": "1",
            "format": "json",
        },
    )

    entity = payload.get("entities", {}).get(qid)
    if not entity or entity.get("missing"):
        raise LookupError(f"{qid} was not found on Wikidata")

    label = entity.get("labels", {}).get(language, {}).get("value")
    if not label:
        raise LookupError(f"{qid} has no {language!r} label")

    return label


def fetch_enwiki_title(qid: str) -> str:
    payload = api_get(
        WIKIDATA_API_URL,
        {
            "action": "wbgetentities",
            "ids": qid,
            "props": "sitelinks",
            "sitefilter": "enwiki",
            "format": "json",
        },
    )

    entity = payload.get("entities", {}).get(qid)
    if not entity or entity.get("missing"):
        raise LookupError(f"{qid} was not found on Wikidata")

    title = entity.get("sitelinks", {}).get("enwiki", {}).get("title")
    if not title:
        raise LookupError(f"{qid} has no English Wikipedia article")

    return title


def fetch_enwiki_page(qid: str) -> dict:
    title = fetch_enwiki_title(qid)
    payload = api_get(
        ENWIKI_API_URL,
        {
            "action": "query",
            "titles": title,
            "redirects": "1",
            "format": "json",
        },
    )

    pages = payload.get("query", {}).get("pages", {})
    for page in pages.values():
        if "missing" in page:
            break
        return {"pageid": page["pageid"], "title": page["title"]}

    raise LookupError(f"{title!r} was not found on English Wikipedia")


def fetch_enwiki_categories(pageid: int, include_hidden: bool, limit: Optional[int]) -> list[dict]:
    categories: list[dict] = []
    params = {
        "action": "query",
        "pageids": str(pageid),
        "prop": "categories",
        "clprop": "hidden",
        "cllimit": "max",
        "format": "json",
    }
    if not include_hidden:
        params["clshow"] = "!hidden"

    while True:
        payload = api_get(ENWIKI_API_URL, params)
        pages = payload.get("query", {}).get("pages", {})
        page = pages.get(str(pageid), {})

        for category in page.get("categories", []):
            categories.append(
                {
                    "title": category["title"],
                    "hidden": "hidden" in category,
                }
            )
            if limit is not None and len(categories) >= limit:
                return categories

        continuation = payload.get("continue")
        if not continuation:
            return categories
        params.update(continuation)


def handle_errors(func):
    try:
        return func()
    except ValueError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2)
    except (LookupError, requests.RequestException) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1)


@app.command("get-wikidata-label")
def get_wikidata_label(
    qid: str = typer.Argument(..., help="Wikidata entity ID, for example Q42"),
    language: str = typer.Option("en", "-l", "--language", help="label language code (default: en)"),
    output_json: bool = typer.Option(False, "--json", help="emit machine-readable JSON"),
) -> None:
    """Print the Wikidata label for a QID."""

    def run() -> None:
        normalized = normalize_qid(qid)
        label = fetch_label(normalized, language)
        if output_json:
            typer.echo(
                json.dumps(
                    {"qid": normalized, "language": language, "label": label},
                    indent=2,
                )
            )
        else:
            typer.echo(label)

    handle_errors(run)


@app.command("list-enwiki-categories")
def list_enwiki_categories(
    qid: str = typer.Argument(..., help="Wikidata entity ID, for example Q4675"),
    hidden: bool = typer.Option(False, "--hidden", help="include hidden maintenance categories"),
    limit: Optional[int] = typer.Option(None, "--limit", min=1, help="maximum number of categories to print"),
    output_json: bool = typer.Option(False, "--json", help="emit machine-readable JSON"),
) -> None:
    """List categories for the English Wikipedia article associated with a QID."""

    def run() -> None:
        normalized = normalize_qid(qid)
        page = fetch_enwiki_page(normalized)
        categories = fetch_enwiki_categories(page["pageid"], hidden, limit)
        if output_json:
            typer.echo(
                json.dumps(
                    {
                        "qid": normalized,
                        "pageid": page["pageid"],
                        "title": page["title"],
                        "categories": categories,
                    },
                    indent=2,
                )
            )
        else:
            for category in categories:
                typer.echo(category["title"])

    handle_errors(run)


@app.command("list-example-humans")
def list_example_humans(
    output_json: bool = typer.Option(False, "--json", help="emit machine-readable JSON"),
) -> None:
    """List ten example human Wikidata items using SPARQL."""

    def run() -> None:
        query = """
SELECT ?item ?itemLabel WHERE {
  ?item wdt:P31 wd:Q5.
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
LIMIT 10
"""
        payload = api_get(WIKIDATA_SPARQL_URL, {"query": query, "format": "json"})
        rows = payload.get("results", {}).get("bindings", [])

        if output_json:
            typer.echo(json.dumps(rows, indent=2))
        else:
            for row in rows:
                label = row.get("itemLabel", {}).get("value", "")
                item = row.get("item", {}).get("value", "")
                typer.echo(f"{label}\t{item}")

    handle_errors(run)


@app.command("help")
def help_command(
    topic: Optional[str] = typer.Argument(None, help="subcommand to describe"),
) -> None:
    """Show help for the tool or a subcommand."""
    args = [topic, "--help"] if topic else ["--help"]
    try:
        app(args, standalone_mode=False)
    except SystemExit:
        pass
    except Exception as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2)


if __name__ == "__main__":
    app()
