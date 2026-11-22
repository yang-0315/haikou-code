"""Find the original text of documents that later notices repealed."""
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, r"C:\Users\Lenovo\Desktop\PageIndex\haikou")

from repeal import CTX, UA, fetch, meta_fields

from paths import DEFAULT_DATABASE as DB

TARGETS = [
    ("海南自由贸易港建设投资基金管理办法",
     ["琼府办〔2024〕27号", "海南自由贸易港建设投资基金管理办法"]),
    ("海南省太阳能热水系统建筑应用管理办法",
     ["海南省人民政府令第227号", "海南省太阳能热水系统建筑应用管理办法"]),
]

LISTING_PAGES = [
    "https://www.hainan.gov.cn/hainan/zfwj/zcwj_list.shtml",
    "https://www.hainan.gov.cn/hainan/zfwj/zcwj_list_2.shtml",
    "https://www.hainan.gov.cn/hainan/flfgxzgfxwj/fgsjk_ywh.shtml",
    "https://www.hainan.gov.cn/hainan/xxyxzfgz/zfgzk.shtml",
]


def search_bing(query: str) -> list[str]:
    url = ("https://cn.bing.com/search?q="
           + urllib.parse.quote(query) + "&count=20")
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=30, context=CTX) as response:
        html = response.read().decode("utf-8", "ignore")
    out = []
    for link in re.findall(r'href="(https?://[^"]+)"', html):
        if "hainan.gov.cn" not in link:
            continue
        if link not in out:
            out.append(link)
    return out


def main():
    found = {}
    for title, keys in TARGETS:
        print(f"\n===== 找原文：{title}")
        candidates = []
        try:
            candidates += search_bing(f"site:hainan.gov.cn {keys[1]}")
        except Exception as exc:
            print("  搜索失败:", type(exc).__name__)
        for page in LISTING_PAGES:
            try:
                html = fetch(page)
            except Exception:
                continue
            for href, text in re.findall(
                    r'<a[^>]+href="([^"]+)"[^>]*>([^<]{6,90})</a>', html):
                clean = re.sub(r"\s+", "", text)
                if any(key.replace("〔", "").replace("〕", "") in clean
                       for key in keys) or keys[1] in clean:
                    candidates.append(urllib.parse.urljoin(page, href))
        candidates = list(dict.fromkeys(c for c in candidates if c.endswith(".shtml")))
        print(f"  候选 {len(candidates)} 个")
        for url in candidates[:6]:
            try:
                html = fetch(url)
            except Exception:
                continue
            meta = meta_fields(html)
            article = meta.get("ArticleTitle") or ""
            body = meta.get("Description") or ""
            if not any(key in article or key.replace("〔", "").replace("〕", "") in body
                       for key in keys):
                continue
            if "废止" in article:
                continue
            print(f"  命中: {article[:56]}")
            print(f"        {url}")
            found[title] = {"url": url, "article": article,
                            "meta": {k: meta.get(k) for k in
                                     ("PubDate", "ContentSource", "Description")}}
            break

    if found:
        path = os.path.join(DB, "_repealed_originals.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(found, fh, ensure_ascii=False, indent=2)
        print(f"\n找到 {len(found)} 份原文 → {path}")
    else:
        print("\n没找到原文页面")


if __name__ == "__main__":
    main()
