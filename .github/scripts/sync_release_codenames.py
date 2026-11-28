"""Resolve KeyForge release codename and synchronize historical GitHub Release titles."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


def main() -> int:
    with open("Cargo.toml", "r", encoding="utf-8") as f:
        cargo_text = f.read()
    m = re.search(r'(?m)^codename\s*=\s*"([^"]+)"', cargo_text)
    current_codename = m.group(1) if m else ""

    with open("src/lib.rs", "r", encoding="utf-8") as f:
        lib_text = f.read()
    pairs = dict(re.findall(r'\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)', lib_text))

    tag = os.environ["TAG_NAME"]
    ver = tag.lstrip("vV").split("-", 1)[0]
    codename = pairs.get(ver, current_codename)
    title = f'KeyForge {tag} "{codename}"' if codename else f"KeyForge {tag}"

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as out:
            out.write(f"codename={codename}\n")
            out.write(f"release_title={title}\n")

    token = os.environ["GH_TOKEN"]
    repo = os.environ["GH_REPO"]
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "KeyForge-Release-Sync",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases?per_page=50",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        releases = json.loads(resp.read().decode("utf-8"))

    for rel in releases:
        rtag = rel.get("tag_name", "")
        rver = rtag.lstrip("vV").split("-", 1)[0]
        rcodename = pairs.get(rver)
        if not rcodename:
            continue
        want_name = f'KeyForge {rtag} "{rcodename}"'
        if rel.get("name") == want_name:
            continue
        patch_req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases/{rel['id']}",
            data=json.dumps({"name": want_name}).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
            method="PATCH",
        )
        try:
            with urllib.request.urlopen(patch_req, timeout=20) as presp:
                print(f"Synced {rtag} -> {want_name} (HTTP {presp.status})")
        except urllib.error.HTTPError as err:
            print(f"Warning: could not sync {rtag}: HTTP {err.code}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
