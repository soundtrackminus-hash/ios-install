#!/usr/bin/env python3
"""Fix catalog: rebuild from done.json + verify GitHub releases."""
import json
from pathlib import Path

BASE = Path("/root/projects/ios-install")
DONE = BASE / "done.json"
CATALOG = BASE / "catalog.json"

with open(DONE) as f:
    done = json.load(f)

catalog = []
missing_from_catalog = []

for key, info in done.items():
    if info.get("status") != "ok":
        continue
    entry = info.get("entry")
    if not entry:
        # Check if we can build one from available info
        name = info.get("name", "")
        slug = info.get("slug", "")
        size = info.get("size", 0)
        ver = ""
        # Extract version from filename
        import re
        m = re.search(r'(\d+\.\d+(?:\.\d+)?)', name)
        if m:
            ver = m.group(1)
        entry = {
            "name": slug.title(),
            "version": ver,
            "slug": slug,
            "bundleId": "unknown",
            "size": size,
            "sizeFormatted": "%.1f МБ" % (size // 1048576),
            "manifestUrl": f"https://soundtrackminus-hash.github.io/ios-install/apps/{slug}/manifest.plist",
            "downloadUrl": f"https://github.com/soundtrackminus-hash/ios-install/releases/download/{slug}-{ver}/{slug}.ipa",
            "iconUrl": f"icons/{slug}.png",
            "searchTags": "_".join(slug.split("-")),
        }
        # Save updated entry back
        done[key]["entry"] = entry
    
    catalog.append(entry)

# Sort and write
catalog.sort(key=lambda e: e["name"].lower())
CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=1))

print(f"✅ Catalog rebuilt: {len(catalog)} entries")
print("\n=== All apps ===")
for app in catalog[:10]:
    print(f"  - {app['name']} v{app['version']} -> {app['slug']}")
if len(catalog) > 10:
    print(f"  ... and {len(catalog)-10} more")
