"""More programme-listing sources, for the channels the builders leave without Now / Next (owner, 10 Nov 2026: "Get
all 128 Indian and 902 world channels now/next by any means possible").

    python -I tools/listing_sources.py assign <channels.json> <world.json> <data dir> <iptv-org/epg folder> <plan out>

For every channel of either list without listings it looks, in this order, for:
  1. i.mjh.nz's own guides by the id in the channel's link (jmp2.uk/plu-, rok-, stvp-; c.mjh.nz ABC Australia) and
     PBS stations by call sign (PBS/all);
  2. Cablecast community-TV servers (the station's own public schedule, /cablecastapi/v1), by the stream's address;
  3. epgshare01's public guide files: US local stations by call sign (the main "-DT" channel), else the exact name in
     a file of the channel's own country, or of a free streaming service (Plex, Distro, Whale TV, TBN+);
  4. the TV-guide sites iptv-org's grabber reads: the channel's iptv-org id (guides.json), else its exact name in a
     working site's channel list for the channel's own country (or a free streaming service's).
Type 1 goes straight into the list ("gi" / "xt" into the i.mjh.nz file). Types 2-4 go to this repository's daily
listings (tools/collect_listings.py, the "listings" branch): the channel gets its country's file (Indian channels:
in.xml.gz) and a key, and <plan out> (lineup/listings_plan.json) says where the daily job collects each key.
Only Python's standard library. Lists are changed in place (before they are signed).
"""
import collections, glob, gzip, html, json, os, re, sys, urllib.error, urllib.request, zlib
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect_listings as L  # noqa: E402

MJH = "https://i.mjh.nz/"
EPGSHARE = "https://epgshare01.online/epgshare01/"
UA = {"User-Agent": "LiveTVGuide-builder"}
# Free streaming services whose channels are the same wherever they are listed.
FAST_EPGSHARE = ("PLEX1", "DISTROTV1", "WHALETVPLUS1", "TBNPLUS1")
FAST_SITES = {"i.mjh.nz", "pluto.tv", "plex.tv", "distro.tv", "xumo.tv", "watch.whaletvplus.com", "nzxmltv.com"}
# Sites that list one country's channels (others carry the country in their site_id, "us#..." / "GB#...").
SITE_COUNTRY = {"tvpassport.com": "US", "streamingtvguides.com": "US", "tvinsider.com": "US", "directv.com": "US",
                "tvtv.us": "US", "tvguide.com": "US", "ontvtonight.com": None, "tataplay.com": "IN", "dishtv.in": "IN",
                "airtelxstream.in": "IN", "jiotv.com": "IN", "sky.com": None}
CC_ALIAS = {"UK": "GB"}
# Checked by hand (10 Nov 2026): the same channel under another name on a site of another country.
MANUAL = {"NSC9 News": {"site": "tataplay.com", "site_id": "1824", "lang": "en", "site_name": "NSC9"},
          "MTA1 World": {"site": "sky.com", "site_id": "GB#3202", "lang": "en", "site_name": "MTA1 World HD"}}


def get(url, limit=None, timeout=60):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read(limit) if limit else r.read()
    except Exception:
        return None


def norm(name):
    s = html.unescape(name).lower().replace("&", "and").replace("+", "plus")
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", s)
    s = re.sub(r"[\W_]+", "", s)
    return s[:-2] if s.endswith("hd") and len(s) > 5 else s


def base_name(c):
    """The playlist's name without the country the World build adds for a name in use elsewhere."""
    return re.sub(r"\s*\((?:[^)]*)\)$", "", c["name"])


def callsigns(name):
    return re.findall(r"\b([KW][A-Z]{2,3})(?:-(?:TV|DT|CD|LD))?\b", name)


# ---- 1. i.mjh.nz by link id / call sign --------------------------------------------------------------------------

_mjh = {}


def mjh_channels(file):
    """{channel id: [names]} of an i.mjh.nz guide file, only channels with programmes; {} without it."""
    if file not in _mjh:
        body = get(MJH + file + ".xml.gz", timeout=120)
        out = {}
        if body and len(body) <= 4 * 1024 * 1024:
            x = gzip.decompress(body).decode("utf-8", "replace")
            listed = collections.Counter(html.unescape(m) for m in re.findall(r'<programme[^>]*?channel="([^"]+)"', x))
            for m in re.finditer(r'<channel id="([^"]+)"[^>]*>(.*?)</channel>', x, re.S):
                cid = html.unescape(m.group(1))
                if listed[cid] >= 2:
                    out[cid] = [html.unescape(n) for n in re.findall(r"<display-name[^>]*>(.*?)</display-name>", m.group(2))]
        _mjh[file] = out
    return _mjh[file]


PLUTO_FILES = [f"PlutoTV/{r}" for r in ("us", "gb", "ca", "de", "fr", "es", "it", "br", "mx", "ar", "dk", "se", "no")]
AU_CITIES = ("Sydney", "Melbourne", "Brisbane", "Adelaide", "Perth", "Hobart", "Darwin", "Canberra")


def mjh_source(c):
    """(i.mjh.nz file, id there) for a channel i.mjh.nz lists under the id in its link or its PBS call sign, or None."""
    u = c.get("url", "")
    m = re.match(r"https?://jmp2\.uk/(plu|rok|stvp)-([0-9A-Za-z]+)", u)
    if m:
        kind, cid = m.groups()
        files = {"rok": ["Roku/all"], "plu": PLUTO_FILES, "stvp": ["SamsungTVPlus/all"]}[kind]
        for f in files:
            if cid in mjh_channels(f):
                return f, cid
        return None
    m = re.match(r"https?://c\.mjh\.nz/([a-z0-9-]+)\.m3u8", u)
    if m:
        for city in AU_CITIES:
            f = f"au/{city}/epg"
            ids = [cid for cid in mjh_channels(f) if cid.lower() == "mjh-" + m.group(1)]
            if ids:
                return f, ids[0]
        return None
    if "pbs.org" in u or c["name"].startswith("PBS "):
        signs = callsigns(c["name"]) + [s.upper() for s in re.findall(r"//([a-z]{4})(?:dt\d?)?\.lls", u)]
        for cid in mjh_channels("PBS/all"):
            if cid.upper() in signs:
                return "PBS/all", cid
    return None


# ---- 2. Cablecast -----------------------------------------------------------------------------------------------

_livestreams = {}
_channels = {}

def cablecast_source(c):
    """(server, Cablecast channel id) for a stream on a Cablecast server whose public API names it exactly."""
    u = c.get("url", "")
    host = urlparse(u).hostname or ""
    if "cablecast.tv" not in host:
        return None
    path = urlparse(u).path
    for h in (host, re.sub(r"^reflect-", "", host)):
        if h not in _livestreams:
            try:
                _livestreams[h] = json.loads(get(f"https://{h}/cablecastapi/v1/livestreams", timeout=30))["liveStreams"]
            except Exception:
                _livestreams[h] = []
        if _livestreams[h]:
            host = h
            break
    streams = _livestreams.get(host, [])
    for s in streams:
        if s.get("channel") and urlparse(s.get("streamUrl") or "").path == path:
            return host, s["channel"]
    # ".../live-7/live/live.m3u8": the stream the server lists under that same "live-7" (one channel only).
    prefix = path.split("/live/")[0] + "/live/"
    same = {s["channel"] for s in streams if s.get("channel") and urlparse(s.get("streamUrl") or "").path.startswith(prefix)}
    if len(same) == 1 and not re.search(r"/stream-\d+/", path):
        return host, same.pop()
    m = re.search(r"/stream-(\d+)/", path)
    if m:
        s = next((s for s in streams if s.get("id") == int(m.group(1)) and s.get("channel")), None)
        if s:
            return host, s["channel"]
        # Some servers answer only the channel list: the channel that names this live stream.
        if host not in _channels:
            try:
                _channels[host] = json.loads(get(f"https://{host}/cablecastapi/v1/channels", timeout=30))["channels"]
            except Exception:
                _channels[host] = []
        ch = [c for c in _channels[host] if int(m.group(1)) in (c.get("liveStreams") or [])]
        if len(ch) == 1:
            return host, ch[0]["id"]
    return None


# ---- 3. epgshare01 ----------------------------------------------------------------------------------------------

def epgshare_tags():
    """The guide files epgshare01 publishes now ("US_LOCALS1", "IN4" ...)."""
    page = get(EPGSHARE, timeout=60) or b""
    return sorted(set(re.findall(r'epg_ripper_([A-Za-z0-9_.-]+?)\.xml\.gz', page.decode("utf-8", "replace"))))


def epgshare_channels(tag):
    """{channel id: [names]} from the start of an epgshare01 file (read only up to its first programme)."""
    dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
    text = ""
    try:
        with urllib.request.urlopen(urllib.request.Request(EPGSHARE + f"epg_ripper_{tag}.xml.gz", headers=UA), timeout=120) as r:
            while "<programme" not in text:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                text += dec.decompress(chunk).decode("utf-8", "replace")
    except Exception:
        return {}
    return {html.unescape(m.group(1)): [html.unescape(n) for n in re.findall(r"<display-name[^>]*>(.*?)</display-name>", m.group(2))]
            for m in re.finditer(r'<channel id="([^"]+)"[^>]*>(.*?)</channel>', text, re.S)}


class EpgShare:
    def __init__(self, countries):
        tags = epgshare_tags()
        wanted = [t for t in tags if t in FAST_EPGSHARE or any(re.match(re.escape(CC_ALIAS.get(cc, cc)) + r"(\d|_LOCALS\d)", t)
                                                                   for cc in countries)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.files = dict(zip(wanted, pool.map(epgshare_channels, wanted)))
        self.names = {t: collections.defaultdict(set) for t in self.files}
        self.callsign = collections.defaultdict(set)
        for t, chans in self.files.items():
            for cid, names in chans.items():
                for n in names:
                    self.names[t][norm(n)].add(cid)
                m = re.match(r"([KW][A-Z0-9]{2,4})-DT\.us_locals\d$", cid)
                if m and t.startswith("US_LOCALS"):
                    self.callsign[m.group(1)].add((t, cid))
        print(f"  epgshare01: {len(self.files)} files, {sum(len(v) for v in self.files.values())} channels")

    def find(self, c, cc):
        """[(tag, id)] best first, or []."""
        if cc == "US":
            for cs in callsigns(c["name"]):
                if self.callsign.get(cs):
                    return [sorted(self.callsign[cs])[0]]
        key = norm(base_name(c))
        if len(key) < 4:
            return []
        own = [t for t in self.files if re.match(re.escape(CC_ALIAS.get(cc, cc)) + r"(\d|_LOCALS\d)", t)]
        out = []
        for t in own + (list(FAST_EPGSHARE) if cc != "IN" else []):
            ids = self.names.get(t, {}).get(key, set())
            if len(ids) == 1:
                out.append((t, next(iter(ids))))
        return out


# ---- 4. iptv-org's grabber sites ----------------------------------------------------------------------------------

def site_country(site, site_id):
    if site in SITE_COUNTRY and SITE_COUNTRY[site]:
        return SITE_COUNTRY[site]
    m = re.match(r"([a-zA-Z]{2})#", site_id)
    if m:
        return m.group(1).upper()
    tld = site.rsplit(".", 1)[-1].upper()
    return {"UK": "GB"}.get(tld, tld) if len(tld) == 2 else None


class SiteLists:
    """Every working site's channel list in the iptv-org/epg checkout, by exact name."""

    def __init__(self, epg_root, working):
        self.by_name = collections.defaultdict(list)
        for path in glob.glob(os.path.join(epg_root, "sites", "*", "*.channels.xml")):
            site = os.path.basename(os.path.dirname(path))
            if site not in working or site == "epgshare01.online":
                continue
            for m in re.finditer(r"<channel ([^>]*)>([^<]*)</channel>", open(path, encoding="utf-8", errors="replace").read()):
                attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
                k = norm(m.group(2))
                if len(k) >= 4 and attrs.get("site_id"):
                    self.by_name[k].append({"site": site, "site_id": html.unescape(attrs["site_id"]),
                                            "lang": attrs.get("lang") or "en", "site_name": html.unescape(m.group(2))})

    def find(self, c, cc):
        want = CC_ALIAS.get(cc, cc)
        out = []
        for e in self.by_name.get(norm(base_name(c)), []):
            if e["site"] in FAST_SITES and cc != "IN" or site_country(e["site"], e["site_id"]) == want:
                out.append(e)
        return out


# ---- assign -------------------------------------------------------------------------------------------------------

def assign(channels_path, world_path, data_dir, epg_root, plan_path):
    lineup = json.load(open(channels_path, encoding="utf-8"))
    world = json.load(open(world_path, encoding="utf-8"))
    src = L.Sources(data_dir)
    sites = SiteLists(epg_root, src.working)
    todo = []   # (list name, channel, country)
    for c in lineup["channels"]:
        if c.get("url") and "gi" not in c and "xt" not in c and "jio" not in c:
            todo.append(("main", c, "IN"))
    for c in world["channels"]:
        if "gi" not in c:
            todo.append(("world", c, c["cc"]))
    es = EpgShare({cc for _, _, cc in todo})
    with ThreadPoolExecutor(max_workers=8) as pool:
        cablecast = dict(zip((id(c) for _, c, _ in todo), pool.map(lambda t: cablecast_source(t[1]), todo)))
    plan, stats = {}, collections.Counter()
    guides = {"main": lineup.setdefault("guides", []), "world": world.setdefault("guides", [])}

    def use(which, c, url, key):
        g = guides[which]
        if url not in g:
            g.append(url)
        c["gi"] = g.index(url)
        c["xt"] = key

    for which, c, cc in todo:
        if which == "world":
            m = mjh_source(c)
            if m:
                use(which, c, MJH + m[0] + ".xml.gz", m[1])
                stats["i.mjh.nz"] += 1
                continue
        key = (L.key(src, c) if c.get("id") else "n~" + norm(base_name(c)) + "~" + cc.lower())
        entries = []
        if cablecast.get(id(c)):
            plan[key] = {"cablecast": list(cablecast[id(c)])}
            stats["cablecast"] += 1
        else:
            if c["name"] in MANUAL:
                entries.append(MANUAL[c["name"]])
            for tag, cid in es.find(c, cc):
                entries.append({"site": "epgshare01.online", "site_id": f"{tag}#{cid}", "lang": "en", "site_name": cid})
            if c.get("id") and which == "main":
                entries += [{"site": g["site"], "site_id": g["site_id"], "lang": g.get("lang") or "en",
                             "site_name": g.get("site_name") or c["name"]} for g in src.candidates(c)]
            entries += sites.find(c, cc)
            seen, uniq = set(), []
            for e in entries:
                if e["site"] not in seen:
                    seen.add(e["site"])
                    uniq.append(e)
            if not uniq:
                continue
            plan[key] = {"sites": uniq[:2]}
            stats["epgshare01" if uniq[0]["site"] == "epgshare01.online" else "sites"] += 1
        use(which, c, L.LISTINGS_BASE + ("in" if which == "main" else cc.lower()) + ".xml.gz", key)
        stats[which] += 1
    for path, data in ((channels_path, lineup), (world_path, world)):
        if path == channels_path and not data["guides"]:
            data.pop("guides")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=0, sort_keys=True)
    print(f"more listings: {dict(stats)}; still without: "
          f"{sum(1 for c in lineup['channels'] if c.get('url') and 'gi' not in c and 'xt' not in c and 'jio' not in c)} "
          f"Indian, {sum(1 for c in world['channels'] if 'gi' not in c)} World")


if __name__ == "__main__":
    if len(sys.argv) >= 7 and sys.argv[1] == "assign":
        assign(*sys.argv[2:7])
    else:
        raise SystemExit(__doc__)
