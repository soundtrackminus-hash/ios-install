#!/usr/bin/env python3
"""Скачивает IPA из локальной папки, создаёт релизы в soundtrackminus-hash/ios-install,
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
IPA_DIR = Path("/srv/storage/ipa/selimseidov@gmail.com")  # Локальная папка с IPA
BASE = Path("/root/projects/ios-install")
OUT = BASE / "out"                # временные загрузки
DONE = BASE / "done.json"         # словарь slug -> {fileId, sha256, size}
LOG = BASE / "build.log"
MAX_ASSET = 2_000_000_000   # лимит одного ассета в GitHub Releases
TMPIPA = "/root/.cache/ipa_work"  # НЕ /tmp: там tmpfs 962 МБ, крупные IPA не влезают

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


def get_ipa_files():
    """Получает список IPA файлов из локальной папки."""
    if not IPA_DIR.exists():
        raise RuntimeError(f"Папка {IPA_DIR} не существует")
    
    files = []
    for f in IPA_DIR.glob("*.ipa"):
        files.append({
            "id": str(f),  # Используем путь как ID
            "name": f.name,
            "size": f.stat().st_size
        })
    return files


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
                "title": title,
                "version": ver,
                "subtitle": title,
            },
        }]
    }, open(out, "wb"))
    return out


def ensure_release(slug, version):
    tag = "%s-%s" % (slug, version)
    try:
        gh("release", "view", tag, "--repo", REPO)
        return tag, True
    except RuntimeError:
        gh("release", "create", tag,
           "--title", "%s %s" % (slug.capitalize(), version),
           "--notes", "Auto-generated release for %s v%s" % (slug, version),
           "--repo", REPO)
        return tag, False


def verify_asset(url, expect_size):
    """Проверяет что ассет доступен на GitHub и имеет верный размер."""
    r = requests.head(url, allow_redirects=True, timeout=30)
    if r.status_code != 200:
        return False, None
    actual = int(r.headers.get("Content-Length", 0))
    if actual != expect_size:
        return False, None
    ct = r.headers.get("Content-Type", "application/octet-stream")
    return True, ct


def main():
    # Читаем токен
    token_path = Path("/root/.hermes/google_token.json")
    if not token_path.exists():
        print("ERROR: token file not found at %s" % token_path)
        sys.exit(1)
    
    with open(token_path) as f:
        token_data = json.load(f)
    
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    
    creds = Credentials(
        token=token_data['token'],
        refresh_token=token_data.get('refresh_token'),
        token_uri=token_data['token_uri'],
        client_id=token_data['client_id'],
        client_secret=token_data.get('client_secret')
    )
    creds.refresh(Request())
    
    # Создаём папку для кэша
    os.makedirs(TMPIPA, exist_ok=True)
    
    # Читаем done.json
    if DONE.exists():
        done = json.loads(DONE.read_text())
    else:
        done = {}
    
    log("=== ЗАПУСК ===")
    log("Папка с IPA: %s" % IPA_DIR)
    log("Уже размещено: %d" % len([v for v in done.values() if v.get("status") == "ok"]))
    
    # Получаем список IPA файлов с диска
    files = get_ipa_files()
    log("файлов в папке IPA: %d" % len(files))
    
    # Сбор уже размещённых слогов для проверки дублей
    placed = set()
    for _v in done.values():
        if _v.get("status") != "ok":
            continue
        _n = _v.get("name")
        if _n:
            placed.add(slugify(Path(_n).stem))
    log("уже размещено имён: %d" % len(placed))
    
    catalog = []
    
    for fi in files:
        name = fi["name"]
        fid = fi["id"]  # Путь к файлу как ID
        expect = int(fi["size"])
        
        # Проверяем, не обработан ли уже
        if fid in done and done[fid].get("status") == "ok":
            log("skip %s (%s уже готов)" % (name, done[fid].get("slug")))
            prev = done[fid].get("entry")
            if prev:
                catalog.append(prev)
            else:
                log("  ВНИМАНИЕ: у %s нет сохранённого entry, каталог будет неполным" % name)
            continue
        
        # Дубль перезаливки
        if slugify(Path(name).stem) in placed:
            log("skip %s (дубль: такое уже размещено)" % name)
            continue
        
        # Проверка размера
        if expect > MAX_ASSET:
            log("skip %s (%d МБ) — больше лимита ассета в 2 ГБ"
                % (name, expect // 1048576))
            continue
        
        ipa = fid  # Файл уже на диске, не скачиваем
        cur = ipa
        log("=== %s (%d МБ) ===" % (name, expect // 1048576))
        
        try:
            # Копируем в кэш если нужно
            if os.path.abspath(cur) != os.path.abspath(ipa):
                shutil.copy2(ipa, cur)
            
            n, v, bundle, _ = ipa_info(cur)
            slug = slugify(n)
            # защита от коллизий slug
            used = [done[k]["slug"] for k in done if k != fid and "slug" in done[k]]
            if slug in used:
                base = slug
                tail = "".join(c for c in bundle if c.isalnum()).lower()[-4:]
                slug = "%s-%s" % (base, tail) if tail else "%s-x%s" % (base, fid[-4:])
            
            tag, existed = ensure_release(slug, v)
            asset_name = "%s.ipa" % slug
            upload_path = os.path.join(TMPIPA, asset_name)
            if os.path.abspath(upload_path) != os.path.abspath(cur):
                shutil.copy2(cur, upload_path)
                cur = upload_path
            
            ipa_url = "https://github.com/%s/releases/download/%s/%s" % (REPO, tag, asset_name)
            ok, ct = verify_asset(ipa_url, expect)
            if not ok:
                if existed:
                    log("  релиз есть, а ассет отсутствует — загружаю")
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
    
    catalog.sort(key=lambda e: e["name"].lower())
    (BASE / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=1))
    log("ИТОГО в каталоге: %d" % len(catalog))


if __name__ == "__main__":
    main()
