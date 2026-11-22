"""Fetch three hainan.gov.cn policy documents and run the条文-level checks."""
import gzip
import json
import os
import re
import ssl
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CTX = ssl.create_default_context()
from paths import DEFAULT_DATABASE as OUT

DOCS = [
    ("海南自由贸易港岛内居民消费零关税进境商品经营主体及免税店管理办法",
     "政府规章",
     "https://www.hainan.gov.cn/hainan/xxyxzfgz/202602/"
     "e7cbe6e008f54c229b0754aeb64aa579.shtml"),
    ("海南自由贸易港商事调解组织登记管理办法",
     "政府规章",
     "https://www.hainan.gov.cn/hainan/xxyxzfgz/202502/"
     "4e3e2b650d4a46e686c45f054ee8cf2c.shtml"),
    ("海南自由贸易港禁毒条例",
     "地方性法规",
     "https://www.hainan.gov.cn/hainan/dfxfg/202602/"
     "8b5fb14262bf458f89d9fec11fe948b6.shtml"),
]

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10}


def cn_to_int(text: str) -> int:
    """第十五条 / 第二十三条 / 三十一 等 → int"""
    text = text.strip()
    if "十" not in text:
        return sum(CN_NUM.get(ch, 0) for ch in text)
    head, _, tail = text.partition("十")
    tens = CN_NUM.get(head, 1) if head else 1
    ones = sum(CN_NUM.get(ch, 0) for ch in tail)
    return tens * 10 + ones


def fetch(url):
    request = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,*/*",
        "Accept-Encoding": "gzip", "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(request, timeout=30, context=CTX) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "ignore")


def body_text(html: str) -> str:
    """正文 = the paragraph blocks between the title and the page furniture."""
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    blocks = re.findall(r"<p[^>]*>(.*?)</p>", html, re.S)
    text = "\n".join(re.sub(r"<[^>]+>", "", b) for b in blocks)
    text = text.replace("&ensp;", " ").replace("&nbsp;", " ")
    text = re.sub(r"&[a-z]+;", " ", text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    joined = "\n".join(lines)
    start = joined.find("第一条")
    if start > 0:
        head = joined[:start]
        tail_marker = re.search(r"(扫一扫在手机打开当前页|相关稿件|【打印】|【关闭】)", joined)
        end = tail_marker.start() if tail_marker else len(joined)
        return joined[max(0, start - 400):end]
    return joined


def metadata(html: str, text: str) -> dict:
    meta = {}
    meta["title"] = (re.search(r"<title>(.*?)</title>", html, re.S)
                     or [None, ""])[1].strip().split("_")[0].split("-")[0]
    patterns = {
        "令号": r"(海南省人民政府令第\s*\d+\s*号)",
        "会议": r"(第[一二三四五六七八九十百\d]+次常务会议审议通过)",
        "公布日期": r"(\d{4}年\d{1,2}月\d{1,2}日)海南省人民政府令",
        "通过日期": r"(\d{4}年\d{1,2}月\d{1,2}日)[^。]{0,12}(?:通过|修订通过)",
        "施行日期": r"自(\d{4}年\d{1,2}月\d{1,2}日)起施行",
        "发布机关": r"(海南省人民代表大会常务委员会|海南省人民政府)",
    }
    for key, pattern in patterns.items():
        hit = re.search(pattern, text)
        meta[key] = hit.group(1) if hit else None
    return meta


def article_check(text: str) -> dict:
    # Only a line-initial 第X条 starts an article; the same phrase inside a
    # sentence is a cross-reference ("依照本办法第十六条的规定...").
    marks = []
    for match in re.finditer(r"(?m)^\s*第([一二三四五六七八九十]+)条", text):
        marks.append((match.start(), cn_to_int(match.group(1))))
    inline = len(re.findall(r"第[一二三四五六七八九十]+条", text)) - len(marks)
    numbers = [n for _, n in marks]
    unique = sorted(set(numbers))
    missing = [n for n in range(1, (max(unique) + 1) if unique else 1)
               if n not in unique]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    chapters = re.findall(r"第([一二三四五六七八九十]+)章\s*([^\n]{0,20})", text)
    segments = []
    for index, (pos, number) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(text)
        segments.append((number, text[pos:end].strip()))
    lengths = [len(body) for _, body in segments]
    return {
        "articles": len(unique),
        "max_article": max(unique) if unique else 0,
        "inline_references": inline,
        "missing": missing[:10],
        "duplicates": duplicates[:10],
        "chapters": [f"第{c}章 {t.strip()}" for c, t in chapters],
        "avg_chars": round(sum(lengths) / len(lengths), 1) if lengths else 0,
        "min_chars": min(lengths) if lengths else 0,
        "max_chars": max(lengths) if lengths else 0,
        "segments": segments,
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    report = []
    for name, kind, url in DOCS:
        html = fetch(url)
        text = body_text(html)
        meta = metadata(html, text)
        check = article_check(text)
        path = os.path.join(OUT, f"{name}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        report.append({"name": name, "kind": kind, "url": url,
                       "chars": len(text), "meta": meta,
                       "check": {k: v for k, v in check.items()
                                 if k != "segments"},
                       "first_segments": check["segments"][:2]})
        print(f"\n===== {name}（{kind}）")
        print(f"  正文 {len(text)} 字 → {path}")
        print(f"  元数据: " + "  ".join(f"{k}={v}" for k, v in meta.items() if v))
        print(f"  条文: {check['articles']} 条（最大第 {check['max_article']} 条）"
              f"  章: {len(check['chapters'])}")
        print(f"  缺号: {check['missing'] or '无'}   重号: {check['duplicates'] or '无'}")
        print(f"  条文长度 平均 {check['avg_chars']} 字 "
              f"最短 {check['min_chars']} 最长 {check['max_chars']}")
        for number, body in check["segments"][:2]:
            print(f"    第{number}条 → {body[:56]}...")

    with open(os.path.join(OUT, "_check.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\n报告: {os.path.join(OUT, '_check.json')}")


if __name__ == "__main__":
    main()
