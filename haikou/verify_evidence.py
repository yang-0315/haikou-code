"""Verify one repeal evidence chain end to end, from the notice's own text."""
import json
import os
import re
import sys

sys.path.insert(0, r"C:\Users\Lenovo\Desktop\PageIndex\haikou")

from repeal import body_text, fetch, meta_fields

from paths import DEFAULT_DATABASE as DB
URL = ("https://www.hainan.gov.cn/hainan/xxyxzfgz/201710/"
       "98c57cd0b41b4ff9b4ef51b2c9a9e0a5.shtml")
CLAIMED = ["海南省人民政府令第106号", "海南省人民政府令第195号",
           "海南省人民政府令第230号"]


def main():
    index = json.load(open(os.path.join(DB, "_repeal_index.json"),
                           encoding="utf-8"))
    notice = next((n for n in index["notices"] if "等5件规章" in n["notice_title"]),
                  None)
    if not notice:
        print("索引里找不到该通知")
        return
    print(f"通知：{notice['notice_title'][:52]}")
    print(f"URL：{notice['url']}")
    print(f"索引记录的文号：{notice.get('repealed_numbers')}")
    print(f"索引记录的文件名：{notice['repealed_documents']}\n")

    url = notice["url"] or URL
    html = fetch(url)
    text = body_text(html, meta_fields(html))
    print(f"重新抓取正文 {len(text)} 字\n")
    for number in CLAIMED:
        hit = number in re.sub(r"\s+", "", text)
        print(f"  正文是否出现 {number}: {hit}")
    # show the sentence that carries the repeal list
    flat = re.sub(r"\s+", "", text)
    for match in re.finditer(r"决定废止", flat):
        print("\n依据句：", flat[match.start():match.start() + 240])
        break
    print("\n正文里的全部文号：",
          list(dict.fromkeys(re.findall(r"海南省人民政府令第\d+号", flat))))


if __name__ == "__main__":
    main()
