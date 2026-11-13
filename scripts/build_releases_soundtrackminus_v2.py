#!/usr/bin/env python3
"""Загружает IPA из /srv/storage/ipa/soundtrackminus@gmail.com напрямую в GitHub Releases,
создаёт манифесты и иконки, обновляет catalog-soundtrackminus.json.
Использует префикс 'sm-' для новых приложений, чтобы не конфликтовать с 'o-' из другого каталога."""
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

REPO = "soundtrackminus-hash/ios-install"
IPA_DIR = Path("/srv/storage/ipa/soundtrackminus@gmail.com")
BASE = Path("/root/projects/ios-install")
DONE = BASE / "done-soundtrackminus.json"
LOG = BASE / "build-soundtrackminus-v2.log"
CATALOG = BASE / "catalog-soundtrackminus.json"
SLUG_PREFIX = "sm-"
MAX_ASSET = 2_000_000_000

import requests

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


def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def slugify(name):
    s = "".join(TR.get(c, c) for c in name)
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "app"


def ipa_info(ipa_path):
    with zipfile.ZipFile(ipa_path) as z:
        apps = [n for n in z.namelist() if re.match(r"Payload/[^/]+\.app/Info\.plist$", n)]
        if not apps:
            raise RuntimeError("no Info.plist in IPA")
        with z.open(apps[0]) as f:
            pl = plistlib.load(f)
        name = pl.get("CFBundleDisplayName") or pl.get("CFBundleName") or Path(ipa_path).stem
        ver = pl.get("CFBundleShortVersionString") or pl.get("CFBundleVersion") or "1.0"
        bundle = pl.get("CFBundleIdentifier", "unknown")
        return name, ver, bundle, apps[0]


def extract_icon(ipa_path, slug):
    with zipfile.ZipFile(ipa_path) as z:
        names = z.namelist()
        cands = [n for n in names if re.match(r"Payload/[^/]+\.app/AppIcon[^/]*\.png$", n)]
        if not cands:
            cands = [n for n in names if ".app/" in n and n.endswith(".png")]
        if not cands:
            return None
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
        out = outdir / ("%s.png" % slug)
        with z.open(chosen) as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return out


def gh(*args):
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired:
        raise RuntimeError("gh %s: TIMEOUT" % " ".join(args))
    if r.returncode != 0:
        raise RuntimeError("gh %s: %s" % (" ".join(args), r.stderr.strip()[:400]))
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


def make_manifest(slug, ver, bundle, title, ipa_url):
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


def main():
    print("DEBUG: main started", flush=True)
    if not IPA_DIR.exists():
        raise RuntimeError("IPA-каталог не существует: %s" % IPA_DIR)
    files = []
    for f in sorted(IPA_DIR.glob("*.ipa")):
        files.append({
            "id": str(f),
            "name": f.name,
            "size": f.stat().st_size
        })
    log("файлов в локальной папке: %d" % len(files))
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
    log("уже размещено имён: %d" % len(placed))

    catalog = []
    if CATALOG.exists():
        catalog = json.loads(CATALOG.read_text())
    existing_slugs = {e.get("slug") for e in catalog}

    for fi in files:
        name = fi["name"]
        fid = fi["id"]
        expect = fi["size"]
        if fid in done and done[fid].get("status") == "ok":
            log("skip %s (уже готов)" % name)
            prev = done[fid].get("entry")
            if prev:
                catalog.append(prev)
            continue
        if slugify(Path(name).stem) in placed:
            log("skip %s (дубль)" % name)
            continue
        if expect > MAX_ASSET:
            log("skip %s (%d МБ) — больше лимита" % (name, expect // 1048576))
            continue
        ipa = Path(fid)
        log("=== %s (%d МБ) ===" % (name, expect // 1048576))
        try:
            got = ipa.stat().st_size
            if got != expect:
                raise RuntimeError("размер не сошёлся: %d != %d" % (got, expect))
            n, v, bundle, _ = ipa_info(ipa)
            slug = SLUG_PREFIX + slugify(n)
            used = [done[k]["slug"] for k in done if k != fid and "slug" in done[k]]
            if slug in used:
                base = slug
                tail = "".join(c for c in bundle if c.isalnum()).lower()[-4:]
                slug = "%s-%s" % (base, tail) if tail else "%s-x%s" % (base, fid[-4:])
            tag = "%s-%s" % (slug, v.replace(".", "."))
            asset_name = "%s.ipa" % slug
            ipa_url = "https://github.com/%s/releases/download/%s/%s" % (REPO, tag, asset_name)
            try:
                gh("release", "view", tag, "--repo", REPO, "--json", "name")
                existed = True
            except RuntimeError:
                gh("release", "create", tag, "--repo", REPO, "--title", slug, "--notes", slug)
                existed = False
            if existed:
                ok, ct = asset_uploaded(tag, asset_name), "application/octet-stream"
                if not ok:
                    log("  релиз есть, а ассет отсутствует — дозагружаю")
            else:
                ok, ct = False, "?"
            if not ok:
                gh("release", "upload", tag, str(ipa), "--repo", REPO, "--clobber")
                ok, ct = asset_uploaded(tag, asset_name), "application/octet-stream"
            if not ok:
                raise RuntimeError("ассет не появился на GitHub")
            title = n
            make_manifest(slug, v, bundle, title, ipa_url)
            iconf = extract_icon(str(ipa), slug)
            entry = {
                "name": title, "version": v, "slug": slug,
                "bundleId": bundle, "size": expect,
                "sizeFormatted": "%.1f МБ" % (expect / 1048576),
                "manifestUrl": "https://soundtrackminus-hash.github.io/ios-install/apps/%s/manifest.plist" % slug,
                "downloadUrl": ipa_url,
                "iconUrl": "https://soundtrackminus-hash.github.io/ios-install/icons/%s.png" % slug,
                "searchTags": "_".join(slug.split("-")),
            }
            catalog.append(entry)
            done[fid] = {"fileId": fid, "status": "ok", "size": expect,
                         "name": name, "slug": slug, "entry": entry}
            DONE.write_text(json.dumps(done, ensure_ascii=False, indent=1))
            log("OK %s v%s slug=%s (%d МБ, %s)" % (title, v, slug, expect // 1048576, ct))
        except Exception as e:
            done[fid] = {"fileId": fid, "status": "err:%s" % e, "name": name}
            DONE.write_text(json.dumps(done, ensure_ascii=False, indent=1))
            log("FAIL %s : %s" % (name, e))

    # Сортируем и сохраняем каталог
    catalog.sort(key=lambda e: e["name"].lower())
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=1))
    log("ИТОГО в каталоге: %d" % len(catalog))


if __name__ == "__main__":
    main()
