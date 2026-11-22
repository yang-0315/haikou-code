"""For each 效力存疑 document, is a replacement anywhere in the catalogue?"""
import os
import re
import sys

sys.path.insert(0, r"C:\Users\Lenovo\Desktop\PageIndex\haikou")

import docstore
from targeted_fetch import questionable

from paths import DEFAULT_DATABASE as DB


def keywords(title: str) -> list[str]:
    """Distinctive two-to-four character phrases from a title."""
    clean = re.sub(r"(海南省|海南经济特区|人民政府|办公厅|关于|的通知|的决定|办法|"
                   r"规定|实施细则|若干|修订|暂行|试行)", "", title)
    clean = re.sub(r"[\s《》〈〉（）()、，,。．.\-—]", "", clean)
    return [clean[i:i + 3] for i in range(0, max(len(clean) - 2, 1))]


def main():
    metadata = docstore.load_metadata(DB)
    suspects = questionable(DB)
    print(f"存疑 {len(suspects)} 份，逐份看目录里有没有同主题文件\n")
    for suspect in suspects:
        title = suspect["title"]
        keys = [k for k in keywords(title) if len(k) >= 3]
        matches = []
        for item in metadata:
            if item["title"] == title:
                continue
            other = item["title"]
            score = sum(1 for k in keys if k in other)
            if score >= 2:
                matches.append((score, other))
        matches.sort(reverse=True)
        print(f"◆ {title[:40]}")
        if matches:
            for score, other in matches[:3]:
                print(f"    相似度 {score:>2}  {other[:52]}")
        else:
            print("    目录中无同主题文件")


if __name__ == "__main__":
    main()
