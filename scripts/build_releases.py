#!/usr/bin/env python3
"""Скачивает IPA из Drive-папки Site, создаёт релизы в soundtrackminus-hash/ios-install,
генерирует манифесты/QR/иконки, собирает catalog.json. По одному файлу, с резюмом."""
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

REPO = "soundtrackminus-hash/ios-install"
DRIVE_FOLDER = "1eMqsKgtbKsGgSP2_avqc5RC4voSs0nsz"
BASE = Path("/root/projects/ios-install")
OUT = BASE / "out"                # временные загрузки
DONE = BASE / "done.json"         # словарь slug -> {fileId, sha256, size}
LOG = BASE / "build.log"
MAX_ASSET = 2_000_000_000   # лимит одного ассета в GitHub Releases
TMPIPA = "/root/.cache/ipa_work"  # НЕ /tmp: там tmpfs 962 МБ, крупные IPA не влезают

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

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


def drive(c):
    d = build("drive", "v3", credentials=c, static_discovery=False)
    files = d.files().list(
        q="'%s' in parents and trashed=false" % DRIVE_FOLDER,
        fields="files(id,name,size)", pageSize=200).execute().get("files", [])
    return d, files


def download(d, file_id, dest):
    media = d.files().get_media(fileId=file_id)
    with open(dest, "wb") as fh:
        dl = MediaIoBaseDownload(fh, media, chunksize=4 * 1024 * 1024)
        prev = 0
        t0 = time.time()
        while True:
            status, done = dl.next_chunk()
            if status and status.progress() and status.progress() > prev + 0.05:
                prev = status.progress()
                mb = os.path.getsize(dest) / 1048576
                spd = mb / (time.time() - t0)
                print("    загружено %.1f МБ, %.1f МБ/с" % (mb, spd), flush=True)
            if done:
                break
    return os.path.getsize(dest)


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
                # читаем PNG-заголовок: ширина/высота с байта 16
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
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("gh %s: %s" % (" ".join(args), r.stderr.strip()[:400]))
    return r.stdout.strip()


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


def itms_link(slug):
    return "itms-services://?action=download-manifest&url=%s" % \
        ("https://soundtrackminus-hash.github.io/ios-install/apps/%s/manifest.plist" % slug)


def verify_asset(url, expected_size, wait=300):
    # GitHub CDN пробрасывает свежезалитый ассет не мгновенно:
    # проверено — URL отдаёт 404 первые ~2-4 минуты, потом 302->200.
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
    tag = "%s-%s" % (slug, ver.replace(".", "."))
    try:
        gh("release", "view", tag, "--repo", REPO, "--json", "name")
        return tag, True
    except RuntimeError:
        gh("release", "create", tag,
           "--repo", REPO, "--title", slug, "--notes", slug)
        return tag, False


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    creds = Credentials.from_authorized_user_file("/root/.hermes/google_token.json")
    creds.refresh(Request())
    d, files = drive(creds)
    if only:
        files = [f for f in files if only.lower() in f["name"].lower()]
    files = sorted(files, key=lambda f: f["name"])
    log("файлов в Drive-папке Site: %d" % len(files))
    os.makedirs(TMPIPA, exist_ok=True)
    done = {}
    if DONE.exists():
        done = json.loads(DONE.read_text())
    # Имена уже размещённых приложений: по ним отсекаем перезаливки.
    # Сравниваем через slugify — он транслитерирует кириллицу, в отличие от
    # голого re.sub(r"[^a-z0-9]+"), который схлопывает русские имена в "".
    placed = set()
    for _v in done.values():
        # Только успешные: у провалившихся записей тоже есть name, иначе
        # предохранитель пропускал бы их как «дубли» и они никогда не повторились бы.
        if _v.get("status") != "ok":
            continue
        _n = _v.get("name")
        if _n:
            placed.add(slugify(Path(_n).stem))
    log("уже размещено имён: %d" % len(placed))

    catalog = []

    for fi in files:
        name = fi["name"]
        fid = fi["id"]
        expect = int(fi["size"])
        if fid in done and done[fid].get("status") == "ok":
            log("skip %s (%s уже готов)" % (name, done[fid].get("slug")))
            prev = done[fid].get("entry")
            if prev:
                catalog.append(prev)
            else:
                log("  ВНИМАНИЕ: у %s нет сохранённого entry, каталог будет неполным" % name)
            continue
        # Дубль перезаливки: такое приложение уже размещено на сайте.
        # Проверяем ДО скачивания, чтобы не тянуть гигабайты впустую.
        if slugify(Path(name).stem) in placed:
            log("skip %s (дубль: такое уже размещено)" % name)
            continue
        # Ассет в GitHub Releases ограничен 2 ГБ — крупнее не загрузить,
        # поэтому не тратим на них время и место.
        if expect > MAX_ASSET:
            log("skip %s (%d МБ) — больше лимита ассета в 2 ГБ"
                % (name, expect // 1048576))
            continue
        ipa = os.path.join(TMPIPA, "%s.ipa" % fid)
        cur = ipa
        log("=== %s (%d МБ) ===" % (name, expect // 1048576))
        try:
            got = download(d, fid, ipa)
            if got != expect:
                raise RuntimeError("размер не сошёлся: %d != %d" % (got, expect))
            n, v, bundle, _ = ipa_info(ipa)
            slug = slugify(n)
            # защита от коллизий slug: добавить хвост bundleId
            used = [done[k]["slug"] for k in done if k != fid and "slug" in done[k]]
            if slug in used:
                base = slug
                tail = "".join(c for c in bundle if c.isalnum()).lower()[-4:]
                slug = "%s-%s" % (base, tail) if tail else "%s-x%s" % (base, fid[-4:])
            tag, existed = ensure_release(slug, v)
            asset_name = "%s.ipa" % slug
            upload_path = os.path.join(TMPIPA, asset_name)
            if os.path.abspath(upload_path) != os.path.abspath(ipa):
                os.replace(ipa, upload_path)
                cur = upload_path
            ipa_url = "https://github.com/%s/releases/download/%s/%s" % (REPO, tag, asset_name)
            ok, ct = verify_asset(ipa_url, expect)
            if not ok:
                if existed:
                    log("  релиз есть, а ассет отсутствует — дозагружаю")
                gh("release", "upload", tag, cur,
                   "--repo", REPO, "--clobber")
                ok, ct = verify_asset(ipa_url, expect)
            if not ok:
                raise RuntimeError("ассет не верифицируется на GitHub")
            title = n
            make_manifest(slug, v, bundle, title, ipa_url, expect)
            iconf = extract_icon(cur, None, slug)
            entry = {
                "name": title, "version": v, "slug": slug,
                "bundleId": bundle, "size": expect,
                "sizeFormatted": "%.1f МБ" % (expect / 1048576),
                "manifestUrl": "https://soundtrackminus-hash.github.io/ios-install/apps/%s/manifest.plist" % slug,
                "downloadUrl": ipa_url,
                "iconUrl": "icons/%s.png" % slug,
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
        finally:
            for p_ in (cur, ipa):
                if p_ and os.path.exists(p_):
                    os.unlink(p_)

    catalog.sort(key=lambda e: e["name"].lower())
    (BASE / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=1))
    log("ИТОГО в каталоге: %d" % len(catalog))


if __name__ == "__main__":
    main()