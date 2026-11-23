"""Offline audit for the policy-update evidence, review, and publish workflow."""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import docstore
import policy_update_scan
import retrieval
import update_workflow


class Audit:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed += 1
            print(f"  FAIL  {name}{': ' + detail if detail else ''}")


def expect_error(callable_, text: str) -> bool:
    try:
        callable_()
    except (ValueError, RuntimeError) as exc:
        return text in str(exc)
    return False


def page_html(title: str, body: str) -> str:
    return (
        "<html><head>"
        f'<meta name="ArticleTitle" content="{title}">'
        '<meta name="PubDate" content="2026-11-23 10:00:00">'
        '<meta name="ContentSource" content="海南省人民政府">'
        "</head><body>"
        f"<p>{body}</p>"
        '<a href="/files/evidence.ofd">官方附件</a>'
        "</body></html>"
    )


BODY = (
    "第一条 为验证政策更新工作流，制定本测试文件。第二条 官方页面正文"
    "必须经过下载、哈希、正文抽取和版本差异后才进入审核队列。第三条 "
    "任何未经核对人确认的候选不得发布，也不得覆盖现行政策正文。第四条 "
    "发布前系统应备份目录、正文和派生索引，发布失败时必须完整回滚。第五条 "
    "发布完成后检索、事项、时效和带效力树索引应使用同一批新数据。"
)


def main() -> None:
    audit = Audit()
    with tempfile.TemporaryDirectory(prefix="update-workflow-audit-") as root:
        channel = "审计测试栏目"
        os.makedirs(os.path.join(root, "library", channel))
        old_url = "https://www.hainan.gov.cn/audit/old.shtml"
        old_item = {
            "channel": channel,
            "page": 1,
            "title": "审计旧办法",
            "url": old_url,
        }
        update_workflow._atomic_json(
            os.path.join(root, "_all_documents.json"), [old_item])
        update_workflow._atomic_text(
            docstore.cache_path(root, old_item),
            "审计旧办法\n第一条 旧正文。\n第二条 不得无审核覆盖。",
        )
        new_url = "https://www.hainan.gov.cn/audit/new.shtml"
        rollback_url = "https://www.hainan.gov.cn/audit/rollback.shtml"
        candidates = [
            {
                "title": "审计新办法",
                "url": new_url,
                "official_url": new_url,
                "channel": channel,
                "page": 1,
                "kind": "new_document",
                "review_status": "pending",
                "workflow_status": "pending",
                "detected_at": update_workflow._now(),
                "id": update_workflow.candidate_id(new_url, "审计新办法"),
            },
            {
                "title": "审计回滚办法",
                "url": rollback_url,
                "official_url": rollback_url,
                "channel": channel,
                "page": 1,
                "kind": "new_document",
                "review_status": "pending",
                "workflow_status": "pending",
                "detected_at": update_workflow._now(),
                "id": update_workflow.candidate_id(
                    rollback_url, "审计回滚办法"),
            },
        ]
        scan = {
            "scan_id": "update-audit",
            "started_at": update_workflow._now(),
            "candidates": candidates,
        }
        update_workflow._save_ledger(root, {
            "version": 1,
            "scans": [scan],
            "latest": scan,
        })

        print("== 候选身份与来源 ==")
        audit.check(
            "候选 ID 对同一 URL 和标题稳定",
            update_workflow.candidate_id(new_url, "审计新办法")
            == candidates[0]["id"],
        )
        audit.check(
            "候选 ID 区分不同官方 URL",
            candidates[0]["id"] != candidates[1]["id"],
        )
        audit.check(
            "只接受 hainan.gov.cn 官方来源",
            update_workflow._official_url(new_url)
            and not update_workflow._official_url(
                "https://example.test/not-official.shtml"),
        )
        fresh = [dict(candidates[0])]
        fresh[0]["workflow_status"] = "pending"
        preserved = policy_update_scan.preserve_review_state(root, fresh)
        audit.check(
            "重复扫描可保留人工审核状态",
            preserved[0]["id"] == candidates[0]["id"],
        )

        print("== 官方取证 ==")

        def page_loader(url):
            return page_html(
                "审计新办法" if url == new_url else "审计回滚办法",
                BODY,
            )

        staged_result = update_workflow.stage_candidates(
            root,
            candidate_ids=[candidates[0]["id"]],
            page_loader=page_loader,
            binary_loader=lambda url: b"official attachment fixture",
            delay=0,
        )
        staged = staged_result["candidates"][0]
        audit.check("官方页面可取证", staged["workflow_status"] == "staged")
        audit.check(
            "正文与来源均保存 SHA-256",
            len(staged.get("page_sha256", "")) == 64
            and len(staged.get("body_sha256", "")) == 64,
        )
        audit.check(
            "附件保存字节哈希并登记",
            staged.get("attachment_count") == 1
            and len(staged["attachment_files"][0].get("sha256", "")) == 64,
        )
        catalog = update_workflow._load_json(
            os.path.join(root, "_all_documents.json"), [])
        audit.check("取证阶段不修改政策目录", len(catalog) == 1)
        review_path = os.path.join(root, update_workflow.REVIEW_NAME)
        audit.check(
            "生成带官方证据字段的审核队列",
            os.path.exists(review_path)
            and "page_sha256" in open(
                review_path, encoding="utf-8-sig").read(),
        )

        print("== 人工审核闸门 ==")
        audit.check(
            "无证据不能审核通过",
            expect_error(
                lambda: update_workflow.record_review(
                    root,
                    candidates[0]["id"],
                    "verified",
                    "审计员",
                    "",
                    "publish_new",
                ),
                "证据",
            ),
        )
        audit.check(
            "未发布候选不能以无效动作通过",
            expect_error(
                lambda: update_workflow.record_review(
                    root,
                    candidates[0]["id"],
                    "verified",
                    "审计员",
                    "官网载明本文件自发布之日起施行。",
                    "unknown_action",
                ),
                "动作",
            ),
        )
        verified = update_workflow.record_review(
            root,
            candidates[0]["id"],
            "verified",
            "审计员",
            "官网载明本文件自发布之日起施行。",
            "publish_new",
            "离线审计记录",
        )
        audit.check(
            "审核记录核对人、证据和动作",
            verified["workflow_status"] == "verified"
            and verified["verified_by"] == "审计员"
            and verified["action"] == "publish_new",
        )
        summary = update_workflow.public_update_summary(root)
        audit.check(
            "公开摘要不泄漏本地文件路径",
            "body_file" not in json.dumps(summary, ensure_ascii=False)
            and summary["verified"] == 1,
        )

        print("== 发布锁与发布 ==")
        with update_workflow.publish_lock(root):
            audit.check(
                "发布期间锁文件可见",
                update_workflow.is_publish_in_progress(root),
            )
            audit.check(
                "禁止并发发布",
                expect_error(
                    lambda: update_workflow.publish_lock(root).__enter__(),
                    "正在运行",
                ),
            )
        audit.check(
            "发布退出后释放锁",
            not update_workflow.is_publish_in_progress(root),
        )
        publication = update_workflow.publish_candidate(
            root, candidates[0]["id"], rebuild=True)
        catalog = update_workflow._load_json(
            os.path.join(root, "_all_documents.json"), [])
        new_item = next(item for item in catalog if item["url"] == new_url)
        audit.check(
            "审核通过后新增目录与正文",
            len(catalog) == 2
            and os.path.exists(docstore.cache_path(root, new_item)),
        )
        audit.check(
            "发布记录保存正文和页面哈希",
            publication["body_sha256"] == staged["body_sha256"]
            and publication["page_sha256"] == staged["page_sha256"],
        )
        audit.check(
            "发布同时重建检索、时效、树和效力索引",
            publication.get("indexes", {}).get("retrieval_units", 0) > 0
            and publication.get("indexes", {}).get("temporal_documents", 0) > 0
            and publication.get("indexes", {}).get("tree_documents", 0) > 0
            and publication.get("indexes", {}).get("force_documents", 0) > 0,
            json.dumps(publication.get("indexes", {}), ensure_ascii=False),
        )
        retrieval_before = retrieval.corpus_fingerprint(root)
        update_workflow._atomic_json(
            os.path.join(root, "_force_status.json"), {"probe": True})
        audit.check(
            "效力索引变化会使持久化检索缓存失效",
            retrieval.corpus_fingerprint(root) != retrieval_before,
        )
        publication_log = update_workflow._load_json(
            os.path.join(root, update_workflow.PUBLISH_LOG),
            {"publications": []},
        )
        audit.check(
            "发布审计日志可追溯",
            len(publication_log.get("publications", [])) == 1
            and publication_log["publications"][0]["verified_by"] == "审计员",
        )
        audit.check(
            "已发布候选不能重复发布",
            expect_error(
                lambda: update_workflow.publish_candidate(
                    root, candidates[0]["id"], rebuild=False),
                "尚未通过人工审核",
            ),
        )

        print("== 失败回滚 ==")
        update_workflow.stage_candidates(
            root,
            candidate_ids=[candidates[1]["id"]],
            page_loader=page_loader,
            binary_loader=lambda url: b"rollback attachment fixture",
            delay=0,
        )
        duplicate_ledger = update_workflow._load_ledger(root)
        duplicate_candidate = update_workflow._candidate_in_scan(
            update_workflow._latest_scan(duplicate_ledger),
            candidates[1]["id"],
        )
        duplicate_candidate["duplicate_of"] = candidates[0]["id"]
        update_workflow._save_ledger(root, duplicate_ledger)
        audit.check(
            "重复正文不能作为新文件审核通过",
            expect_error(
                lambda: update_workflow.record_review(
                    root,
                    candidates[1]["id"],
                    "verified",
                    "审计员",
                    "官网载明回滚测试文件自发布之日起施行。",
                    "publish_new",
                ),
                "重复正文",
            ),
        )
        duplicate_candidate.pop("duplicate_of", None)
        duplicate_candidate.pop("duplicate_reason", None)
        update_workflow._save_ledger(root, duplicate_ledger)
        with open(review_path, encoding="utf-8-sig", newline="") as handle:
            queue_rows = list(csv.DictReader(handle))
        target_row = next(
            row for row in queue_rows
            if row["candidate_id"] == candidates[1]["id"])
        target_row.update({
            "review_status": "verified",
            "reviewed_by": "审计员",
            "evidence": "官网载明回滚测试文件自发布之日起施行。",
            "action": "publish_new",
            "notes": "由 CSV 队列导入",
        })
        with open(review_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=update_workflow.REVIEW_FIELDS)
            writer.writeheader()
            writer.writerows(queue_rows)
        imported = update_workflow.import_review_csv(root)
        audit.check(
            "CSV 审核队列走同一审核闸门",
            imported["applied"] == [candidates[1]["id"]],
        )
        catalog_before = open(
            os.path.join(root, "_all_documents.json"),
            encoding="utf-8",
        ).read()
        def fail_rebuild(database):
            raise RuntimeError("injected rebuild failure")

        audit.check(
            "索引重建失败触发发布回滚",
            expect_error(
                lambda: update_workflow.publish_candidate(
                    root,
                    candidates[1]["id"],
                    rebuild=True,
                    index_rebuilder=fail_rebuild,
                ),
                "已回滚",
            ),
        )
        catalog_after = open(
            os.path.join(root, "_all_documents.json"),
            encoding="utf-8",
        ).read()
        ledger_after = update_workflow._load_json(
            os.path.join(root, "_update_ledger.json"), {})
        rollback_candidate = ledger_after["scans"][0]["candidates"][1]
        audit.check(
            "回滚恢复目录且不残留锁",
            catalog_after == catalog_before
            and not update_workflow.is_publish_in_progress(root),
        )
        audit.check(
            "回滚保留审核状态且不误记发布",
            rollback_candidate["workflow_status"] == "verified"
            and not rollback_candidate.get("publication_id"),
        )

    total = audit.passed + audit.failed
    print(f"\nupdate workflow audit: {audit.passed}/{total}")
    raise SystemExit(0 if audit.failed == 0 else 1)


if __name__ == "__main__":
    main()
