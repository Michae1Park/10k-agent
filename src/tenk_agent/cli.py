import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path

from tenk_agent.corpus import COMPANIES, FISCAL_YEARS, company_by_ticker
from tenk_agent.edgar import EdgarClient, EdgarError, fetch_company


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tenk")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    commands = parser.add_subparsers(dest="command", required=True)

    fetch = commands.add_parser("fetch", help="download the corpus 10-Ks from EDGAR")
    fetch.add_argument("--tickers", nargs="*", help="subset of corpus tickers (default: all)")
    fetch.add_argument("--refresh", action="store_true", help="re-download cached files")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.command == "fetch":
        return _fetch(args)
    return 1


def _fetch(args: argparse.Namespace) -> int:
    user_agent = os.environ.get("SEC_USER_AGENT")
    if not user_agent:
        print("Set SEC_USER_AGENT, e.g. 'Name you@example.com'", file=sys.stderr)
        return 2

    companies = [company_by_ticker(t) for t in args.tickers] if args.tickers else list(COMPANIES)
    client = EdgarClient(user_agent)
    manifest_path = args.data_dir / "raw" / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    failed = False
    for company in companies:
        try:
            filings = fetch_company(client, company, FISCAL_YEARS, args.data_dir, args.refresh)
        except EdgarError as error:
            logging.error("%s", error)
            failed = True
            continue
        manifest[company.ticker] = [asdict(f) for f in filings]

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
