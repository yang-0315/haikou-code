"""How far does the automated force annotation actually reach?"""
import json
import os
import re
from collections import Counter

from paths import DEFAULT_DATABASE as DB


def main():
    index = json.load(open(os.path.join(DB, "_repeal_index.json"),
                           encoding="utf-8"))
    notices = index["notices"]
    affected = index.get("corpus_affected", [])
    rejected = index.get("rejected_pages", [])

    print("== 废止文件层 ==")
    print(f"  有效废止文件      {len(notices)}")
    print(f"  剔除的假阳性页面  {len(rejected)}")
    print(f"  已命中语料        {len(affected)}")
    complete = [n for n in notices if n.get("list_complete", True)]
    incomplete = [n for n in notices if not n.get("list_complete", True)]
    print(f"  清单完整          {len(complete)}")
    print(f"  清单不完整        {len(incomplete)}")
    with_number = [n for n in notices if n.get("repealed_numbers")]
    print(f"  带被废止文号      {len(with_number)}")

    print("\n== 被点名的文件 ==")
    names = []
    for notice in notices:
        names.extend(re.sub(r"\s+", "", x) for x in notice["repealed_documents"])
    unique = sorted(set(names))
    print(f"  去重后 {len(unique)} 份")
    repeated = [n for n, c in Counter(names).items() if c > 1]
    print(f"  被点名两次以上 {len(repeated)} 份")

    print("\n== 语料层 ==")
    status_path = os.path.join(DB, "_force_status.json")
    if os.path.exists(status_path):
        status = json.load(open(status_path, encoding="utf-8"))
        print(f"  语料 {len(status)} 份")
        print(f"  状态: {dict(Counter(v['status'] for v in status.values()))}")
        print(f"  置信度: {dict(Counter(v['confidence'] for v in status.values()))}")
        for _, item in status.items():
            if item["status"] != "现行有效":
                print(f"    ⚠ {item['title'][:36]} → {item['status']}"
                      f"（{item['confidence']}）")

    print("\n== 本管线尚未覆盖的失效方式 ==")
    for kind in ("文件自带有效期届满",
                 "清理决定中列在附件里的条目",
                 "被上位法取代（现已按年代+修订记录降级为存疑）"):
        print(f"  - {kind}")


if __name__ == "__main__":
    main()
