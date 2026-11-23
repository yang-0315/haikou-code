"""Re-score the annotated spot-check against the current rules."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from warn import load_index, own_numbers, status_for

from paths import DEFAULT_DATABASE as DB
SPOT = os.path.join(DB, "_spotcheck.md")


def annotations() -> list[dict]:
    text = open(SPOT, encoding="utf-8").read()
    out = []
    for block in re.split(r"\n### ", text)[1:]:
        title = re.sub(r"^\d+\.\s*", "", block.split("\n", 1)[0].strip())
        tail = block.split("请核对**：", 1)[-1].split("\n")[0]
        note = re.search(r"）\s*(.+)$", tail)
        old = re.search(r"系统判定：\*\*(.+?)\*\*", block)
        out.append({
            "title": title,
            "old": old.group(1) if old else "?",
            "human_ok": (note.group(1).strip().startswith("有效")
                         if note else None),
            "note": note.group(1).strip() if note else "",
        })
    return out


def locate(title: str) -> str | None:
    root = os.path.join(DB, "library")
    for folder, _, files in os.walk(root):
        for name in files:
            if os.path.splitext(name)[0] == title:
                return os.path.join(folder, name)
    return None


def main():
    index = load_index(DB)
    rows = []
    for item in annotations():
        path = locate(item["title"])
        if not path:
            continue
        text = open(path, encoding="utf-8", errors="ignore").read()
        status = status_for({"file": os.path.basename(path),
                             "title": item["title"],
                             "doc_numbers": own_numbers(text),
                             "text": text[:400]}, index)
        item["new"] = status["status"]
        rows.append(item)

    print(f"抽检 {len(rows)} 份，逐份对比（人工标注 vs 系统判定）\n")
    print(f"{'文件':<34}{'人工':<6}{'改前':<8}{'改后':<8}")
    print("-" * 62)
    for item in rows:
        human = "有效" if item["human_ok"] else "无效"
        flag = ""
        if item["human_ok"] is False and item["new"] == "现行有效":
            flag = "  ← 仍漏报"
        if item["human_ok"] is True and item["new"] != "现行有效":
            flag = "  ← 过度标记"
        print(f"{item['title'][:32]:<34}{human:<6}"
              f"{item['old']:<8}{item['new']:<8}{flag}")

    # scoring: the system's "in force" call is what must not be wrong
    said_in_force = [r for r in rows if r["new"] == "现行有效"]
    truly_valid = [r for r in said_in_force if r["human_ok"]]
    misses = [r for r in rows if r["human_ok"] is False and r["new"] == "现行有效"]
    over = [r for r in rows if r["human_ok"] is True and r["new"] != "现行有效"]
    invalid = [r for r in rows if r["human_ok"] is False]

    classification_ok = sum(
        (item["new"] == "现行有效") == item["human_ok"] for item in rows
    )
    print(f"\n== 指标（二分类：现行有效／非现行） ==")
    print(f"  人工与系统二分类一致 {classification_ok}/{len(rows)}"
          f" → 准确率 {classification_ok/max(len(rows),1):.0%}")
    print(f"  系统说「现行有效」{len(said_in_force)} 份，其中人工确认为有效 "
          f"{len(truly_valid)} 份 → 精确率 "
          f"{len(truly_valid)/max(len(said_in_force),1):.0%}")
    print(f"  人工判无效 {len(invalid)} 份，被系统标为非现行 "
          f"{len(invalid)-len(misses)} 份 → 非现行召回率 "
          f"{(len(invalid)-len(misses))/max(len(invalid),1):.0%}")
    print(f"  过度标记（人工说有效、系统未说有效）{len(over)} 份")
    print(f"  残留漏报 {len(misses)} 份")
    print("  说明：效力存疑只算“非现行/需核实”，不等同于已确认废止；"
          "本文件是当时 128 份语料的历史抽检，不代表当前 1,244 份全量精度。")


if __name__ == "__main__":
    main()
