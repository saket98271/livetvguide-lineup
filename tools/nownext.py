"""Now / Next for the whole line-up in two small files, so the guide on the TV reads them in seconds.

The guide used to download every guide file its channels point to and read them on the TV: with every World country
ticked that was 161 files, 28 MB to download and 246 MB of XMLTV to read, one after another, three quarters of it for
channels the guide does not carry (measured 10 Nov 2026: about 4-7 minutes on the 1 GB Fire TV Stick, and again after
every restart). Here GitHub reads them once and keeps only the guide's own channels, titles and times, from an hour
back to 32 hours ahead: about 1 MB in all (the owner's choice, 10 Nov 2026). The TV falls back to the guide files
themselves when these are missing.

    python -I tools/nownext.py lineup/channels.json lineup/world.json <out dir>

writes <out dir>/main.tsv.gz (the channel list's channels), world.tsv.gz (the World channels) and report.json. The
format, gzip'd UTF-8 text:

    #NOWNEXT 1 <built, seconds since 1970>
    @<channel id: the "xt" or iptv-org id the guide matches, lower case>
    <start>\t<stop>\t<title>          (seconds since 1970, UTC)

Standard library only; it downloads data files and runs nothing from them.
"""
import datetime
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BACK_S = 3600
AHEAD_S = 32 * 3600
UA = {"User-Agent": "livetvguide-nownext/1 (+https://github.com/saket98271/livetvguide-lineup)"}

PROGRAMME = re.compile(rb"<programme\s([^>]*)>(.*?)</programme>", re.S)
TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.S)


def attr(attrs, name):
    m = re.search(name + rb'="([^"]*)"', attrs)
    return m.group(1).decode("utf-8", "replace") if m else ""


def seconds(value):
    """XMLTV time ("20261110063000 +0000", the zone optional) as seconds since 1970, or 0."""
    v = value.strip()
    try:
        t = datetime.datetime.strptime(v[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return 0
    zone = v[14:].strip()
    offset = 0
    if re.fullmatch(r"[+-]\d{4}", zone):
        offset = (int(zone[1:3]) * 60 + int(zone[3:5])) * 60 * (1 if zone[0] == "+" else -1)
    return int(t.replace(tzinfo=datetime.timezone.utc).timestamp()) - offset


def wanted_ids(channels):
    """The ids the guide looks programmes up by: a channel's "xt", else its iptv-org id before the '@'."""
    out = set()
    for c in channels:
        if c.get("xt"):
            out.add(c["xt"].lower())
        if c.get("id"):
            out.add(c["id"].split("@")[0].lower())
    return out


def guide_urls(lineup):
    urls = []
    for u in [lineup.get("xmltv")] + lineup.get("guides", []):
        if u and u not in urls:
            urls.append(u)
    return urls


def fetch(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                data = r.read()
            return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
        except Exception as e:
            err = e
            time.sleep(3 * (attempt + 1))
    print(f"  failed: {url} ({err})")
    return None


def programmes(data, wanted, lo, hi, out):
    """Adds the programmes of the wanted channels overlapping [lo, hi) in one XMLTV file to out."""
    for m in PROGRAMME.finditer(data):
        a = m.group(1)
        ch = attr(a, rb"channel").lower()
        at = ch.find("@")
        if at >= 0:
            ch = ch[:at]
        if ch not in wanted:
            continue
        start, stop = seconds(attr(a, rb"start")), seconds(attr(a, rb"stop"))
        if not start or stop <= lo or start >= hi:
            continue
        t = TITLE.search(m.group(2))
        title = html.unescape(t.group(1).decode("utf-8", "replace")) if t else ""
        title = " ".join(title.replace("<![CDATA[", "").replace("]]>", "").split())
        if title:
            out.setdefault(ch, {})[(start, title)] = stop


def write(path, built, found):
    lines = [f"#NOWNEXT 1 {built}"]
    for ch in sorted(found):
        lines.append("@" + ch)
        for (start, title), stop in sorted(found[ch].items()):
            lines.append(f"{start}\t{stop}\t{title}")
    data = ("\n".join(lines) + "\n").encode("utf-8")
    with open(path, "wb") as f:
        f.write(gzip.compress(data, 9))
    return len(data), os.path.getsize(path)


def build(channels_path, world_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    built = int(time.time())
    lo, hi = built - BACK_S, built + AHEAD_S
    lists = {"main": json.load(open(channels_path, encoding="utf-8")),
             "world": json.load(open(world_path, encoding="utf-8"))}
    urls = []
    for lineup in lists.values():
        urls += [u for u in guide_urls(lineup) if u not in urls]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as pool:
        files = dict(zip(urls, pool.map(fetch, urls)))
    print(f"{len(urls)} guide files, {sum(len(d) for d in files.values() if d) / 1e6:.0f} MB of XMLTV, "
          f"{time.time() - t0:.0f} s; {sum(1 for d in files.values() if d is None)} failed")
    report = {"built": built, "files": len(urls), "failed": [u for u, d in files.items() if d is None]}
    for name, lineup in lists.items():
        wanted = wanted_ids(lineup["channels"])
        found = {}
        for u in guide_urls(lineup):
            if files.get(u):
                programmes(files[u], wanted, lo, hi, found)
        text, packed = write(os.path.join(out_dir, name + ".tsv.gz"), built, found)
        count = sum(len(v) for v in found.values())
        report[name] = {"channels": len(found), "programmes": count, "bytes": packed}
        print(f"{name}: {len(found)} channels, {count} programmes, {text / 1e6:.1f} MB text, {packed / 1e6:.2f} MB")
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)


if __name__ == "__main__":
    if len(sys.argv) == 4:
        build(*sys.argv[1:4])
    else:
        raise SystemExit(__doc__)
