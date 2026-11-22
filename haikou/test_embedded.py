"""Prove the embedded-clause path flags a document that a new law repealed."""
import sys

sys.path.insert(0, r"C:\Users\Lenovo\Desktop\PageIndex\haikou")

from warn import load_index, status_for

from paths import DEFAULT_DATABASE as DB


def main():
    index = load_index(DB)
    print(f"废止来源：明文通知 {len(index['notices'])} 份，"
          f"文件内废止条款 {len(index.get('embedded', []))} 条\n")

    for name in ("海南经济特区禁毒条例", "海南自由贸易港禁毒条例",
                 "海南自由贸易港商事调解组织登记管理办法"):
        doc = {"file": name + ".txt", "title": name, "doc_numbers": []}
        result = status_for(doc, index)
        print(f"《{name}》")
        print(f"    判定: {result['status']}（{result['confidence']}）")
        evidence = result["evidence"]
        if evidence.get("repealed_by"):
            print(f"    依据: {evidence['repealed_by']}")
            print(f"    生效: {evidence.get('effective_date')}")
            if evidence.get("source"):
                print(f"    来源: {evidence['source']}")
        print()


if __name__ == "__main__":
    main()
