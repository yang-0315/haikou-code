"""Prove that touching a cached document re-discovers its repeal clause."""
import json
import os
import shutil
import sys

sys.path.insert(0, r"C:\Users\Lenovo\Desktop\PageIndex\haikou")

import docstore

from paths import DEFAULT_DATABASE as DB
INDEX = os.path.join(DB, "_embedded_repeals.json")


def main():
    backup = INDEX + ".bak"
    if os.path.exists(INDEX):
        shutil.copy(INDEX, backup)
        rows = json.load(open(INDEX, encoding="utf-8"))
        print(f"索引原有 {len(rows)} 条，备份到 {os.path.basename(backup)}")

    # keep only one record so the cache scan has something to rediscover
    keep = [r for r in rows if "免签证" not in r["source_title"]][:1]
    with open(INDEX, "w", encoding="utf-8") as fh:
        json.dump(keep, fh, ensure_ascii=False, indent=2)
    print(f"临时缩减为 {len(keep)} 条（保留的是别的记录）\n")

    metadata = docstore.load_metadata(DB)
    target = next((item for item in metadata
                   if "免签证" in item["title"]), None)
    if not target:
        print("找不到测试文档")
        return
    print(f"取用缓存中的《{target['title']}》")
    text, how = docstore.ensure(DB, target)
    print(f"  ensure() → {how}")

    rows = json.load(open(INDEX, encoding="utf-8"))
    print(f"  索引现在 {len(rows)} 条")
    for row in rows:
        print(f"    {row['source_title'][:34]} → {row['repeals']}"
              f"  来源={row.get('discovered_by')}")


if __name__ == "__main__":
    main()
