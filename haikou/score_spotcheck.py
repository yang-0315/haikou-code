"""Score the manual spot-check and look for a fixable pattern in the misses."""
import os
import re
import sys

sys.path.insert(0, r"C:\Users\Lenovo\Desktop\PageIndex\haikou")

from paths import DEFAULT_DATABASE as DB
SPOT = os.path.join(DB, "_spotcheck.md")


def parse() -> list[dict]:
    text = open(SPOT, encoding="utf-8").read()
    rows = []
    for block in re.split(r"\n### ", text)[1:]:
        title = block.split("\n", 1)[0].strip()
        title = re.sub(r"^\d+\.\s*", "", title)
        system = re.search(r"系统判定：\*\*(.+?)\*\*", block)
        tail = block.split("请核对**：", 1)[-1].split("\n")[0]
        human = re.search(r"）\s*(.+)$", tail)
        rows.append({
            "title": title,
            "system": system.group(1) if system else "?",
            "human_raw": human.group(1).strip() if human else "",
        })
    return rows


def main():
    rows = parse()
    for row in rows:
        raw = row["human_raw"]
        row["human_valid"] = raw.startswith("有效")
    flagged = [r for r in rows if r["system"] != "现行有效"]
    pass_ = [r for r in rows if r["system"] == "现行有效"]
    misses = [r for r in pass_ if not r["human_valid"]]
    agree = [r for r in pass_ if r["human_valid"]]

    print(f"抽检 {len(rows)} 份\n")
    print("== 一致性 ==")
    print(f"  系统判现行有效 {len(pass_)} 份：人工同意 {len(agree)}，"
          f"漏报 {len(misses)}（{len(misses)/max(len(pass_),1):.0%}）")
    print(f"  系统判已废止   {len(flagged)} 份："
          f"人工同意 {sum(1 for r in flagged if not r['human_valid'])}，"
          f"人工不同意 {sum(1 for r in flagged if r['human_valid'])}")

    if misses:
        print("\n== 漏报明细（系统说现行有效，人工说无效）==")
        for row in misses:
            print(f"  ✗ {row['title'][:44]}")
            print(f"      人工备注：{row['human_raw']}")

    if any(r["human_valid"] for r in flagged):
        print("\n== 需澄清 ==")
        for row in flagged:
            if row["human_valid"]:
                print(f"  ? {row['title'][:44]}")
                print(f"      系统：已废止（依据文号匹配）")
                print(f"      人工：{row['human_raw']}")

    print("\n== 漏报的共性：是否能在库里找到同主题的新文件 ==")
    corpus = {}
    root = os.path.join(DB, "library")
    for folder, _, files in os.walk(root):
        for name in files:
            if name.endswith(".txt"):
                corpus[os.path.splitext(name)[0]] = os.path.join(folder, name)

    def key(title):
        t = re.sub(r"(海南省|海南经济特区|办法|规定|细则|若干|的通知|实施细则)", "",
                   title)
        return re.sub(r"[\s《》〈〉（）()、，,。．.\-—]", "", t)

    for row in misses:
        target = key(row["title"])
        candidates = []
        for title, path in corpus.items():
            if title == row["title"]:
                continue
            other = key(title)
            if len(other) < 2 or len(target) < 2:
                continue
            shared = len(set(target) & set(other)) / max(len(set(target)), 1)
            if shared > 0.5:
                candidates.append((shared, title))
        candidates.sort(reverse=True)
        print(f"\n  ▸ {row['title'][:40]}")
        for shared, title in candidates[:3]:
            print(f"      相似 {shared:.0%}  {title[:46]}")
        if not candidates:
            print("      库内未见同主题文件")


if __name__ == "__main__":
    main()
