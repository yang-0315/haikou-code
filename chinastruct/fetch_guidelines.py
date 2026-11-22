"""Download Chinese clinical guidelines, verify them, and gate them on intake.

Pipeline per target: search (Bing filetype:pdf) -> download -> sanity check
(real PDF, enough text, keywords present) -> intake gate -> place.
Passing files go to <out>/<theme>/<name>.pdf, rejects to <out>/_excluded/ with
the reason recorded. Every file's source URL and domain are written to
_provenance.json, and non-official domains are flagged for manual review.

    python fetch_guidelines.py [--out DATASET_DIR] [--only SUBSTRING]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import ssl
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pypdfium2 as pdfium          # noqa: E402

from gate import evaluate_pdf       # noqa: E402

DEFAULT_OUT = r"C:\Users\Lenovo\Desktop\dataset"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MIN_BYTES = 200_000
JUNK_HOSTS = re.compile(r"baike|zdic|hanyu|gushici|shici|hgcha|bing\.com|"
                        r"microsoft|msn\.|miit\.gov|beian|dili360|cctv|"
                        r"people\.com\.cn|china\.com\.cn|bajiu")
# domains that publish guidelines themselves
OFFICIAL = re.compile(r"(\.gov\.cn$|\.org\.cn$|nhc\.gov\.cn|cma\.org\.cn|"
                      r"chinacirculation\.org|chinagp\.net|yiigle\.com|"
                      r"cjcp\.org|medlive\.cn)")

# (theme, file name, search queries, keywords that must appear in the text)
TARGETS: list[tuple[str, str, list[str], list[str]]] = [
    # filled in per run; see fetch_batch_*.py for concrete batches
]

CTX = ssl.create_default_context()
CTX_LAX = ssl.create_default_context()
CTX_LAX.check_hostname = False
CTX_LAX.verify_mode = ssl.CERT_NONE


def http_get(url, timeout=90, binary=True, referer="https://cn.bing.com/"):
    headers = {"User-Agent": UA, "Accept": "*/*",
               "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=timeout, context=CTX)
    except ssl.SSLError:
        response = urllib.request.urlopen(request, timeout=timeout, context=CTX_LAX)
    with response as handle:
        data = handle.read()
        return (data, handle.geturl()) if binary else (
            data.decode("utf-8", "ignore"), handle.geturl())


def safe_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc,
                                    urllib.parse.quote(parts.path, safe="/%"),
                                    urllib.parse.quote(parts.query, safe="=&%"), ""))


def search_pdf_links(query: str, limit: int = 8) -> list[str]:
    html, _ = http_get("https://cn.bing.com/search?q="
                       + urllib.parse.quote(query) + "&count=30",
                       timeout=30, binary=False, referer=None)
    html = html.replace("&amp;", "&")
    seen, out = set(), []
    for url in re.findall(r'href="(https?://[^"]+)"', html):
        if not url.split("?")[0].lower().endswith(".pdf") or JUNK_HOSTS.search(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def head_text(data: bytes, pages: int = 3) -> str:
    doc = pdfium.PdfDocument(io.BytesIO(data))
    text = "\n".join(doc[i].get_textpage().get_text_range()
                     for i in range(min(pages, len(doc))))
    doc.close()
    return text


def record_provenance(folder: str, name: str, url: str, verdict: str,
                      reasons: list[str], theme: str) -> None:
    path = os.path.join(folder, "_provenance.json")
    rows = []
    if os.path.exists(path):
        try:
            rows = json.load(open(path, encoding="utf-8"))
        except ValueError:
            rows = []
    domain = urllib.parse.urlsplit(url).netloc.lower()
    rows.append({"file": name, "theme": theme, "url": url, "domain": domain,
                 "official_domain": bool(OFFICIAL.search(domain)),
                 "verdict": verdict, "reasons": reasons,
                 "sha1": None})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)


def fetch_target(out: str, theme: str, name: str, queries: list[str],
                 keywords: list[str]) -> dict:
    theme_dir = os.path.join(out, theme)
    os.makedirs(theme_dir, exist_ok=True)
    target = os.path.join(theme_dir, f"{name}.pdf")
    excluded = os.path.join(out, "_excluded")
    parked = os.path.join(excluded, f"{name}.pdf")
    if os.path.exists(target) or os.path.exists(parked):
        return {"name": name, "status": "already"}

    notes: list[str] = []
    fallback = None
    for query in queries:
        try:
            links = search_pdf_links(query)
        except Exception as exc:
            notes.append(f"search {type(exc).__name__}")
            links = []
        for url in links:
            try:
                data, final = http_get(safe_url(url))
            except Exception as exc:
                notes.append(f"{url[:48]} -> {type(exc).__name__}")
                continue
            if not data.startswith(b"%PDF-") or len(data) < MIN_BYTES:
                continue
            try:
                head = head_text(data)
            except Exception:
                head = ""
            if not any(keyword in head for keyword in keywords):
                notes.append(f"{url[:48]} -> keyword miss")
                continue

            tmp_path = os.path.join(theme_dir, "_tmp.pdf")
            with open(tmp_path, "wb") as fh:
                fh.write(data)
            verdict = evaluate_pdf(tmp_path)
            if verdict["verdict"] == "pass":
                os.replace(tmp_path, target)
                record_provenance(out, name, final, "pass", [], theme)
                return {"name": name, "status": "ok", "bytes": len(data),
                        "url": final, "pages": verdict["metrics"].get("pages")}
            fallback = fallback or (tmp_path, final, verdict)
            notes.append(f"{url[:48]} -> {verdict['reasons'][0][:40]}")
            os.remove(tmp_path)
        time.sleep(1.2)

    if fallback:
        tmp_path, final, verdict = fallback
        os.makedirs(excluded, exist_ok=True)
        os.replace(tmp_path, parked)
        record_provenance(out, name, final, "reject", verdict["reasons"], theme)
        return {"name": name, "status": "excluded", "url": final,
                "reasons": verdict["reasons"]}
    return {"name": name, "status": "failed", "notes": notes[:5]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--only", default="")
    parser.add_argument("--targets", default="",
                        help="JSON file with [[theme, name, [queries], [kw]], ...]")
    args = parser.parse_args()

    targets = TARGETS
    if args.targets:
        targets = [tuple(row) for row in json.load(open(args.targets, encoding="utf-8"))]
    if args.only:
        targets = [t for t in targets if args.only in t[0] or args.only in t[1]]
    if not targets:
        print("没有目标。用 --targets 传入 JSON 清单。")
        return

    results = []
    for theme, name, queries, keywords in targets:
        result = fetch_target(args.out, theme, name, queries, keywords)
        results.append(result)
        print(f"[{result['status']:>8}] {name}", flush=True)
        for note in result.get("notes", [])[:3]:
            print(f"           {note}", flush=True)
        for reason in result.get("reasons", []):
            print(f"           排除: {reason}", flush=True)
    ok = sum(1 for r in results if r["status"] in ("ok", "already"))
    print(f"\n完成：可用 {ok}，排除 "
          f"{sum(1 for r in results if r['status'] == 'excluded')}，"
          f"失败 {sum(1 for r in results if r['status'] == 'failed')}")


if __name__ == "__main__":
    main()
