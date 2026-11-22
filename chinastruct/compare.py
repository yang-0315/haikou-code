"""Print the before/after comparison table."""
import json

OUT = r"C:\Users\Lenovo\Desktop\PageIndex\baseline\reports"


def main():
    new = {r["file"]: r for r in json.load(
        open(OUT + r"\chinastruct_eval.json", encoding="utf-8"))}
    old = {r["file"]: r for r in json.load(
        open(OUT + r"\baseline_full.json", encoding="utf-8"))}

    header = (f"{'文档':<28}{'页':>4} {'原节点':>7}{'新节点':>7} "
              f"{'原残句':>7}{'新残句':>7} {'原深':>4}{'新深':>4}  {'来源':<9}")
    print(header)
    print("-" * len(header))
    for fname, n in new.items():
        o = old.get(fname, {})
        note = ""
        if (o.get("nodes") or 0) > 100 and n.get("nodes", 0) < o["nodes"] * 0.6:
            note = "  <-- 节点大幅减少"
        print(f"{fname.replace('.pdf', '')[:26]:<28}{n.get('pages', 0):>4} "
              f"{o.get('nodes', '-'):>7}{n.get('nodes', '-'):>7} "
              f"{o.get('fragments', '-'):>7}{n.get('fragments', '-'):>7} "
              f"{o.get('max_depth', '-'):>4}{n.get('depth', '-'):>4}  "
              f"{o.get('toc_source', '-'):<9}{note}")

    print(f"\n合计  原节点 {sum((old.get(f, {}).get('nodes') or 0) for f in new)}"
          f"  新节点 {sum(n.get('nodes', 0) for n in new.values())}")
    print(f"      原残句 {sum((old.get(f, {}).get('fragments') or 0) for f in new)}"
          f"  新残句 {sum(n.get('fragments', 0) for n in new.values())}")


if __name__ == "__main__":
    main()
