"""Save the original text of a repealed document into the corpus."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from repeal import body_text, document_numbers, fetch, meta_fields

from paths import DEFAULT_DATABASE as DB
INDEX = os.path.join(DB, "_repealed_originals.json")


def main():
    found = json.load(open(INDEX, encoding="utf-8"))
    for title, info in found.items():
        html = fetch(info["url"])
        meta = meta_fields(html)
        text = body_text(html, meta)
        name = re.sub(r"[\\/:*?\"<>|]", "_", title)
        path = os.path.join(DB, f"{name}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"[saved] {os.path.basename(path)}  {len(text)} 字")
        print(f"        来源: {info['url']}")
        print(f"        发布: {meta.get('ContentSource')}  {meta.get('PubDate')}")
        print(f"        文号: {document_numbers(text) or '未识别'}")
    print(f"\n语料目录现有: "
          f"{[f for f in sorted(os.listdir(DB)) if f.endswith('.txt')]}")


if __name__ == "__main__":
    main()
