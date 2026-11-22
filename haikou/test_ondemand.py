"""Prove the on-demand fetch: a document not yet cached gets fetched on request."""
import os
import sys

sys.path.insert(0, r"C:\Users\Lenovo\Desktop\PageIndex\haikou")

import docstore

from paths import DEFAULT_DATABASE as DB


def main():
    metadata = docstore.load_metadata(DB)
    cached_names = {os.path.basename(p) for p in docstore.library_files(DB)}
    print(f"元数据 {len(metadata)} 份，已缓存正文 {len(cached_names)} 份\n")

    targets = [item for item in metadata
               if docstore.safe_name(item["title"]) + ".txt" not in cached_names]
    print(f"未缓存 {len(targets)} 份，取前 3 份演示按需抓取:\n")

    for item in targets[:3]:
        print(f"◆ {item['title'][:48]}")
        print(f"   栏目 {item['channel']}")
        path = docstore.cache_path(DB, item)
        print(f"   抓前是否在缓存: {os.path.exists(path)}")
        text, how = docstore.ensure(DB, item)
        print(f"   ensure() → {how}，正文 {len(text) if text else 0} 字")
        print(f"   抓后是否在缓存: {os.path.exists(path)}")
        if text:
            print(f"   开头: {text[:90]}")
        print()

    print(f"缓存总数现在: {len(docstore.library_files(DB))}")


if __name__ == "__main__":
    main()
