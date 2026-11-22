"""Show how each line of a document is classified, to debug level mapping."""
import sys

sys.path.insert(0, r"C:\Users\Lenovo\Desktop\PageIndex\chinastruct")

from lines import body_size, extract_lines
from build_tree import _clean_title, _numbering, collect_headings, detect_style, is_heading


def main():
    path = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    lines = extract_lines(path)
    body = body_size(lines)
    style = detect_style(lines, body)
    print(f"# {path}")
    print(f"# lines={len(lines)} body={body:.1f} style={style}\n")

    shown = 0
    for line in lines:
        text = _clean_title(line.text)
        num = _numbering(text)
        ok, level, kind = is_heading(line, body, style, line.page_height)
        if num or ok:
            flag = "HEAD" if ok else "    "
            print(f"p{line.page:<4} h={line.height:4.1f} {flag} L{level} "
                  f"[{kind or '-':<8}] {text[:56]}")
            shown += 1
            if shown >= limit:
                break


if __name__ == "__main__":
    main()
