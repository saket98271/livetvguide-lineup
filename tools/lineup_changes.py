"""What changed between two channel lists: the weekly report the line-up workflow files as a GitHub issue.

    python -I tools/lineup_changes.py OLD.json NEW.json --title-file TITLE.txt > REPORT.md

Channels are matched by name, as the guide does (favourites, recent, Keep/Hide). TITLE.txt is left empty when
nothing was added, removed or moved, and the workflow then files nothing.
"""
import argparse
import json
from collections import defaultdict


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return {c["name"]: c for c in json.load(f)["channels"]}
    except (OSError, ValueError, KeyError):
        return {}


def how(c):
    if c.get("url"):
        return "free stream"
    apps = [l.get("app", "?") for l in c.get("links", [])]
    return " or ".join(dict.fromkeys(apps)) if apps else "?"


def table(rows, head):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(x).replace("|", "/") for x in r) + " |" for r in rows]
    return out


def grouped(channels, by_lang):
    """Lines grouped by language, Hindi and English first, as the guide lists them."""
    groups = defaultdict(list)
    for c in channels:
        groups[c.get("lang", "?")].append(c)
    first = ["Hindi", "English", "Chhattisgarhi", "Gujarati"]
    order = [l for l in first if l in groups] + sorted(l for l in groups if l not in first)
    lines = []
    for lang in order:
        lines.append(f"**{lang}** ({len(groups[lang])})")
        lines.append("")
        lines += table(by_lang(groups[lang]), ["No.", "Channel", "Genre", "Plays in"])
        lines.append("")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--title-file", required=True)
    a = ap.parse_args()
    old, new = load(a.old), load(a.new)
    if not new:
        raise SystemExit("the new channel list is missing or broken")
    added = [new[n] for n in new if n not in old]
    removed = [old[n] for n in old if n not in new]
    moved = [(old[n], new[n]) for n in new if n in old
             and (old[n].get("cat"), old[n].get("lang")) != (new[n].get("cat"), new[n].get("lang"))]
    title = ""
    if old and (added or removed or moved):
        parts = []
        if added:
            parts.append(f"{len(added)} new")
        if removed:
            parts.append(f"{len(removed)} gone")
        if moved:
            parts.append(f"{len(moved)} moved")
        title = "Channel list changes: " + ", ".join(parts)
    with open(a.title_file, "w", encoding="utf-8") as f:
        f.write(title)

    rows = lambda cs: [(c.get("n", ""), c["name"], c.get("cat", ""), how(c)) for c in sorted(cs, key=lambda c: c.get("n", 0))]
    lines = [f"This week's channel list has **{len(new)}** channels (last week {len(old)})." if old
             else f"This week's channel list has **{len(new)}** channels (no earlier list to compare)."]
    lines.append("")
    if added:
        lines += [f"### New channels ({len(added)})", "",
                  "On the TV these wait in the guide's **New** genre: hold OK on one and choose **Keep** "
                  "(it joins its genre) or **Hide**. Channels in languages you have not ticked (MENU) stay out of "
                  "sight until you tick that language.", ""]
        lines += grouped(added, rows)
    if removed:
        lines += [f"### Gone from the list ({len(removed)})", "",
                  "Usually a stream that stopped working or a channel that closed; the guide drops them by itself.", ""]
        lines += grouped(removed, rows)
    if moved:
        lines += [f"### Moved to another genre or language ({len(moved)})", ""]
        lines += table([(b.get("n", ""), b["name"], f'{o.get("cat")} / {o.get("lang")}', f'{b.get("cat")} / {b.get("lang")}')
                        for o, b in sorted(moved, key=lambda p: p[1].get("n", 0))],
                       ["No.", "Channel", "Was", "Now"])
        lines.append("")
    if not (added or removed or moved):
        lines.append("No channels were added, removed or moved.")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
