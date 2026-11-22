"""A few probe questions for the retrieval layer."""
import subprocess
import sys

PY = sys.executable
SCRIPT = r"C:\Users\Lenovo\Desktop\PageIndex\haikou\retrieval.py"

QUESTIONS = [
    ("现金句（现行文件）", "免税店的设立需要什么条件"),
    ("条号直查", "管理办法第十四条讲的是什么"),
    ("已废止文件", "海南自由贸易港建设投资基金的运作方式是什么"),
    ("语料外话题", "海南自贸港的碳排放交易规则是什么"),
]


def main():
    for label, question in QUESTIONS:
        print("=" * 74)
        print(f"# {label}")
        result = subprocess.run([PY, SCRIPT, question, "--top", "3"],
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        body = result.stdout.split("\n", 2)[-1]
        print(body[:1200])


if __name__ == "__main__":
    main()
