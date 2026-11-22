"""Are the documents the repeal notices name even in the catalogue?"""
import json
import os
import re
import sys

sys.path.insert(0, r"C:\Users\Lenovo\Desktop\PageIndex\haikou")

import docstore

from paths import DEFAULT_DATABASE as DB


def norm(text: str) -> str:
    return re.sub(r"[\s《》〈〉（）()、，,。．.\-—]", "", text)


def main():
    index = json.load(open(os.path.join(DB, "_repeal_index.json"),
                           encoding="utf-8"))
    metadata = docstore.load_metadata(DB)
    catalogue = [norm(m["title"]) for m in metadata]
    cached = [norm(os.path.splitext(os.path.basename(p))[0])
              for p in docstore.library_files(DB)]

    named = []
    for notice in index["notices"] + index.get("embedded", []):
        for name in notice["repealed_documents"]:
            key = norm(name)
            if key and key not in named:
                named.append(key)

    in_catalogue, fetched, absent = [], [], []
    for key in named:
        hit = next((c for c in catalogue if key in c or c in key), None)
        if not hit:
            absent.append(key)
            continue
        in_catalogue.append(key)
        if any(key in c or c in key for c in cached):
            fetched.append(key)

    print(f"废止依据点名的文件 {len(named)} 份\n")
    print(f"  目录里能找到      {len(in_catalogue)}（其中已抓正文 {len(fetched)}）")
    print(f"  目录里根本没有    {len(absent)}")
    print("\n目录里没有的（平台已下线，抓不到）：")
    for key in absent[:14]:
        print(f"    - {key[:46]}")
    if len(absent) > 14:
        print(f"    … 还有 {len(absent) - 14} 份")
    pending = [k for k in in_catalogue if k not in fetched]
    print("\n在目录里但正文未抓：")
    for key in pending[:10]:
        print(f"    - {key[:46]}")
    if not pending:
        print("    无")
    print(f"\n结论：提高「已废止」识别的可抓上限是 {len(pending)} 份；"
          f"另有 {len(absent)} 份已无法从本目录获取。")


if __name__ == "__main__":
    main()
