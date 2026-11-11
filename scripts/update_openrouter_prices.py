#!/usr/bin/env python3
"""Fill input_price_per_m / output_price_per_m in conf/openrouter_models.json from OpenRouter's model list.

The prices (USD per million tokens) feed the MAX_OUTPUT_PRICE_PER_M cost guard (utils/cost_policy.py).
Run it at each catalog refresh; it needs no API key. Entries that OpenRouter does not list, or lists with
a variable price (for example the Fusion routers, priced per underlying call), keep no price, so the
ceiling does not apply to them. Those routers use requires_opt_in instead.

Usage:
    python scripts/update_openrouter_prices.py           # update the file and print the changes
    python scripts/update_openrouter_prices.py --check   # exit 1 if any stored price differs
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

CONF = Path(__file__).resolve().parents[1] / "conf" / "openrouter_models.json"
API = "https://openrouter.ai/api/v1/models"


def per_million(value) -> float | None:
    """OpenRouter gives USD per token as a string; return USD per million tokens, or None if unusable."""
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price < 0:
        return None
    return round(price * 1_000_000, 4)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report differences and exit 1; do not write")
    args = parser.parse_args()

    with urllib.request.urlopen(API, timeout=60) as resp:
        listed = {m["id"]: m.get("pricing") or {} for m in json.load(resp)["data"]}

    text = CONF.read_text()
    data = json.loads(text)
    changed, missing = [], []
    for entry in data["models"]:
        name = entry["model_name"]
        pricing = listed.get(name)
        if pricing is None:
            missing.append(name)
            continue
        new = {
            "input_price_per_m": per_million(pricing.get("prompt")),
            "output_price_per_m": per_million(pricing.get("completion")),
        }
        if new["input_price_per_m"] is None or new["output_price_per_m"] is None:
            for key in new:  # variable or unknown price: store none
                if key in entry:
                    changed.append(f"{name}: drop {key}")
                    del entry[key]
            continue
        for key, value in new.items():
            if entry.get(key) != value:
                changed.append(f"{name}: {key} {entry.get(key)} -> {value}")
                entry[key] = value

    for line in changed:
        print(line)
    if missing:
        print(f"not listed on OpenRouter (no price stored): {', '.join(missing)}")
    if args.check:
        return 1 if changed else 0
    if changed:
        CONF.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        print(f"updated {CONF.name}: {len(changed)} change(s)")
    else:
        print("prices are current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
