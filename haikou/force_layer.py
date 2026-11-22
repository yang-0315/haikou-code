"""Legal-force layer for PageIndex trees: annotate before, review after.

Two integration points, one code path:

  前置 (pre)  — after a tree is built, stamp every node with its document's
                force status, so the tree itself carries the effect information.
  后置 (post) — after retrieval returns nodes, drop or flag what is out of
                force and return the warnings to show the user.

Force is a document-level fact, so each node inherits its document's state; the
node carries it so a hit is self-describing wherever it surfaces.

    python force_layer.py --tree tree.json        # 前置：标注并写回
    python force_layer.py --query "免税店设立条件"  # 后置：检索并审查
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import temporal
import temporal_evidence
import article_force
from warn import (ARTICLE_PARTIAL, ARTICLE_REPEALED, IN_FORCE, QUESTIONABLE,
                  REPEALED, SERIES_REPEALED, load_index, own_numbers,
                  status_for)

from paths import DEFAULT_DATABASE as DEFAULT_DB

OUT_OF_FORCE = (REPEALED, SERIES_REPEALED, ARTICLE_REPEALED)
POLICIES = ("flag", "drop", "strict")

# 一份文件的两个版本（试行／正式）会落到同一个 doc_key。哪一版进结果不能由
# 命中顺序决定，否则可能引到已被正式版取代的试行稿。
VERSION_RANK = {
    IN_FORCE: 0, QUESTIONABLE: 1, ARTICLE_PARTIAL: 2,
    REPEALED: 3, SERIES_REPEALED: 4, ARTICLE_REPEALED: 5,
}


def doc_key(name: str) -> str:
    """Same document under two filenames (试行 suffix, 文号, punctuation) is one doc."""
    name = re.sub(r"[（(][^）)]{0,30}(?:〔[^〕]{0,20}〕|号)[^）)]{0,10}[)）]",
                  "", name)                      # drop a trailing 文号
    return re.sub(r"[（(]\s*试行\s*[)）]|[\s_“”\"'、，,。．.\-—]", "", name)


class ForceLayer:
    """Resolves and applies the legal-force status of policy documents."""

    def __init__(self, database: str = DEFAULT_DB):
        self.database = database
        self.index = load_index(database)
        self._cache: dict[str, dict] = {}
        self._text: dict[str, str] = {}
        self._titles: set[str] | None = None
        self._temporal_index: dict | None = None
        self._temporal_checked = False
        self._article_resolver: article_force.ArticleForceResolver | None = None
        self._article_checked = False
        for path in (
            os.path.join(database, temporal_evidence.INDEX_NAME),
            os.path.join(os.path.dirname(__file__), temporal_evidence.INDEX_NAME),
        ):
            if os.path.exists(path):
                self._temporal_index = json.load(open(path, encoding="utf-8"))
                break

    def temporal_index(self) -> dict | None:
        """Do not use a date index after the corpus fingerprint changes."""
        if not self._temporal_checked:
            self._temporal_checked = True
            if self._temporal_index and not temporal_evidence.index_is_current(
                    self._temporal_index, self.database):
                self._temporal_index = None
        return self._temporal_index

    def catalog_titles(self) -> set[str]:
        """目录里登记的正式文件名；库里有时会把正文存成缺后缀的名字。"""
        if self._titles is None:
            path = os.path.join(self.database, "_all_documents.json")
            titles: set[str] = set()
            if os.path.exists(path):
                try:
                    titles = {item.get("title")
                              for item in json.load(open(path, encoding="utf-8"))}
                except Exception:
                    titles = set()
            self._titles = {title for title in titles if title}
        return self._titles

    def article_index(self) -> article_force.ArticleForceResolver:
        """Article-level deletion events, validated against the corpus."""
        if not self._article_checked:
            self._article_checked = True
            self._article_resolver = article_force.ArticleForceResolver(
                article_force.load_index(self.database))
        return self._article_resolver

    def text_of(self, title: str) -> str:
        """The document's own text, needed by the rules that read the masthead.

        Callers often only have a name (a tool response, a tree node); without
        this lookup the year and amendment history are missing and an old,
        unmaintained regulation is wrongly reported as in force.
        """
        if title in self._text:
            return self._text[title]
        import re as _re
        target = _re.sub(r"[\s_]+", "", title)
        found = ""
        for root, _, files in os.walk(self.database):
            if os.path.basename(root).startswith("_"):
                continue
            for name in files:
                if not name.lower().endswith((".txt", ".md")):
                    continue
                stem = os.path.splitext(name)[0]
                if _re.sub(r"[\s_]+", "", stem) == target:
                    # The force rules only inspect the masthead, but historical
                    # queries also need the implementation clause, which can sit
                    # just below it.
                    found = open(os.path.join(root, name),
                                 encoding="utf-8", errors="ignore").read(1800)
                    break
            if found:
                break
        self._text[title] = found
        return found

    # ── 前置：给树加标注 ──
    def status_for(self, title: str, doc_numbers=None, text=None,
                   as_of=None, file=None) -> dict:
        point = temporal.parse_as_of(as_of) if as_of else None
        key = (title, file or "", temporal.period_key(point) if point else "")
        if key not in self._cache:
            if text is None:
                text = self.text_of(title)
            elif point and len(text) < 1800:
                # A tool adapter may pass only its 400-character masthead to
                # keep the live judgement cheap.  Historical evaluation also
                # needs the implementation clause further down.
                known = self.text_of(title)
                if len(known) > len(text):
                    text = known
            current = status_for(
                {"file": f"{title}.txt", "title": title,
                 "doc_numbers": doc_numbers or [], "text": text or ""},
                self.index)
            if point:
                evidence = temporal_evidence.locate(
                    self.temporal_index(), title, file)
                dates = temporal_evidence.status_dates(evidence, text or "")
                self._cache[key] = temporal.status_at(
                    current, point, text or "", evidence=dates)
            else:
                self._cache[key] = current
        return self._cache[key]

    def status_for_file(self, path: str, as_of=None) -> dict:
        text = open(path, encoding="utf-8", errors="ignore").read()
        title = os.path.splitext(os.path.basename(path))[0]
        return self.status_for(title, own_numbers(text), text[:1800], as_of,
                               file=os.path.basename(path))

    def article_status_for(self, title: str, article: str,
                           document_status: dict | None = None,
                           as_of=None, file=None) -> dict:
        """Apply article-level deletions without weakening document-level force."""
        base = document_status or self.status_for(title, as_of=as_of, file=file)
        return self.article_index().resolve(
            base, title, article, file=file, as_of=as_of)

    def annotate(self, nodes: list[dict], title: str,
                 status: dict | None = None, file=None,
                 as_of=None) -> list[dict]:
        """Return a copy of the tree with a force stamp on every node.

        Article nodes get a second, narrower judgement.  A document can remain
        in force while one article was deleted by a later amendment decision.
        """
        status = status or self.status_for(title)

        def stamp_for(detail: dict) -> dict:
            return {
                "status": detail["status"],
                "confidence": detail["confidence"],
                "reason": detail.get("reason"),
                "checked": detail.get("checked"),
                "missing": detail.get("missing"),
                "next_steps": detail.get("next_steps"),
                "evidence": detail["evidence"],
                "caveats": detail["caveats"],
                "temporal": detail.get("temporal"),
                "article_force": detail.get("article_force", False),
            }

        def walk(items):
            out = []
            for node in items or []:
                clone = copy.deepcopy(node)
                article = clone.get("article")
                if not article:
                    match = re.match(
                        r"^(第[一二三四五六七八九十百零〇两\d]+条)",
                        str(clone.get("title") or "").strip())
                    article = match.group(1) if match else ""
                detail = self.article_status_for(
                    title, article, status, as_of=as_of, file=file) \
                    if article else status
                clone["force"] = stamp_for(detail)
                if clone.get("nodes"):
                    clone["nodes"] = walk(clone["nodes"])
                out.append(clone)
            return out

        return walk(nodes)

    def annotate_file(self, tree_path: str, title: str,
                      out_path: str | None = None, as_of=None) -> dict:
        tree = json.load(open(tree_path, encoding="utf-8"))
        nodes = tree.get("structure", tree) if isinstance(tree, dict) else tree
        stamped = self.annotate(nodes, title,
                                self.status_for(title, as_of=as_of),
                                as_of=as_of)
        if isinstance(tree, dict):
            tree["structure"] = stamped
        else:
            tree = stamped
        if out_path:
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(tree, fh, ensure_ascii=False, indent=2)
        return tree

    # ── 后置：审查检索结果 ──
    def dedupe_copies(self, hits: list[dict]) -> tuple[list[dict], list[dict]]:
        """同一份文档的重复副本只留一条代表，并说明合并了什么。

        合并要拿证据，不能只看名字像：两边的正文必须完全一致（同一份文件重复
        入库）。正文不同说明是真正不同的版本，两条都留着并标成 divergent——
        否则等于替使用者删掉了一个版本，而版本差异恰恰是这个项目要看的东西。
        """
        # key -> 版本记录列表。同名同条但正文不同，是两个不同的文件共用一个
        # 名字（库按栏目分目录，同名文件可能落在不同栏目里），必须分开记。
        groups: dict[str, list[dict]] = {}
        order: list[str] = []
        for hit in hits:
            title = self._title_of(hit)
            key = doc_key(title)
            if key not in groups:
                groups[key] = []
                order.append(key)
            slot = str(hit.get("article") or hit.get("node_id")
                       or hit.get("id") or "")
            text = hit.get("text", "")
            for record in groups[key]:
                if record["title"] != title:
                    continue
                # 条号／节点号分不出来的调用方（PageIndex 节点）只能归到同一条，
                # 否则同一份文件的每个节点都会被当成一个"版本"。
                if slot and slot in record["texts"]:
                    if record["texts"][slot] == text:
                        # 同名同条同文＝同一条命中出现两次，只留一条
                        record["dupes"] += 1
                        break
                    continue
                if not slot and record["hits"]:
                    record["hits"].append(hit)
                    break
                record["texts"][slot] = text
                record["hits"].append(hit)
                break
            else:
                groups[key].append({"title": title, "hits": [hit],
                                    "texts": {slot: text}, "dupes": 0})

        keep, notes = [], []
        titles = self.catalog_titles()
        for key in order:
            versions = groups[key]
            if len(versions) < 2:
                record = versions[0]
                keep += record["hits"]
                if record["dupes"]:
                    notes.append({
                        "key": key, "merged": True, "divergent": False,
                        "representative": record["title"], "kept": [],
                        "reason": f"同名同条同文的命中 {record['dupes']} 条已并成一条",
                    })
                continue
            ranked = []
            for index, record in enumerate(versions):
                status = self._status_of_hit(record["hits"][0])["status"]
                ranked.append({
                    "title": record["title"],
                    "hits": record["hits"],
                    "signature": tuple(sorted(record["texts"].items())),
                    "rank": (VERSION_RANK.get(status, 2),
                             0 if record["title"] in titles else 1, index),
                    "status": status,
                })
            ranked.sort(key=lambda row: row["rank"])
            representative = ranked[0]
            others = ranked[1:]
            # 只有正文逐条一致才敢合并。名字像不算证据：库按栏目分目录，
            # 同名文件可能落在不同栏目里，正文却是两份东西。
            identical = (bool(representative["signature"][0])
                         and len({row["signature"] for row in ranked}) == 1)
            if not identical:
                notes.append({
                    "key": key, "merged": False, "divergent": True,
                    "representative": representative["title"],
                    "kept": [{"document": row["title"], "status": row["status"],
                              "hits": len(row["hits"])} for row in others],
                    "reason": "同名副本的正文不一致，属于不同版本，两条都保留",
                })
                for row in ranked:
                    keep += row["hits"]
                continue
            keep += representative["hits"]
            notes.append({
                "key": key, "merged": True, "divergent": False,
                "representative": representative["title"],
                "kept": [{"document": row["title"], "status": row["status"],
                          "hits": len(row["hits"])} for row in others],
                "reason": "重复入库的同一份文件",
            })
        return keep, notes

    def review(self, hits: list[dict], policy: str = "flag",
               dedupe: bool = False) -> dict:
        """Split retrieval hits by force and collect the warnings to show."""
        if policy not in POLICIES:
            raise ValueError(f"policy must be one of {POLICIES}")
        duplicates: list[dict] = []
        if dedupe:
            hits, duplicates = self.dedupe_copies(hits)
        kept, dropped, warnings = [], [], []
        seen_docs = set()
        for hit in hits:
            status = self._status_of_hit(hit)
            state = status["status"]
            if state in OUT_OF_FORCE and policy in ("drop", "strict"):
                dropped.append({**hit, "force": status})
                continue
            if state in (QUESTIONABLE, ARTICLE_PARTIAL) and policy == "strict":
                dropped.append({**hit, "force": status})
                continue
            kept.append({**hit, "force": status})
            doc = hit.get("doc") or hit.get("title") or hit.get("document")
            if state != IN_FORCE and doc not in seen_docs:
                seen_docs.add(doc)
                warnings.append({"document": doc, "status": state,
                                 "reason": status.get("reason"),
                                 "missing": status.get("missing"),
                                 "next_steps": status.get("next_steps"),
                                 "evidence": status["evidence"],
                                 "caveats": status["caveats"]})
        return {"kept": kept, "dropped": dropped, "warnings": warnings,
                "duplicates": duplicates,
                "summary": summarize(kept, dropped, warnings)}

    @staticmethod
    def _title_of(hit: dict) -> str:
        return hit.get("doc") or hit.get("document") or hit.get("title") or ""

    def _status_of_hit(self, hit: dict) -> dict:
        # the hit may already carry a judgement made with the full text in hand
        if isinstance(hit.get("force"), dict):
            base = hit["force"]
        elif isinstance(hit.get("status_detail"), dict) and hit["status_detail"]:
            base = hit["status_detail"]
        else:
            base = self.status_for(self._title_of(hit), hit.get("doc_numbers"),
                                   hit.get("text"))
        if base.get("article_force"):
            return base
        return self.article_status_for(
            self._title_of(hit), hit.get("article", ""), base,
            file=hit.get("file"))


def summarize(kept: list[dict], dropped: list[dict],
              warnings: list[dict]) -> str:
    if not kept and dropped:
        return (f"全部 {len(dropped)} 条命中均来自已失效文件，"
                "不能作为现行依据。")
    if warnings:
        names = "、".join(w["document"][:20] for w in warnings[:3])
        tail = "等" if len(warnings) > 3 else ""
        return (f"命中 {len(kept)} 条，其中涉及效力存疑、条文变更"
                f"或已失效来源：{names}{tail}。")
    return f"命中 {len(kept)} 条现行有效条文。"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=DEFAULT_DB)
    parser.add_argument("--tree", help="前置：给这份树加效力标注")
    parser.add_argument("--title", help="树所属文档名")
    parser.add_argument("--out", help="标注后写到哪里")
    parser.add_argument("--query", help="后置：检索并审查")
    parser.add_argument("--policy", default="flag", choices=POLICIES)
    parser.add_argument("--dedupe", action="store_true",
                        help="同一份文件的重复副本只留一条代表")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--as-of", help="标注或审查时使用的历史时点")
    args = parser.parse_args()

    layer = ForceLayer(args.database)
    point = temporal.parse_as_of(args.as_of) if args.as_of else None

    if args.tree:
        annotated = layer.annotate_file(args.tree, args.title or "", args.out,
                                        point)
        nodes = annotated.get("structure", annotated) \
            if isinstance(annotated, dict) else annotated
        status = layer.status_for(args.title or "", as_of=point)
        print(f"文档《{args.title}》效力：{status['status']}"
              f"（置信度 {status['confidence']}）")
        if point:
            print(temporal.context(point))
        count = 0

        def walk(items):
            nonlocal count
            for node in items or []:
                count += 1
                walk(node.get("nodes"))

        walk(nodes)
        print(f"已标注 {count} 个节点" + (f" → {args.out}" if args.out else ""))
        return

    if args.query:
        import retrieval
        from docstore import library_files
        articles = retrieval.load_articles(args.database)
        articles += retrieval.load_articles_from_paths(
            library_files(args.database), args.database)
        hits = retrieval.Index(articles).search(args.query, args.top)
        result = layer.review(hits, args.policy, dedupe=args.dedupe)
        print(result["summary"] + "\n")
        for note in result["duplicates"]:
            merged = "已合并" if note["merged"] else "未合并"
            print(f"  ▣ {merged}：「{note['representative'][:34]}」")
            for other in note["kept"]:
                print(f"      {'并入' if note['merged'] else '保留'}"
                      f"「{other['document'][:34]}」（{other['status']}，"
                      f"{other['hits']} 条）")
            print(f"      {note['reason']}")
        if result["duplicates"]:
            print()
        for warning in result["warnings"]:
            print(f"  ⚠ {warning['document'][:40]}（{warning['status']}）")
            if warning.get("reason"):
                print(f"      原因：{warning['reason']['text']}")
            if warning.get("missing"):
                print("      缺什么：" + "；".join(warning["missing"]))
            if warning.get("next_steps"):
                print("      下一步：" + "；".join(warning["next_steps"]))
        print()
        for rank, hit in enumerate(result["kept"], 1):
            print(f"[{rank}] {hit['doc'][:36]} {hit['article']}"
                  f"  [{hit['force']['status']}]")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
