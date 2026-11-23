"""Why are named-but-present documents not flagged as repealed?"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import docstore
from warn import load_index, own_numbers, status_for

from paths import DEFAULT_DATABASE as DB


def norm(text: str) -> str:
    return re.sub(r"[\s《》〈〉（）()、，,。．.\-—]", "", text)


def main():
    index = load_index(DB)
    metadata = docstore.load_metadata(DB)
    files = docstore.library_files(DB)
    by_title = {}
    for path in files:
        by_title[os.path.splitext(os.path.basename(path))[0]] = path

    named = []
    for notice in index["notices"] + index.get("embedded", []):
        for name in notice["repealed_documents"]:
            if name not in named:
                named.append(name)

    print("被点名且库里有正文的文件，逐个看现在判成什么：\n")
    checked = 0
    for name in named:
        key = norm(name)
        target = None
        for title, path in by_title.items():
            if key in norm(title) or norm(title) in key:
                target = (title, path)
                break
        if not target:
            continue
        checked += 1
        if checked > 12:
            continue
        title, path = target
        text = open(path, encoding="utf-8", errors="ignore").read()
        status = status_for({"file": os.path.basename(path), "title": title,
                             "doc_numbers": own_numbers(text),
                             "text": text[:400]}, index)
        print(f"◆ 点名: {name[:40]}")
        print(f"  库内: {title[:40]}")
        print(f"  判定: {status['status']}  "
              f"依据: {(status['evidence'].get('matched_by') or status['reason']['code'])[:40]}")
        print()


if __name__ == "__main__":
    main()
