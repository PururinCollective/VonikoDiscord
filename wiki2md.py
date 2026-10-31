"""Turn saved Fandom wiki pages (dump/*.html) into short Markdown (profile/*.md).

A saved page is megabytes of site code, ads and images; the Markdown keeps only
the article: the infobox, the sections, lists, quotes and tables.

    python wiki2md.py                       # every dump/*.html -> profile/<first name>.md
    python wiki2md.py page.html -o somewhere/
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import lxml.html

# Sections that are only links, pictures or episode lists.
SKIP_SECTIONS = {"references", "external links", "gallery", "navigation", "media", "images", "appearances", "see also"}
QUOTE_MARKS = {"“", "”", "„", "‟", '"', "«", "»"}

# Page furniture inside the article.
DROP_CLASSES = {
    "mw-editsection", "reference", "toc", "navbox", "wikia-gallery", "mbox", "mw-references-wrap",
    "references-small", "thumb", "pi-image", "pi-image-collection", "gallery-icon-container", "noprint",
}
DROP_TAGS = {"script", "style", "noscript", "svg", "img", "figure", "audio", "video", "iframe", "input", "button"}

BLOCK_TAGS = {
    "p", "div", "section", "aside", "ul", "ol", "dl", "dt", "dd", "table", "blockquote", "center",
    "figure", "pre", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
}
BR = "\x00"  # stands in for <br> until whitespace is collapsed


def classes(el) -> set[str]:
    return set((el.get("class") or "").split())


def tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace("\xa0", " "))
    lines = (line.strip() for line in text.split(BR))
    return "\n".join(line for line in lines if line)


def emphasis(text: str, mark: str) -> str:
    core = text.strip()
    if not core or BR in core:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    return f"{lead}{mark}{core}{mark}{trail}"


def ruby_parts(el) -> tuple[str, str]:
    base = (el.text or "") + "".join(
        "".join(child.itertext()) + (child.tail or "") if child.tag not in ("rt", "rp") else (child.tail or "")
        for child in el if isinstance(child.tag, str)
    )
    reading = "".join("".join(rt.itertext()) for rt in el.iterchildren("rt"))
    return base.strip(), reading.strip()


def inline(el) -> str:
    """The element's content as one line of Markdown (with BR marks)."""
    parts = [el.text or ""]
    children = [child for child in el if isinstance(child.tag, str)]
    i = 0
    while i < len(children):
        child = children[i]
        if child.tag == "ruby":
            # 伊(い)地(じ)知(ち) reads better as 伊地知(いじち)
            base, reading = ruby_parts(child)
            while not (child.tail or "").strip() and i + 1 < len(children) and children[i + 1].tag == "ruby":
                i += 1
                child = children[i]
                more = ruby_parts(child)
                base, reading = base + more[0], reading + more[1]
            parts.append(f"{base}({reading})" if reading else base)
        else:
            parts.append(render_inline(child))
        parts.append(child.tail or "")
        i += 1
    return "".join(parts)


def render_inline(el) -> str:
    tag = el.tag
    if tag == "br":
        return BR
    if tag in ("ul", "ol"):
        return BR + BR.join(tidy(inline(li)) for li in el.iterchildren("li")) + BR
    if tag in BLOCK_TAGS:
        return f" {BR.join(b.replace(chr(10), BR) for b in blocks(el))} "
    text = inline(el)
    if tag in ("b", "strong"):
        return emphasis(text, "**")
    if tag in ("i", "em"):
        return emphasis(text, "*")
    if tag == "span" and el.get("title") and "dotted" in (el.get("style") or ""):
        return f"{text} ({el.get('title')})"  # hover note, e.g. who uses a nickname
    return text


def blocks(el) -> list[str]:
    """The element's content as Markdown blocks (paragraphs, lists, ...)."""
    out: list[str] = []
    run = [el.text or ""]

    def flush():
        text = tidy("".join(run))
        run.clear()
        if text:
            out.append(text)

    for child in el:
        if isinstance(child.tag, str):
            if child.tag in BLOCK_TAGS:
                flush()
                out.extend(block(child))
            else:
                run.append(render_inline(child))
        run.append(child.tail or "")
    flush()
    return out


def block(el) -> list[str]:
    tag, cls = el.tag, classes(el)
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        text = tidy(inline(el)).replace("\n", " ").removesuffix("[]").strip()
        return [f"{'#' * int(tag[1])} {text}"] if text else []
    if tag in ("ul", "ol"):
        lines = list_lines(el)
        return ["\n".join(lines)] if lines else []
    if tag == "dt":
        text = tidy(inline(el)).replace("\n", " ")
        return [f"**{text}**"] if text else []
    if tag == "table":
        return table(el)
    if tag == "hr":
        return []
    if tag == "aside" and "portable-infobox" in cls:
        return infobox(el)
    if "wds-tabber" in cls or "tabber" in cls:
        return tabber(el)
    if tag == "blockquote" or "quote" in cls:
        return ["\n".join(f"> {line}" for b in blocks(el) for line in b.splitlines())]
    return blocks(el)


def list_lines(el, depth: int = 0) -> list[str]:
    lines = []
    for n, li in enumerate(el.iterchildren("li"), 1):
        run, nested = [li.text or ""], []
        for child in li:
            if child.tag in ("ul", "ol"):
                nested += list_lines(child, depth + 1)
            elif isinstance(child.tag, str):
                run.append(render_inline(child))
            run.append(child.tail or "")
        text = tidy("".join(run)).replace("\n", " ")
        bullet = f"{n}." if el.tag == "ol" else "-"
        if text:
            lines.append(f"{'  ' * depth}{bullet} {text}")
        lines += nested
    return lines


def cell_text(cell) -> str:
    return " ".join(blocks(cell)).replace("\n", "; ").replace("|", "\\|")


def table(el) -> list[str]:
    rows = [row.xpath("./th|./td") for row in el.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr")]
    rows = [cells for cells in rows if cells]
    if not rows:
        return []
    marks = [cell for cells in rows for cell in cells if cell.text_content().strip() in QUOTE_MARKS]
    if marks:
        # A quote box: big “ and „ in the side cells, the quote and "~ speaker" in the middle.
        lines = [line for cells in rows for cell in cells if cell not in marks for b in blocks(cell) for line in b.splitlines()]
        return ["\n".join(f"> {line}" for line in lines)] if lines else []
    if el.xpath(".//table") or len({len(cells) for cells in rows}) != 1 or len(rows[0]) < 2:
        # Collapsible boxes and layout tables: keep their content as plain blocks.
        return [b for cells in rows for cell in cells for b in blocks(cell)]
    grid = [[cell_text(cell) for cell in cells] for cells in rows]
    grid = [row for row in grid if any(row)]
    if not grid:
        return []
    keep = [i for i in range(len(grid[0])) if any(row[i] for row in grid[1:])] or range(len(grid[0]))
    grid = [[row[i] for i in keep] for row in grid]  # e.g. a "Preview" column of embedded players
    lines = [f"| {' | '.join(grid[0])} |", f"|{' --- |' * len(keep)}"]
    lines += [f"| {' | '.join(row)} |" for row in grid[1:]]
    return ["\n".join(lines)]


def tabber(el) -> list[str]:
    labels = [tidy("".join(li.itertext())) for li in el.xpath("./div[contains(@class,'wds-tabs__wrapper')]//li")]
    contents = el.xpath("./div[contains(@class,'wds-tab__content')]")
    out = []
    for label, content in zip(labels + [""] * len(contents), contents):
        inner = blocks(content)
        if inner and label:
            out.append(f"**{label}**")
        out += inner
    return out


def infobox(el) -> list[str]:
    lines = []
    for item in el.iter():
        if not isinstance(item.tag, str):
            continue
        cls = classes(item)
        if "pi-title" in cls:
            lines.append(f"**{tidy(inline(item)).replace(chr(10), ' / ')}**")
        elif "pi-header" in cls:
            lines.append(f"\n### {tidy(inline(item))}")
        elif "pi-data" in cls:
            label = item.xpath("./*[contains(@class,'pi-data-label')]")
            value = item.xpath("./*[contains(@class,'pi-data-value')]")
            if value:
                name = tidy("".join(label[0].itertext())).rstrip(":") if label else ""
                text = tidy(inline(value[0])).replace("\n", "; ")
                lines.append(f"- {name}: {text}" if name else f"- {text}")
        elif "pi-horizontal-group" in cls:
            names = [tidy(inline(th)) for th in item.xpath(".//th[contains(@class,'pi-data-label')]")]
            values = [tidy(inline(td)).replace("\n", "; ") for td in item.xpath(".//td[contains(@class,'pi-data-value')]")]
            lines += [f"- {name}: {value}" for name, value in zip(names, values)]
    return ["\n".join(lines).strip()] if lines else []


def drop_furniture(root) -> None:
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            if el.getparent() is not None:
                el.drop_tree()  # comments
            continue
        # Hidden bits like the comma in "下北沢の大天使, Shimokitazawa no Daitenshi", but
        # not the rows of a collapsed table, which the page's script hid.
        hidden = "display:none" in (el.get("style") or "").replace(" ", "").lower() and el.tag != "tr"
        src_link = el.tag == "sup" and el.text_content().strip() == "[src]"
        if el.tag in DROP_TAGS or classes(el) & DROP_CLASSES or hidden or src_link:
            if el.getparent() is not None:
                el.drop_tree()


def drop_banners(article) -> None:
    """Drops what sits above the infobox: "Overview | Gallery" tabs, cleanup notices."""
    if not article.xpath(".//aside[contains(@class,'portable-infobox')]"):
        return
    for child in list(article):
        if not isinstance(child.tag, str):
            continue
        if child.xpath("self::aside[contains(@class,'portable-infobox')]|.//aside[contains(@class,'portable-infobox')]"):
            break
        if child.tag != "p":
            child.drop_tree()


def heading_level(part: str) -> int:
    if not part.startswith("#") or "\n" in part:
        return 0
    return len(part) - len(part.lstrip("#"))


def skip_sections(parts: list[str]) -> list[str]:
    out, skipping = [], 0
    for part in parts:
        level = heading_level(part)
        if level and skipping and level <= skipping:
            skipping = 0
        if level and part[level:].strip().lower() in SKIP_SECTIONS:
            skipping = level
        if not skipping:
            out.append(part)
    # Drop headings left with nothing under them.
    kept = []
    for i, part in enumerate(out):
        level = heading_level(part)
        nxt = heading_level(out[i + 1]) if i + 1 < len(out) else 1
        if level and nxt and nxt <= level:
            continue
        kept.append(part)
    return kept


def convert(path: Path) -> tuple[str, str]:
    """Returns (article title, Markdown)."""
    doc = lxml.html.parse(str(path)).getroot()
    article = doc.xpath("//div[@id='mw-content-text']//div[contains(@class,'mw-parser-output')]")
    if not article:
        raise ValueError("no article text found (is this a saved Fandom/MediaWiki page?)")
    article = article[0]
    title = (doc.findtext(".//title") or path.stem).split("|")[0].strip()
    heading = doc.xpath("//h1[@id='firstHeading']")
    if heading:
        title = tidy(heading[0].text_content()) or title
    url = next(iter(doc.xpath("//meta[@property='og:url']/@content")), "")
    site = next(iter(doc.xpath("//meta[@property='og:site_name']/@content")), "Fandom")

    drop_furniture(article)
    drop_banners(article)
    parts = skip_sections(blocks(article))
    source = f"<{url}>" if url else f"`{path.name}`"
    head = [f"# {title}", f"From {site}: {source}. Community content under CC BY-SA, by the wiki's contributors."]
    text = "\n\n".join(head + parts)
    return title, re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def slug(text: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "page"


def first_name(title: str) -> str:
    return slug(title.split()[0] if title.split() else "page")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")  # page names like "Sakurako Ōmuro" on a cp1252 pipe
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pages", nargs="*", type=Path, help="saved .html pages (default: dump/*.html)")
    parser.add_argument("-o", "--out", type=Path, default=here / "profile", help="output folder (default: profile/)")
    args = parser.parse_args()

    pages = args.pages or sorted((here / "dump").glob("*.html"))
    if not pages:
        print("No .html pages found in dump/.", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    failed = 0
    # Pages about the same character (one per wiki) go into one file.
    by_title: dict[str, list[tuple[Path, str]]] = {}
    for page in pages:
        try:
            title, text = convert(page)
        except (OSError, ValueError) as e:
            print(f"{page.name}: {e}", file=sys.stderr)
            failed += 1
            continue
        by_title.setdefault(title, []).append((page, text))
    # named after her first name, or her whole name if another character shares it ("Hitori Bocchi", "Hitori Gotoh")
    firsts = Counter(first_name(title) for title in by_title)
    for title, parts in by_title.items():
        target = args.out / f"{first_name(title) if firsts[first_name(title)] == 1 else slug(title)}.md"
        text = "\n---\n\n".join(text for _, text in parts)
        target.write_text(text, encoding="utf-8", newline="\n")
        size = sum(page.stat().st_size for page, _ in parts) // 1024
        names = " + ".join(page.name for page, _ in parts)
        print(f"{names} -> {target.relative_to(here) if target.is_relative_to(here) else target}"
              f" ({size:,} KB -> {len(text.encode()) // 1024:,} KB)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
