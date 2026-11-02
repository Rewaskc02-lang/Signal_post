"""Generate the browsable profile manifest used by reports/ui/index.html."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _claim_map(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        str(fact.get("field_name")): fact.get("value")
        for fact in profile.get("facts", [])
        if isinstance(fact, dict) and fact.get("field_name")
    }


def build_manifest(profiles_dir: Path, manifest_path: Path) -> int:
    entries: list[dict[str, Any]] = []
    for path in sorted(profiles_dir.glob("*.json")):
        if path.name == "envelopes.jsonl":
            continue
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(profile, dict):
            continue

        claims = _claim_map(profile)
        orgnr = str(profile.get("orgnr") or path.stem)
        statuses = profile.get("source_statuses") or {}
        state = "available" if claims else "failed"
        categories = sorted(
            {
                category
                for category, status in statuses.items()
                if status in {"available", "missing", "not_available", "blocked", "ambiguous"}
            }
        )
        entries.append(
            {
                "orgnr": orgnr,
                "name": claims.get("legal_name") or f"Organisation {orgnr}",
                "state": state,
                "city": (
                    claims.get("business_address", {}).get("city")
                    if isinstance(claims.get("business_address"), dict)
                    else ""
                ),
                "industry": " ".join(
                    str(claims.get(field, ""))
                    for field in ("nace_code", "nace_description")
                    if claims.get(field)
                ),
                "categories": categories,
                "file": f"../../profiles/{path.name}",
                "profile_url": f"profile.html?file=../../profiles/{path.name}",
            }
        )

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(entries)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, default=Path("profiles"))
    parser.add_argument("--output", type=Path, default=Path("reports/ui/profiles_index.json"))
    args = parser.parse_args()
    print(f"Generated {build_manifest(args.profiles, args.output)} profile entries at {args.output}")


if __name__ == "__main__":
    main()
