"""Audit the repeal index: where does the parser get it wrong?"""
import json
import os
import re

from paths import DEFAULT_DATABASE as DB

COUNT_HINT = re.compile(r"等\s*([一二三四五六七八九十百\d]+)\s*(?:件|个|部|项)")
CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
      "八": 8, "九": 9, "十": 10}


def cn_int(text: str) -> int:
    if text.isdigit():
        return int(text)
    if "十" not in text:
        return sum(CN.get(ch, 0) for ch in text)
    head, _, tail = text.partition("十")
    return (CN.get(head, 1) if head else 1) * 10 + sum(CN.get(c, 0) for c in tail)


def main():
    index = json.load(open(os.path.join(DB, "_repeal_index.json"),
                           encoding="utf-8"))
    notices = index["notices"]
    print(f"审计 {len(notices)} 份废止文件\n")

    problems = []
    for notice in notices:
        title = notice["notice_title"]
        names = notice["repealed_documents"]
        flags = []

        hint = COUNT_HINT.search(title)
        expected = cn_int(hint.group(1)) if hint else None
        if expected and len(names) < expected:
            flags.append(f"数量不符：标题说 {expected} 件，只识别出 {len(names)} 件")

        if any("&lt;" in n or "&gt;" in n or "&amp;" in n for n in names):
            flags.append("实体未解码（&lt; / &gt; 混入文件名）")

        own = re.sub(r"\s+", "", title)
        if any(re.sub(r"\s+", "", n) == own for n in names):
            flags.append("把通知自己的标题当成了被废止文件")

        if "修改" in title and "废止" in title:
            flags.append("混合决定：同一文件既修改又废止，需分别归类")

        if not notice.get("issuer"):
            flags.append("未识别发布机关")
        if not notice.get("effective_date"):
            flags.append("未识别生效日期")
        if not names:
            flags.append("未识别出任何被废止文件")

        if notice.get("not_a_repeal_notice"):
            flags = ["抓到的页面其实不是废止文件（列表页标题与文章标题不一致）"]
        elif not notice.get("list_complete", True):
            flags.append(notice.get("list_note", "清单不完整"))

        if flags:
            problems.append((title, flags))

    print(f"有问题的 {len(problems)} 份：\n")
    for title, flags in problems:
        print(f"◆ {title[:56]}")
        for flag in flags:
            print(f"    - {flag}")

    print("\n== 问题类型统计 ==")
    counter = {}
    for _, flags in problems:
        for flag in flags:
            key = flag.split("：")[0]
            counter[key] = counter.get(key, 0) + 1
    for key, value in sorted(counter.items(), key=lambda kv: -kv[1]):
        print(f"  {value:>3}  {key}")


if __name__ == "__main__":
    main()
