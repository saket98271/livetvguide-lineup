"""Programme listings for the World channels that no public guide file covers (owner's choice "B", 2026-11-10).

A daily GitHub Actions job (.github/workflows/listings.yml) runs iptv-org's open-source grabber
(https://github.com/iptv-org/epg) for these channels and publishes what it got on this repository's "listings" branch:
one small XMLTV file per country (<cc>.xml.gz, e.g. uk.xml.gz), titles and times only, one hour back to two days
ahead. tools/build_world.py points those channels at their country's file ("gi") with their key there ("xt").

    python -I tools/collect_listings.py plan <world.json> <folder with guides.json, feeds.json, SITES.md> <out.channels.xml>
        the grabber's channel list: a listings site per channel, and a second one where there is one
    python -I tools/collect_listings.py pack <world.json> <grabbed.xml> <out folder> [--previous <folder>]
        one file per country from the grabber's output; a channel the grabber got nothing for today keeps the
        programmes it still has ahead in yesterday's file (--previous)

Only Python's standard library: the job runs it on a plain runner. The grabber's output is read as data only.
"""
import collections, datetime, gzip, json, os, re, sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

LISTINGS_BASE = "https://raw.githubusercontent.com/saket98271/livetvguide-lineup/listings/"
# Sites iptv-org's grabber reads that this guide does not use, with the reason.
LEFT_OUT = {"epg.iptvx.one": "an IPTV seller's guide; who runs it could not be found"}
# The World list's language names -> the ISO 639-1 codes the grabber's sites use: a site in the channel's own
# language is preferred (Turkish titles for a Turkish channel rather than a Russian site's).
LANG2 = {"English": "en", "Spanish": "es", "Portuguese": "pt", "French": "fr", "Italian": "it", "Russian": "ru",
         "German": "de", "Arabic": "ar", "Persian": "fa", "Dutch": "nl", "Turkish": "tr", "Chinese": "zh",
         "Romanian": "ro", "Greek": "el", "Indonesian": "id", "Vietnamese": "vi", "Hungarian": "hu", "Thai": "th",
         "Ukrainian": "uk", "Korean": "ko", "Polish": "pl", "Catalan": "ca", "Serbian": "sr", "Urdu": "ur",
         "Albanian": "sq", "Kurdish": "ku", "Czech": "cs", "Kazakh": "kk", "Danish": "da", "Bulgarian": "bg",
         "Azerbaijani": "az", "Slovak": "sk", "Macedonian": "mk", "Khmer": "km", "Finnish": "fi", "Pashto": "ps",
         "Croatian": "hr", "Slovenian": "sl", "Burmese": "my", "Tajik": "tg", "Georgian": "ka", "Hebrew": "he",
         "Somali": "so", "Bosnian": "bs", "Lithuanian": "lt", "Sinhala": "si", "Letzeburgesch": "lb",
         "Turkmen": "tk", "Armenian": "hy", "Kirghiz": "ky", "Dhivehi": "dv", "Norwegian": "no", "Swedish": "sv",
         "Japanese": "ja", "Swahili": "sw", "Latvian": "lv", "Basque": "eu", "Uzbek": "uz", "Estonian": "et",
         "Lao": "lo", "Tagalog": "tl", "Icelandic": "is", "Malay": "ms", "Amharic": "am", "Belarusian": "be"}
# Listings kept: from an hour ago to two days ahead (the job runs daily; the guide keeps a day ahead).
KEEP_BEFORE_H, KEEP_AHEAD_H = 1, 50
MAX_TITLE = 160
# The grabber's id for a channel's second site (plan), read only when its first site gave nothing (pack).
BACKUP = "|2"


def working_sites(sites_md):
    """Sites iptv-org's SITES.md marks as working (green), less LEFT_OUT."""
    green = set(re.findall(r'<td><a href="sites/([^"]+)">[^<]*</a></td><td align="right">\d+</td>'
                           r'<td align="center">\U0001F7E2</td>', sites_md))
    return green - set(LEFT_OUT)


class Sources:
    """Which working grabber sites list each channel (iptv-org's guides.json), and the channels' main feeds."""

    def __init__(self, folder):
        sites_md = os.path.join(folder, "SITES.md")
        if not os.path.exists(sites_md):
            sites_md = os.path.join(folder, "epg_sites.md")
        self.working = working_sites(open(sites_md, encoding="utf-8").read())
        self.by_channel = collections.defaultdict(list)
        for g in json.load(open(os.path.join(folder, "guides.json"), encoding="utf-8")):
            if g.get("channel") and g.get("site") in self.working and g.get("site_id"):
                self.by_channel[g["channel"]].append(g)
        self.main_feed = {f["channel"]: f["id"] for f in
                          json.load(open(os.path.join(folder, "feeds.json"), encoding="utf-8")) if f.get("is_main")}

    def feed_of(self, c):
        base, _, feed = c["id"].partition("@")
        return base, feed or self.main_feed.get(base, "")

    def candidates(self, c):
        """The grabber entries that can give World channel c its listings, best first; [] for none. Only an entry for
        the channel's own feed (or one with no feed): a local station whose only listings are its network's
        (ABC.us@KAAL vs ABC.us East / West) would show the wrong programmes."""
        base, feed = self.feed_of(c)
        own = LANG2.get(c.get("lang"), "")
        out = [g for g in self.by_channel.get(base, ()) if (g.get("feed") or "") in (feed, "")]
        return sorted(out, key=lambda g: (0 if (g.get("feed") or "") == feed else 1, 0 if g.get("lang") == own else 1))


def key(sources, c):
    """The channel's id in the listings files: the iptv-org id and feed, lower case, with no "@" (the guide drops
    what follows an "@"; one channel's regional feeds must not share listings)."""
    base, feed = sources.feed_of(c)
    return f"{base}~{feed}".lower()


def ours(world):
    """World channels whose listings come from these files, as (channel, country file)."""
    urls = world.get("guides", [])
    for c in world["channels"]:
        gi = c.get("gi", -1)
        if 0 <= gi < len(urls) and urls[gi].startswith(LISTINGS_BASE):
            yield c, urls[gi][len(LISTINGS_BASE):]


def plan(world_path, folder, out_path):
    world = json.load(open(world_path, encoding="utf-8"))
    src = Sources(folder)
    wanted = {}
    for c, _ in ours(world):
        cands = src.candidates(c)
        if cands and c["xt"] not in wanted:
            wanted[c["xt"]] = cands
    # Among equally good entries, the site that serves the most of these channels: fewer sites, fewer failures.
    popular = collections.Counter(g["site"] for cands in wanted.values() for g in {g["site"]: g for g in cands}.values())
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<channels>"]
    sites, backups = collections.Counter(), 0

    def entry(g, xid):
        return (f'  <channel site={quoteattr(g["site"])} lang={quoteattr(g.get("lang") or "en")} '
                f'xmltv_id={quoteattr(xid)} site_id={quoteattr(g["site_id"])}>'
                f'{escape(g.get("site_name") or xid)}</channel>')

    for k, cands in sorted(wanted.items()):
        best = cands[0]
        top = [g for g in cands if (g.get("feed") or "", g.get("lang")) == (best.get("feed") or "", best.get("lang"))]
        g = sorted(top, key=lambda g: (-popular[g["site"]], g["site"], g["site_id"]))[0]
        sites[g["site"]] += 1
        lines.append(entry(g, k))
        # A second site, if there is one, under its own id: used only when the first gives nothing.
        other = [o for o in cands if o["site"] != g["site"]]
        if other:
            lines.append(entry(other[0], k + BACKUP))
            backups += 1
    lines.append("</channels>")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"plan: {len(wanted)} channels from {len(sites)} sites ({backups} with a second site); "
          f"most: {sites.most_common(8)}")


def parse_time(v):
    m = re.fullmatch(r"(\d{14})\s*([+-]\d{4})?", (v or "").strip())
    if not m:
        return None
    t = datetime.datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    z = m.group(2) or "+0000"
    off = datetime.timedelta(hours=int(z[1:3]), minutes=int(z[3:5]))
    return (t - off if z[0] == "+" else t + off).replace(tzinfo=datetime.timezone.utc)


def clean(title):
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", title or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t[:MAX_TITLE].rstrip()


def read_programmes(path, keys, out):
    """Adds {key: [(start, stop, title)]} from an XMLTV file (plain or .gz) for the wanted keys."""
    opener = gzip.open if open(path, "rb").read(2) == b"\x1f\x8b" else open
    with opener(path, "rb") as f:
        for _, el in ET.iterparse(f, events=("end",)):
            if el.tag != "programme":
                if el.tag == "channel":
                    el.clear()
                continue
            k = (el.get("channel") or "").lower()
            start, stop = parse_time(el.get("start")), parse_time(el.get("stop"))
            title = clean(el.findtext("title"))
            if k in keys and start and stop and stop > start and title:
                out[k].append((start, stop, title))
            el.clear()


def fmt(t):
    return t.strftime("%Y%m%d%H%M%S +0000")


def pack(world_path, grabbed, out_dir, previous=None, now=None):
    world = json.load(open(world_path, encoding="utf-8"))
    files = collections.defaultdict(set)   # country file -> keys
    for c, file in ours(world):
        files[file].add(c["xt"].lower())
    keys = set().union(*files.values()) if files else set()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    lo, hi = now - datetime.timedelta(hours=KEEP_BEFORE_H), now + datetime.timedelta(hours=KEEP_AHEAD_H)

    today = collections.defaultdict(list)
    if os.path.exists(grabbed):
        read_programmes(grabbed, keys | {k + BACKUP for k in keys}, today)
    backup_used = 0
    for k in keys:
        if not any(stop > now for _, stop, _ in today.get(k, ())) and today.get(k + BACKUP):
            today[k] = today[k + BACKUP]
            backup_used += 1
    kept_over = collections.defaultdict(list)
    if previous and os.path.isdir(previous):
        for name in os.listdir(previous):
            if name.endswith(".xml.gz"):
                read_programmes(os.path.join(previous, name), keys, kept_over)

    os.makedirs(out_dir, exist_ok=True)
    report, carried = {}, 0
    for file, ks in sorted(files.items()):
        body, n_ch, n_pr = [], 0, 0
        for k in sorted(ks):
            progs = today.get(k) or []
            if not any(stop > now for _, stop, _ in progs) and kept_over.get(k):
                progs = [p for p in kept_over[k] if p[1] > now]
                carried += bool(progs)
            progs = sorted({(s, e, t) for s, e, t in progs if e > lo and s < hi})
            if not progs:
                continue
            n_ch += 1
            n_pr += len(progs)
            body.append(f"<channel id={quoteattr(k)}/>")
            for s, e, t in progs:
                body.append(f"<programme start={quoteattr(fmt(s))} stop={quoteattr(fmt(e))} channel={quoteattr(k)}>"
                            f"<title>{escape(t)}</title></programme>")
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="LiveTVGuide listings">\n'
               + "\n".join(body) + "\n</tv>\n").encode("utf-8")
        with open(os.path.join(out_dir, file), "wb") as f:
            f.write(gzip.compress(xml, 9, mtime=0))
        report[file[:-len(".xml.gz")]] = {"planned": len(ks), "listed": n_ch, "programmes": n_pr}
    summary = {"generated": now.strftime("%Y-%m-%dT%H:%MZ"), "planned": len(keys),
               "listed": sum(r["listed"] for r in report.values()), "from_second_site": backup_used,
               "kept_from_yesterday": carried, "countries": report}
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1, sort_keys=True)
    print(f"pack: {summary['listed']} of {len(keys)} channels have listings ({backup_used} from a second site, "
          f"{carried} kept from yesterday's files), {len(report)} country files")
    return summary


def main():
    if len(sys.argv) >= 5 and sys.argv[1] == "plan":
        plan(sys.argv[2], sys.argv[3], sys.argv[4])
    elif len(sys.argv) >= 5 and sys.argv[1] == "pack":
        prev = sys.argv[sys.argv.index("--previous") + 1] if "--previous" in sys.argv else None
        pack(sys.argv[2], sys.argv[3], sys.argv[4], prev)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
