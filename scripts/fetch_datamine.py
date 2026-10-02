"""Fetch a pinned subset of Dimbreath TurnBasedGameData (no full clone).

Primary: GitHub DimbreathBot/TurnBasedGameData
Fallback: GitLab Dimbreath/turnbasedgamedata (same commit)

Writes data/external/turnbasedgamedata/ and PROVENANCE.md.
That directory is gitignored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "data" / "external" / "turnbasedgamedata"

GITHUB_REPO = "DimbreathBot/TurnBasedGameData"
GITLAB_PROJECT = "Dimbreath/turnbasedgamedata"

FILES = (
    "ExcelOutput/AvatarSkillConfig.json",
    "ExcelOutput/AvatarConfig.json",
    "ExcelOutput/AvatarConfigEnhanced.json",
    "ExcelOutput/MonsterSkillConfig.json",
    "ExcelOutput/AvatarRankConfig.json",
    "ExcelOutput/ExtraEffectConfig.json",
    "ExcelOutput/AvatarSkillTreeConfig.json",
    "ExcelOutput/AvatarPromotionConfig.json",
    "ExcelOutput/EquipmentConfig.json",
    "ExcelOutput/EquipmentPromotionConfig.json",
    "ExcelOutput/EquipmentSkillConfig.json",
    "ExcelOutput/RelicSetConfig.json",
    "ExcelOutput/RelicSetSkillConfig.json",
    "TextMap/TextMapEN.json",
)

# Expected sizes/blob prefixes for commit 4ce30f69b32d (D1 + D6 + D5.2).
PINNED_EXPECT: dict[str, dict[str, tuple[int, str]]] = {
    "4ce30f69b32d": {
        "ExcelOutput/AvatarSkillConfig.json": (10_683_800, "2fb72d62e4e9"),
        "ExcelOutput/AvatarConfig.json": (230_483, "d400bf68e117"),
        "ExcelOutput/AvatarConfigEnhanced.json": (5_464, "4e4560a43d11"),
        "ExcelOutput/MonsterSkillConfig.json": (2_590_061, "f96d45bb8008"),
        "ExcelOutput/AvatarRankConfig.json": (314_267, "d4ed522fbba5"),
        "ExcelOutput/ExtraEffectConfig.json": (95_370, "1bc7ddb3160c"),
        "ExcelOutput/AvatarSkillTreeConfig.json": (4_336_332, "919ccb126815"),
        "ExcelOutput/AvatarPromotionConfig.json": (483_591, "2b7237ecb072"),
        "ExcelOutput/EquipmentConfig.json": (125_070, "0099c1c97666"),
        "ExcelOutput/EquipmentPromotionConfig.json": (677_186, "707a0cf9e371"),
        "ExcelOutput/EquipmentSkillConfig.json": (391_164, "5d0d2dcd75a9"),
        "ExcelOutput/RelicSetConfig.json": (22_101, "0ddef773f0f6"),
        "ExcelOutput/RelicSetSkillConfig.json": (30_473, "d04cd68ad009"),
        "TextMap/TextMapEN.json": (57_589_766, "3837d223f756"),
    }
}

def lookup_pinned(table: dict[str, str], commit_sha: str) -> str | None:
    for key, value in table.items():
        if commit_sha.startswith(key) or key.startswith(commit_sha):
            return value
    return None


def pinned_row(commit_sha: str, path: str) -> tuple[int, str] | None:
    for key, table in PINNED_EXPECT.items():
        if commit_sha.startswith(key) or key.startswith(commit_sha):
            return table.get(path)
    return None


PINNED_VERSION = {
    "4ce30f69b32d": "OSPRODWin4.5.0_D16545211_A16445860_L16502768",
}

UA = "hsr-fetch-datamine"


def _get_json(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def _download(url: str, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".partial")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    nbytes = 0
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, tmp.open("wb") as fh:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                nbytes += len(chunk)
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return nbytes


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data).hexdigest()


def github_commit(sha: str) -> dict:
    return _get_json(f"https://api.github.com/repos/{GITHUB_REPO}/commits/{sha}")  # type: ignore[return-value]


def github_file_meta(path: str, sha: str) -> dict:
    quoted = path
    return _get_json(  # type: ignore[return-value]
        f"https://api.github.com/repos/{GITHUB_REPO}/contents/{quoted}?ref={sha}"
    )


def github_has_license(sha: str) -> bool:
    for name in ("LICENSE", "LICENSE.md", "COPYING"):
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{name}?ref={sha}"
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status == 200:
                    return True
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
    return False


def raw_urls(path: str, sha: str) -> list[tuple[str, str]]:
    return [
        (
            "github",
            f"https://raw.githubusercontent.com/{GITHUB_REPO}/{sha}/{path}",
        ),
        (
            "github-raw-redirect",
            f"https://github.com/{GITHUB_REPO}/raw/{sha}/{path}",
        ),
        (
            "gitlab",
            "https://gitlab.com/api/v4/projects/"
            f"{GITLAB_PROJECT.replace('/', '%2F')}/repository/files/"
            f"{path.replace('/', '%2F')}/raw?ref={sha}",
        ),
    ]


def fetch_one(path: str, sha: str, meta: dict) -> tuple[Path, str, int]:
    dest = OUT_DIR / path
    errors: list[str] = []
    for source, url in raw_urls(path, sha):
        try:
            print(f"  GET {source} {path}")
            _download(url, dest)
            break
        except Exception as exc:  # noqa: BLE001 — try next mirror
            errors.append(f"{source}: {exc}")
    else:
        raise SystemExit(f"download failed for {path}:\n" + "\n".join(errors))

    data = dest.read_bytes()
    size = len(data)
    blob = git_blob_sha1(data)
    api_size = int(meta["size"])
    api_sha = str(meta["sha"])
    if size != api_size or blob != api_sha:
        raise SystemExit(
            f"API mismatch {path}: downloaded {size} blob {blob}; "
            f"API size {api_size} blob {api_sha}"
        )
    expect = pinned_row(sha, path)
    if expect is not None:
        exp_size, exp_prefix = expect
        if size != exp_size or not blob.startswith(exp_prefix):
            raise SystemExit(
                f"D1 mismatch {path}: got {size} bytes blob {blob}; "
                f"D1 expects {exp_size} bytes blob prefix {exp_prefix}. Stopping."
            )
    return dest, blob, size


def write_provenance(
    *,
    sha: str,
    version: str,
    rows: list[tuple[str, int, str]],
    has_license: bool,
    merge_existing: bool = False,
) -> None:
    by_path: dict[str, tuple[int, str]] = {}
    if merge_existing:
        prev = OUT_DIR / "PROVENANCE.md"
        if prev.is_file():
            for line in prev.read_text(encoding="utf-8").splitlines():
                if not line.startswith("| `"):
                    continue
                parts = [p.strip() for p in line.strip("|").split("|")]
                if len(parts) != 3 or parts[0] == "文件":
                    continue
                path = parts[0].strip("`")
                by_path[path] = (int(parts[1].replace(",", "")), parts[2].strip("`"))
    for path, size, blob in rows:
        by_path[path] = (size, blob)
    ordered = sorted(by_path.items(), key=lambda kv: kv[0])
    lines = [
        "# TurnBasedGameData provenance",
        "",
        f"- 来源 URL: https://github.com/{GITHUB_REPO}",
        f"- 备选 URL: https://gitlab.com/{GITLAB_PROJECT}（同一提交）",
        f"- 提交哈希: `{sha}`",
        f"- 游戏版本串: `{version}`",
        f"- 获取日期: {date.today().isoformat()}",
        f"- LICENSE 文件: {'有' if has_license else '无（仓库无 LICENSE / LICENSE.md / COPYING）'}",
        "",
        "字节数与 git blob SHA-1（`sha1(\"blob {size}\\0\" + bytes)`，与 GitHub contents API 的 `sha` 相同）：",
        "",
        "| 文件 | 字节数 | blob SHA-1 |",
        "|---|---:|---|",
    ]
    for path, (size, blob) in ordered:
        lines.append(f"| `{path}` | {size} | `{blob}` |")
    lines.append("")
    lines.append("由 `scripts/fetch_datamine.py` 写入。本目录不进入 git。")
    lines.append("")
    (OUT_DIR / "PROVENANCE.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit",
        default="4ce30f69b32d",
        help="Full or unique commit SHA (default: D1 pin 4ce30f69b32d)",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        default=None,
        help="Fetch only these repo-relative paths (merge into PROVENANCE)",
    )
    args = parser.parse_args()

    commit = github_commit(args.commit)
    sha = commit["sha"]
    version = commit["commit"]["message"].splitlines()[0].strip()
    print(f"commit {sha}")
    print(f"version {version}")
    pinned = lookup_pinned(PINNED_VERSION, sha) or lookup_pinned(PINNED_VERSION, args.commit)
    if pinned is not None and version != pinned:
        raise SystemExit(f"version string mismatch: got {version!r}, expected {pinned!r}")
    if args.commit not in (sha, sha[: len(args.commit)]) and not sha.startswith(args.commit):
        raise SystemExit(f"commit resolved to {sha}, not {args.commit}")

    has_license = github_has_license(sha)
    if has_license:
        print("WARNING: LICENSE file exists; D1 recorded none")
    else:
        print("LICENSE: absent")

    paths = tuple(args.only) if args.only else FILES
    for path in paths:
        if path not in FILES:
            raise SystemExit(f"path not in FILES allow-list: {path}")

    rows: list[tuple[str, int, str]] = []
    for path in paths:
        print(f"meta {path}")
        meta = github_file_meta(path, sha)
        _dest, blob, size = fetch_one(path, sha, meta)
        print(f"  ok {size} {blob}")
        rows.append((path, size, blob))

    write_provenance(
        sha=sha,
        version=version,
        rows=rows,
        has_license=has_license,
        merge_existing=bool(args.only),
    )
    print(f"wrote {OUT_DIR / 'PROVENANCE.md'}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code} {exc.reason}", file=sys.stderr)
        sys.exit(1)
