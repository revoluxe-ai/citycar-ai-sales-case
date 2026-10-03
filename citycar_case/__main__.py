"""Run a synthetic demo or inspect an amoCRM account read-only."""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .amo import AmoAPIError, AmoClient
from .rules import problem_deals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--demo", action="store_true")
    modes.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.demo:
            fixture_path = Path(__file__).resolve().parents[1] / "examples" / "demo.json"
            data = json.loads(fixture_path.read_text(encoding="utf-8"))
            as_of = int(datetime.fromisoformat(data["as_of"]).timestamp())
            leads, tasks = data["leads"], data["tasks"]
            source = "synthetic_demo"
        else:
            subdomain = os.environ.get("AMO_SUBDOMAIN", "")
            token = os.environ.get("AMO_ACCESS_TOKEN", "")
            if not subdomain or not token:
                parser.error("Set AMO_SUBDOMAIN and AMO_ACCESS_TOKEN")
            client = AmoClient(subdomain, token)
            as_of = int(time.time())
            leads = client.get_all("leads")
            tasks = client.get_all("tasks", {
                "filter[entity_type]": "leads",
                "filter[is_completed]": 0,
            })
            source = "amocrm"
        problems = problem_deals(leads, tasks, as_of)
        report = {
            "data_source": source,
            "snapshot_at": datetime.fromtimestamp(as_of, timezone.utc).isoformat(),
            "visible_leads": len(leads),
            "problem_count": len(problems),
            "problem_deals": problems,
        }
        text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
    except (AmoAPIError, ValueError, KeyError, OSError) as error:
        print(f"Report not produced: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
