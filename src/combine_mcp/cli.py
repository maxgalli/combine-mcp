"""Command-line interface for combine-mcp."""

from __future__ import annotations

import argparse
from pathlib import Path

from combine_mcp.scrape import DEFAULT_OUTPUT as SCRAPE_DEFAULT_OUTPUT
from combine_mcp.scrape import scrape as _do_scrape
from combine_mcp.server import serve


def main() -> None:
    """Entry point for the ``combine-mcp`` command."""
    parser = argparse.ArgumentParser(
        prog="combine-mcp",
        description=(
            "MCP server exposing the CMS Combine documentation corpus."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # --- serve ----------------------------------------------------------
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the MCP server",
    )
    serve_parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport protocol (default: stdio). Use 'streamable-http' "
             "for OpenWebUI / opencode-remote-MCP / Claude Desktop remote.",
    )
    serve_parser.add_argument(
        "--host",
        default="0.0.0.0",  # noqa: S104
        help="Bind address for HTTP transport (default: 0.0.0.0)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for HTTP transport (default: 8000)",
    )
    serve_parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Path to a JSON config defining documentation sources. "
            "When omitted, the package-bundled docs_sources.json is used."
        ),
    )

    # --- scrape ---------------------------------------------------------
    scrape_parser = subparsers.add_parser(
        "scrape",
        help="Scrape the cms-talk Statistics category into corpora/forum/",
    )
    scrape_parser.add_argument(
        "--output", type=Path, default=SCRAPE_DEFAULT_OUTPUT,
        help=f"output directory (default: {SCRAPE_DEFAULT_OUTPUT})",
    )
    scrape_parser.add_argument(
        "--full", action="store_true",
        help="rescrape every topic, ignoring the manifest",
    )
    scrape_parser.add_argument(
        "--sleep", type=float, default=0.5,
        help="seconds between API calls (default: 0.5)",
    )
    scrape_parser.add_argument(
        "--limit", type=int, default=None,
        help="stop after N topics (debug)",
    )

    args = parser.parse_args()

    if args.command == "serve":
        serve(
            transport=args.transport,
            host=args.host,
            port=args.port,
            config_path=args.config,
        )
    elif args.command == "scrape":
        _do_scrape(
            output_dir=args.output,
            full=args.full,
            sleep_s=args.sleep,
            limit=args.limit,
        )
    else:
        parser.print_help()
