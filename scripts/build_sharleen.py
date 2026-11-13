#!/usr/bin/env python3
"""Build Sharleen page: upload IPA from sharleenbarnts70584@hotmail.com"""

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

# Configuration
REPO = "soundtrackminus-hash/ios-install"
IPA_DIR = Path("/srv/storage/ipa/sharleenbarnts70584@hotmail.com")
BASE = Path("/root/projects/ios-install")
OUT = BASE / "out"
DONE = BASE / "done-sharleen.json"
LOG = BASE / "build-sharleen.log"
CATALOG = BASE / "catalog-sharleen.json"
SLUG_PREFIX = "sharleen-"
MAX_ASSET = 2_000_000_000

import requests


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def slugify(name):
    TR = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
        "А": "a", "Б": "b", "В": "v", "Г": "g", "Д": "d", "Е": "e",
        "Ё": "e", "Ж": "zh", "З": "z", "И": "i", "Й": "y", "К": "k",
        "Л": "l", "М": "m", "Н": "n", "О": "o", "П": "p", "Р": "r",
        "С": "s", "Т": "t", "У": "u", "Ф": "f", "Х": "kh", "Ц": "ts",
        "Ч": "ch", "Ш": "sh", "Щ": "shch", "Ъ": "", "Ы": "y", "Ь": "",
        "Э": "e", "Ю": "yu", "Я": "ya",
    }
    s = "".join(TR.get(c, c) for c in name)
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "app"


def ipa_info(ipa_path):
    with zipfile.ZipFile(ipa_path) as z:
        apps = [n for n in z.namelist()
                if re.match(r"Payload/[^/]+\.app/Info\.plist$", n)]
        if not apps:
            raise RuntimeError("no Info.plist in IPA")
        with z.open(apps[0]) as f:
            pl = plistlib.load(f)
        name = pl.get("CFBundleDisplayName") or pl.get("CFBundleName") or Path(ipa_path).stem
        ver = pl.get("CFBundleShortVersionString") or pl.get("CFBundleVersion") or "1.0"
        bundle = pl.get("CFBundleIdentifier", "unknown")
        return name, ver, bundle, apps[0]


def extract_icon(ipa_path, plist_path, slug):
    with zipfile.ZipFile(ipa_path) as z:
        names = z.namelist()
        cands = [n for n in names if re.match(r"Payload/[^/]+\.app/AppIcon[^/]*\.png$", n)]
        if not cands:
            cands = [n for n in names if ".app/" in n and n.endswith(".png")]
        if not cands:
            return None
        t0 = time.time()
        sizes = []
        for n in cands:
            info = z.getinfo(n)
            try:
                with z.open(n) as f:
                    hdr = f.read(24)
                if len(hdr) >= 24 and hdr[:8] == b"\x89PNG\r\n\x1a\n":
                    w, h = int.from_bytes(hdr[16:20], "big"), int.from_bytes(hdr[20:24], "big")
                    sizes.append((w * h, n, info.file_size))
            except Exception:
                pass
        if not sizes:
            return None
        sizes.sort(reverse=True, key=lambda x: x[0])
        _, chosen, _ = sizes[0]
        outdir = BASE / "icons"
        outdir.mkdir(parents=True, exist_ok=True)
        out = outdir / f"{slug}.png"
        with z.open(chosen) as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return out


def gh(*args):
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"gh {' '.join(args)}: TIMEOUT")
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)}: {r.stderr.strip()[:400]}")
    return r.stdout.strip()


def asset_uploaded(tag, asset_name, tries=6):
    for _i in range(tries):
        try:
            out = gh("release", "view", tag, "--repo", REPO, "--json", "assets")
            for a in json.loads(out).get("assets", []):
                if a.get("name") == asset_name and a.get("state") == "uploaded":
                    return True
        except Exception:
            pass
        time.sleep(5)
    return False


def make_manifest(slug, ver, bundle, title, ipa_url, ipa_size):
    out = BASE / "apps" / slug / "manifest.plist"
    out.parent.mkdir(parents=True, exist_ok=True)
    plistlib.dump({
        "items": [{
            "assets": [{
                "kind": "software-package",
                "url": ipa_url,
            }],
            "metadata": {
                "bundle-identifier": bundle,
                "bundle-version": ver,
                "kind": "software",
                "title": title,
            },
        }],
    }, open(out, "wb"))
    return out


def verify_asset(url, expected_size, wait=300):
    deadline = time.time() + wait
    delay = 10
    while time.time() < deadline:
        try:
            r = requests.head(url, allow_redirects=True, timeout=30)
            if r.status_code == 200 and r.headers.get("Content-Length") == str(expected_size):
                return True, r.headers.get("Content-Type", "?")
        except Exception:
            pass
        time.sleep(delay)
        delay = min(delay * 1.5, 60)
    return False, "?"


def ensure_release(slug, ver):
    tag = f"{slug}-{ver.replace('.', '.')}"
    try:
        gh("release", "view", tag, "--repo", REPO, "--json", "name")
        return tag, True
    except RuntimeError:
        gh("release", "create", tag,
           "--repo", REPO, "--title", slug, "--notes", slug)
        return tag, False


def main():
    if not IPA_DIR.exists():
        raise RuntimeError(f"IPA-каталог не существует: {IPA_DIR}")
    files = []
    for f in sorted(IPA_DIR.glob("*.ipa")):
        files.append({
            "id": str(f),
            "name": f.name,
            "size": f.stat().st_size
        })
    files = sorted(files, key=lambda f: f["name"])
    log(f"файлов в локальной папке: {len(files)}")
    done = {}
    if DONE.exists():
        done = json.loads(DONE.read_text())
    placed = set()
    for _v in done.values():
        if _v.get("status") != "ok":
            continue
        _n = _v.get("name")
        if _n:
            placed.add(slugify(Path(_n).stem))
    log(f"уже размещено имён: {len(placed)}")
    catalog = []
    for fi in files:
        name = fi["name"]
        fid = fi["id"]
        expect = fi["size"]
        if fid in done and done[fid].get("status") == "ok":
            log(f"skip {name} ({done[fid].get('slug')} уже готов)")
            prev = done[fid].get("entry")
            if prev:
                catalog.append(prev)
            else:
                log(f"  ВНИМАНИЕ: у {name} нет сохранённого entry, каталог будет неполным")
            continue
        if slugify(Path(name).stem) in placed:
            log(f"skip {name} (дубль: такое уже размещено)")
            continue
        if expect > MAX_ASSET:
            log(f"skip {name} ({expect // 1048576} МБ) — больше лимита ассета в 2 ГБ")
            continue
        ipa = Path(fid)
        cur = ipa
        log(f"=== {name} ({expect // 1048576} МБ) ===")
        try:
            got = ipa.stat().st_size
            if got != expect:
                raise RuntimeError(f"размер не сошёлся: {got} != {expect}")
            n, v, bundle, _ = ipa_info(ipa)
            slug = SLUG_PREFIX + slugify(n)
            used = [done[k]["slug"] for k in done if k != fid and "slug" in done[k]]
            if slug in used:
                base = slug
                tail = "".join(c for c in bundle if c.isalnum()).lower()[-4:]
                slug = f"{base}-{tail}" if tail else f"{base}-x{fid[-4:]}"
            tag, existed = ensure_release(slug, v)
            asset_name = fi["name"]  # use original file name
            ipa_url = f"https://github.com/{REPO}/releases/download/{tag}/{asset_name}"
            if existed:
                ok, ct = verify_asset(ipa_url, expect, wait=30)
                if not ok:
                    log("  релиз есть, а ассет отсутствует — дозагружаю")
                else:
                    log("  ассет уже на GitHub, пропускаю загрузку")
            else:
                ok, ct = False, "?"
            if not ok:
                gh("release", "upload", tag, str(ipa),
                   "--repo", REPO, "--clobber")
                ok, ct = asset_uploaded(tag, asset_name), "application/octet-stream"
            if not ok:
                raise RuntimeError("ассет не появился на GitHub")
            title = n
            make_manifest(slug, v, bundle, title, ipa_url, expect)
            iconf = extract_icon(str(ipa), None, slug)
            entry = {
                "name": title, "version": v, "slug": slug,
                "bundleId": bundle, "size": expect,
                "sizeFormatted": f"{expect / 1048576:.1f} МБ",
                "manifestUrl": f"https://soundtrackminus-hash.github.io/ios-install/apps/{slug}/manifest.plist",
                "downloadUrl": ipa_url,
                "iconUrl": f"https://soundtrackminus-hash.github.io/ios-install/icons/{slug}.png",
                "searchTags": "_".join(slug.split("-")),
            }
            catalog.append(entry)
            done[fid] = {"fileId": fid, "status": "ok", "size": expect,
                         "name": name, "slug": slug, "entry": entry}
            DONE.write_text(json.dumps(done, ensure_ascii=False, indent=1))
            log(f"OK {title} v{v} slug={slug} ({expect // 1048576} МБ, {ct})")
        except Exception as e:
            done[fid] = {"fileId": fid, "status": f"err:{e}", "name": name}
            DONE.write_text(json.dumps(done, ensure_ascii=False, indent=1))
            log(f"FAIL {name} : {e}")
        finally:
            pass
    catalog.sort(key=lambda e: e["name"].lower())
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=1))
    log(f"ИТОГО в каталоге: {len(catalog)}")
if __name__ == "__main__":
    main()