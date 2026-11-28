"""Resolve kTap GitHub release title for the tagged version."""

from __future__ import annotations

import os
import re


def main() -> int:
    with open("Cargo.toml", "r", encoding="utf-8") as f:
        cargo_text = f.read()
    m = re.search(r'(?m)^codename\s*=\s*"([^"]+)"', cargo_text)
    codename = m.group(1).strip() if m else ""

    with open("src/lib.rs", "r", encoding="utf-8") as f:
        lib_text = f.read()
    pairs = dict(re.findall(r'\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)', lib_text))

    tag = os.environ["TAG_NAME"]
    ver = tag.lstrip("vV").split("-", 1)[0]
    codename = pairs.get(ver, codename)
    title = f'{ver} "{codename}"' if codename else ver

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as out:
            out.write(f"codename={codename}\n")
            out.write(f"release_title={title}\n")

    print(f"Resolved tag={tag} -> release_title={title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
