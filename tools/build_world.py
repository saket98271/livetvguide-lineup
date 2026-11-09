"""The guide's World channels, and the Indian-language channels from abroad, from iptv-org's whole playlist.

Usage:  python -I tools/build_world.py [--offline] [--out lineup/world.json] [--main <the main channels.json>]
        (after build_channels.py, which downloads index.m3u, countries.json and regions.json into data/)

Every entry of index.m3u from a country other than India goes through the same rules as the Indian list, then:
  - its main language is an Indian one (Nepali from Nepal, Bengali from Bangladesh, Tamil from Sri Lanka and the
    UK ...): it joins the main line-up (build_channels.py calls indian_abroad), where the guide holds it in New for
    Keep or Hide. Pakistani channels go to World instead (the owner's choice, 2026-11-09).
  - otherwise it goes to lineup/world.json for the World genres at the end of the genre list ("Entertainment
    World", "Movies World" ...). The guide shows them only while Settings > World channels is ticked, and only for
    the countries ticked in Settings > World countries. Numbers: ten thousand per genre, 10001 and up (Indian
    channels keep 1001-9999). Inside a genre: the UK, the US, Canada, Australia, Ireland and New Zealand first,
    then the other countries A to Z, each country's channels by name.

Left out, each with its reason in data/world_excluded.csv:
  - the Indian list's rules: streams needing a faked referring page, anonymous re-hosts, unlicensed redistributors,
    Indian pay channels outside a licensed platform (build_channels.PAY_BRANDS);
  - pay channels from abroad (DAZN, beIN, Sky's paid channels, HBO, ITV, E4, CNN, Cartoon Network ... FOREIGN_PAY)
    outside a licensed free platform or the broadcaster's own site;
  - every stream on a server that carries such a pay channel: it is a re-broadcaster, so nothing on it is trusted;
  - bare-IP servers carrying three or more channels (re-broadcast servers);
  - personal or anonymous copy sites, a paid US service's streams (Boss TV), and sites carrying many broadcasters'
    channels whose licence could not be found (UNSURE_HOSTS);
  - channels that play only in their own country (iptv-org marks them Geo-blocked), and adult channels;
  - a second stream of the same channel from the same country (the first, best quality, stays).
The weekly build does not test the streams (its machines are outside India); the guide tests the ticked
countries' streams on the TV (StreamCheck.java).
"""
import csv
import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict
from urllib.parse import urlparse

INDEX_URL = "https://iptv-org.github.io/iptv/index.m3u"
WORLD_SUFFIX = " World"
# Ten thousand numbers per genre, in the Indian list's number order; Shopping channels go to Entertainment World.
WORLD_START = {"Entertainment": 10001, "Movies": 20001, "Music": 30001, "Kids": 40001, "Sports": 50001,
               "Infotainment": 60001, "News": 70001, "Business News": 80001, "Devotional": 90001}
# Countries listed first in every World genre (English-speaking); iptv-org's codes ("UK", not "GB").
ENGLISH_FIRST = ["UK", "US", "CA", "AU", "IE", "NZ"]
# Indian languages besides build_channels.LANG_ORDER (English is not one here).
INDIAN_EXTRA = {"Nepali", "Sindhi", "Konkani", "Maithili", "Sanskrit", "Manipuri", "Kashmiri", "Dogri", "Santali",
                "Bodo", "Mizo", "Khasi"}

# The Indian-language channels from abroad were checked one by one on 2026-11-09
# (data/indian_from_abroad_2026-11-09.csv); these are the findings as rules.
PAY_COPIES = {"StarPlus (United Kingdom)": "Disney Star's UK copy of StarPlus",
              "Utsav Bharat": "Disney Star's UK channel",
              "PTC Punjabi Canada": "PTC Punjabi is in the guide through JioTV+; this copy is on a site re-sending 46 channels",
              "Jus Hindi": "US pay channel on a bare-IP server", "Jus One": "US pay channel on a bare-IP server",
              "Jus Punjabi": "US pay channel on a bare-IP server"}
PERSONAL_HOSTS = {"chowdhury-shaheb.com", "shariarsuvo.com", "github.io", "freem3u.xyz"}
PAID_SERVICES = {"bozztv.com": "streams of a paid US service (Boss TV), not free"}
OPERATOR_RESTREAMS = {"shamon-cable.net": "a cable operator's re-broadcast"}
UNSURE_HOSTS = {"ncare.live": "one site carrying 11 channels of different broadcasters",
                "sonarbanglatv.com": "one site carrying several broadcasters' channels",
                "smartplaytv.in": "one site re-sending 54 channels (BBC among them)",
                "antenaplay.ro": "a Romanian site carrying 57 channels",
                "ottlive.co.in": "one site re-sending 46 channels",
                "toonamiaftermath.com": "a fan-run stream of other companies' cartoons"}
# Anime genres in the main list (the owner's, 2026-11-09): anime channels from abroad, by language. Doraemon,
# Shin-chan and the Indian cartoon channels (Hungama ...) stay in Kids (the owner's rule), so they are not here.
ANIME = re.compile(r"\banime\b|animax|crunchyroll|naruto|one piece|yu-?gi-?oh|dragon ?ball|pok[eé]mon|sailor moon|"
                   r"hunter ?x ?hunter|gundam|retro ?crush|hidive|funimation|detective conan|captain tsubasa|"
                   r"inuyasha|boruto|ranma|gintama|\bmanga\b", re.I)
ANIME_BANDS = ("Anime English", "Anime Japanese", "Anime other languages")
# iptv-org writes Japanese as "Japanese" or, for some Pluto TV feeds, the bare code "jap".
JAPANESE = {"Japanese", "jap"}
# Names that say the language when iptv-org's does not ("Yu-Gi-Oh! en espanol" is listed as English).
NAME_LANGUAGE = [(re.compile(r"\ben espa[nñ]ol\b", re.I), "Spanish")]
# iptv-org's language is wrong: Selangor TV is the Malaysian state's Malay channel, not Malayalam.
MISLABELLED = {"SelangorTV": "Malay"}
# Pakistani channels iptv-org files under another country: World only, like the rest of Pakistan's.
PAKISTANI_ABROAD = {"Dunya News UK"}
# Channels India does not permit: Peace TV was refused permission to telecast in India for security reasons
# (I&B Ministry, July 2016).
NOT_PERMITTED = re.compile(r"^peace tv\b", re.I)
# The BBC's UK-only feeds (BBC One, Two, Red Button ...): they play only in the UK, on the BBC's own servers.
BBC_UK_ONLY = re.compile(r"-push[a-z]?-uk[.-]|^vs-.*-uk-live\.|\.cf\.md\.bbci\.co\.uk$.*-uk", re.I)

# Free ad-supported and licensed platforms abroad (besides build_channels.LICENSED_PLATFORMS): a pay brand here is
# the broadcaster's own free channel (Pluto TV, Samsung TV Plus, Plex and Roku streams come through jmp2.uk).
WORLD_PLATFORMS = {"wurl.tv", "tubi.video", "frequency.stream", "getpublica.com", "trace.plus", "stingray.com"}
# Pay channels from abroad, by name. A licensed free platform or the brand's own site is fine; anywhere else the
# stream is an unauthorised re-broadcast (and the server carrying it is not trusted for anything else).
FOREIGN_PAY = re.compile(r"\b(" + "|".join([
    # sport
    r"dazn", r"bein", r"espn\d?", r"fox sports?", r"tnt sports?", r"tsn ?\d?", r"sportsnet", r"supersport",
    r"setanta", r"arena ?sport", r"canal ?\+", r"movistar", r"eleven sports?", r"premier sports?", r"bt sport",
    r"la ?liga tv", r"ziggo sport", r"viaplay", r"polsat sport", r"nova sport", r"cosmote sport", r"digi sport",
    r"sport ?tv ?\d", r"sportv", r"nfl network", r"nba tv", r"mlb network", r"nhl network", r"big ten network",
    r"golf channel", r"tennis channel", r"willow", r"mutv", r"lfc ?tv", r"tyc sports", r"directv sports",
    r"win sports", r"gol ?tv", r"match ?(tv|premier|futbol|arena)", r"premiere", r"combate", r"eurosport",
    # films and series
    r"hbo", r"cinemax", r"showtime", r"starz", r"epix", r"mgm ?\+", r"paramount", r"axn", r"a ?& ?e", r"lifetime",
    r"amc", r"sundance", r"ifc", r"hallmark", r"syfy", r"usa network", r"bravo", r"tbs", r"tnt", r"trutv",
    r"universal (tv|channel)", r"studio universal", r"warner (tv|channel)", r"sony (channel|movies|max)",
    r"fox (life|crime|movies|comedy|channel)", r"fx", r"cinecanal", r"telecine", r"megapix", r"film ?& ?arts",
    r"tv ?1000", r"viju", r"kinopremiera", r"amedia", r"filmbox", r"epic drama", r"viasat", r"c more", r"v sport",
    r"nova cinema", r"cinestar", r"13th street", r"rtl (passion|crime|living)", r"prosieben (maxx|fun)",
    r"sat\.? ?1 (gold|emotions)", r"osn", r"art (cinema|aflam|hekayat)", r"animax", r"astro", r"showcase",
    # the UK's channels, which stream only to UK viewers on the broadcasters' own players
    r"sky (sports?|cinema|atlantic|one|max|showcase|comedy|crime|witness|arts|nature|kids|documentaries|history|"
    r"replay|mix|premiere|action|thriller|family|hits|krimi|uno|tg ?24)",
    r"bbc (one|two|three|four|alba|scotland|wales|parliament|uhd|brit|earth|first|lifestyle|entertainment|nl|"
    r"drama|kids|world news)", r"cbbc", r"cbeebies", r"itv ?\d?", r"itvbe", r"channel 4", r"e4", r"more4", r"film4",
    r"4seven", r"5usa", r"5star", r"5action", r"u ?& ?\w+", r"uktv", r"dave", r"alibi",
    # news, children's, factual and music pay channels
    r"cnn", r"cnbc", r"msnbc", r"fox news(?! radio)", r"fox business", r"cartoon network", r"cartoonito",
    r"boomerang", r"nick(elodeon| ?jr| ?toons)?", r"disney", r"baby ?tv", r"duck ?tv", r"jim ?jam", r"minimax",
    r"da vinci", r"tlc", r"animal planet", r"nat ?geo", r"national geographic", r"discovery", r"history",
    r"travel channel", r"food network", r"hgtv", r"comedy central", r"mtv", r"vh1", r"bet", r"cmt", r"e!",
]) + r")(?:(?<=\w)(?!\w)|(?<=\W))", re.I)

# Regions for Settings > World countries, in this order; a country is in the first region that lists it
# (iptv-org's regions.json), except the countries placed by hand.
REGIONS = [("Near India", "SAS"), ("Middle East", "MIDEAST"), ("Asia", "ASIA"), ("Europe", "EUR"),
           ("North America", None), ("Central America and Caribbean", "NORAM"), ("South America", "LATAM"),
           ("Africa", "AFR"), ("Oceania", "OCE")]
REGION_FIXED = {"US": "North America", "CA": "North America", "MX": "North America", "BM": "North America",
                "GL": "North America", "PM": "North America", "RU": "Europe", "CY": "Europe", "UK": "Europe",
                "XK": "Europe", "HK": "Asia", "MO": "Asia", "TW": "Asia", "GY": "South America",
                "SR": "South America", "GF": "South America", "BQ": "Central America and Caribbean"}


def site(host):
    """The registered site of a host: 'a.b.example.com' -> 'example.com', 'x.tv.co.uk' -> 'tv.co.uk'."""
    if re.fullmatch(r"[\d.]+", host or ""):
        return host
    parts = (host or "?").split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"com", "co", "net", "org", "gov", "ac", "go",
                                                                    "or", "ne", "edu", "tv", "in"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def country_of(tvg_id):
    cid = tvg_id.partition("@")[0]
    return cid.rsplit(".", 1)[-1].upper() if "." in cid else ""


class Abroad:
    """index.m3u with iptv-org's data, and the decision for every entry from abroad."""

    def __init__(self, B):
        self.B = B
        data = B.DATA
        self.entries = B.parse_m3u(os.path.join(data, "index.m3u"))
        self.channels = {c["id"]: c for c in json.load(open(os.path.join(data, "channels.json"), encoding="utf-8"))}
        self.feeds = defaultdict(list)
        for f in json.load(open(os.path.join(data, "feeds.json"), encoding="utf-8")):
            self.feeds[f["channel"]].append(f)
        self.lang_names = {l["code"]: l["name"] for l in
                           json.load(open(os.path.join(data, "languages.json"), encoding="utf-8"))}
        self.indian = (set(B.LANG_ORDER) | INDIAN_EXTRA) - {"English"}
        # iptv-org's India playlist also lists some channels from abroad: those are the Indian list's already.
        india = B.parse_m3u(os.path.join(data, "in.m3u"))
        self.in_urls = {e["url"] for e in india}
        self.in_ids = {e["attrs"].get("tvg-id", "").partition("@")[0].lower() for e in india} - {""}
        self.in_keys = {B.guide_key(self.clean(e["title"])) for e in india}
        hosts = [urlparse(e["url"]).hostname or "" for e in self.entries]
        per_host = Counter(hosts)
        self.ip_rebroadcast = {h for h, n in per_host.items() if re.fullmatch(r"[\d.]+", h) and n >= 3}
        self.platforms = B.LICENSED_PLATFORMS | WORLD_PLATFORMS
        # Servers carrying a pay channel (Indian or foreign) outside a licensed platform or the brand's own site.
        self.pirate_hosts = set()
        for e, h in zip(self.entries, hosts):
            if self.pay_reason(self.clean(e["title"]), h):
                self.pirate_hosts.add(h)

    @staticmethod
    def clean(title):
        return re.sub(r"\s*\(\d{3,4}[pi]\)|\s*\[[^\]]+\]", "", title).strip()

    def pay_reason(self, name, host):
        """Why a stream of this name on this host is a pay channel outside a licensed platform, or None."""
        if site(host) in self.platforms or self.B.domain(host) in self.platforms:
            return None
        m = self.B.PAY_BRANDS.search(name) or FOREIGN_PAY.search(name)
        if not m:
            return None
        # The brand's own site ("cbsnews.com" for CBS News, "foxnews.com" for Fox News Radio) is its own feed.
        brand = re.sub(r"[^a-z0-9]", "", m.group(0).lower())
        if len(brand) >= 3 and brand in re.sub(r"[^a-z0-9]", "", host.lower()):
            return None
        return "pay channel outside a licensed platform"

    def info(self, e):
        tvg = e["attrs"].get("tvg-id", "")
        cid, _, feed_id = tvg.partition("@")
        ch = self.channels.get(cid, {})
        feed = next((f for f in self.feeds.get(cid, []) if f["id"] == feed_id), None) or \
            next((f for f in self.feeds.get(cid, []) if f.get("is_main")), None)
        langs = [self.B.LANG_ALIASES.get(n, n) for n in
                 (self.lang_names.get(c, c) for c in (feed or {}).get("languages", []))]
        name = self.clean(e["title"])
        main = MISLABELLED.get(name, langs[0] if langs else "")
        for pattern, language in NAME_LANGUAGE:
            if pattern.search(name):
                main = language
        cats = [c.lower() for c in (ch.get("categories") or [])] or \
               [g.strip().lower() for g in e["attrs"].get("group-title", "undefined").split(";")]
        band = next((self.B.CATEGORY_MAP[c] for c in cats if c in self.B.CATEGORY_MAP), "Entertainment")
        flags = re.findall(r"\[([^\]]+)\]", e["title"])
        return {"name": name, "tvg": tvg, "cc": country_of(tvg), "langs": langs, "lang": main or "Other", "cats": cats,
                "band": "Entertainment" if band == "Shopping" else band, "nsfw": bool(ch.get("is_nsfw")),
                "geo": "Geo-blocked" in flags, "part": "Not 24/7" in flags,
                "quality": (re.findall(r"\((\d{3,4})[pi]\)", e["title"]) or ["0"])[0]}

    def decide(self, e, i):
        """("main" | "world" | "out", reason) for an entry from abroad (i = self.info(e))."""
        host = urlparse(e["url"]).hostname or ""
        s, d = site(host), self.B.domain(host)
        reason = None
        if "http-referrer" in e["opts"]:
            reason = "needs a faked referring page (hotlink protection)"
        elif i["geo"]:
            reason = "plays only in its own country"
        elif BBC_UK_ONLY.search(host):
            reason = "the BBC's UK-only feed (plays only in the UK)"
        elif i["nsfw"]:
            reason = "adult channel"
        elif NOT_PERMITTED.search(i["name"]):
            reason = "not permitted in India (Peace TV, I&B Ministry 2016)"
        elif i["name"] in PAY_COPIES:
            reason = "pay channel outside a licensed platform: " + PAY_COPIES[i["name"]]
        elif host in self.ip_rebroadcast:
            reason = "bare-IP server re-sending many channels"
        elif s in PERSONAL_HOSTS or d in self.B.ANONYMOUS_HOSTS:
            reason = "copy on a personal or anonymous site"
        elif d in self.B.UNLICENSED_REDISTRIBUTORS:
            reason = "unlicensed redistributor"
        elif s in PAID_SERVICES:
            reason = PAID_SERVICES[s]
        elif s in OPERATOR_RESTREAMS:
            reason = OPERATOR_RESTREAMS[s]
        elif self.pay_reason(i["name"], host):
            reason = self.pay_reason(i["name"], host)
        elif host in self.pirate_hosts:
            reason = "server re-broadcasts pay channels"
        elif s in UNSURE_HOSTS:
            reason = UNSURE_HOSTS[s] + "; no licence found"
        if reason:
            return "out", reason
        anime = self.anime_band(i)
        if anime:
            return "anime", anime
        if i["cc"] == "PK" or i["name"] in PAKISTANI_ABROAD:
            return "world", "Pakistani channel (World only, the owner's choice)"
        if i["lang"] in self.indian:
            return "main", ""
        if re.fullmatch(r"[\d.]+", host):
            # The Indian list keeps a bare-IP stream of a free channel; World, picked from thousands, does not:
            # such a server is rarely the broadcaster's own.
            return "out", "bare-IP server, not the broadcaster's own site"
        return "world", ""

    @staticmethod
    def anime_band(i):
        """The anime genre of an anime channel (by name, or a Japanese animation channel), else None."""
        japanese_cartoons = i["lang"] in JAPANESE and "animation" in i["cats"]
        if not ANIME.search(i["name"]) and not japanese_cartoons:
            return None
        if i["lang"] == "English":
            return ANIME_BANDS[0]
        return ANIME_BANDS[1] if i["lang"] in JAPANESE else ANIME_BANDS[2]

    def abroad(self):
        """(entry, info, decision, reason) for every entry with a country other than India, except those
        iptv-org's India playlist lists too (the Indian list has them already). An anime channel's "reason" is its
        anime genre."""
        for e in self.entries:
            i = self.info(e)
            if not i["cc"] or i["cc"] == "IN":
                continue
            if e["url"] in self.in_urls or i["tvg"].partition("@")[0].lower() in self.in_ids:
                continue
            d, why = self.decide(e, i)
            if d in ("main", "anime") and self.B.guide_key(i["name"]) in self.in_keys:
                continue
            yield e, i, d, why


def indian_abroad(B):
    """For build_channels.py's main line-up: index.m3u entries from abroad whose main language is Indian, and the
    anime channels from abroad (each entry's "anime_band" says which anime genre)."""
    path = os.path.join(B.DATA, "index.m3u")
    if not os.path.exists(path):
        return []
    out = []
    for e, i, d, why in Abroad(B).abroad():
        if d == "anime":
            e["anime_band"] = why
        if d in ("main", "anime"):
            out.append(e)
    return out


GUIDE_BASE = "https://i.mjh.nz/"
# A guide file the 1 GB stick downloads (daily) and reads: bigger ones are skipped (SkyGo's is 6.6 MB).
MAX_GUIDE_BYTES = 4 * 1024 * 1024
_guide_checked = {}


def guide_ok(file):
    """True when i.mjh.nz has this guide file and it is small enough for the TV (checked once per build). Without a
    network answer it counts as there: the TV simply skips a guide it cannot get."""
    if file not in _guide_checked:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(GUIDE_BASE + file + ".xml.gz", method="HEAD",
                                     headers={"User-Agent": "LiveTVGuide-builder"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                _guide_checked[file] = r.status == 200 and int(r.headers.get("Content-Length") or 0) <= MAX_GUIDE_BYTES
        except urllib.error.HTTPError:
            _guide_checked[file] = False
        except Exception:
            _guide_checked[file] = True
    return _guide_checked[file]


def mjh_guides(data):
    """{iptv-org channel id: [(file, id in that file)]} for the channels i.mjh.nz's public XMLTV guides list
    (Pluto TV, Plex, Roku, Samsung TV Plus, Australia's free channels ...; iptv-org's guides.json says which):
    "PlutoTV/us#5ad8..." is the file https://i.mjh.nz/PlutoTV/us.xml.gz, channel "5ad8...". Nothing without the file."""
    path = os.path.join(data, "guides.json")
    out = defaultdict(list)
    if not os.path.exists(path):
        return out
    for g in json.load(open(path, encoding="utf-8")):
        if g.get("site") == "i.mjh.nz" and g.get("channel") and "#" in (g.get("site_id") or ""):
            file, _, xid = g["site_id"].partition("#")
            out[g["channel"]].append((file, xid))
    return out


def pick_guide(guides, pluto, c):
    """(file, id) of the guide that lists channel c, or None. A Pluto TV stream takes Pluto TV's guide (its own
    country's, else one that has it); others take a guide for their own country, then Roku's (one file for all
    countries), then any."""
    m = re.match(r"https?://jmp2\.uk/plu-([0-9a-f]+)", c["url"])
    region = {"UK": "gb"}.get(c["cc"], c["cc"].lower())
    if m and m.group(1) in pluto:
        where = pluto[m.group(1)]
        r = region if region in where else ("us" if "us" in where else sorted(where)[0])
        if guide_ok(f"PlutoTV/{r}"):
            return f"PlutoTV/{r}", m.group(1)
    options = [o for o in guides.get(c["id"].partition("@")[0], []) if guide_ok(o[0])]
    if not options:
        return None

    def rank(o):
        file = o[0]
        own = file.lower().endswith("/" + region) or ("/" + region + "/") in ("/" + file.lower() + "/")
        return (0 if own else 1 if file == "Roku/all" else 2, file)
    return sorted(options, key=rank)[0]


def pluto_numbers(data):
    """{Pluto TV channel id: {region: channel number}} from i.mjh.nz's public Pluto TV data, or {} without it."""
    path = os.path.join(data, "plutotv_channels.json")
    if not os.path.exists(path):
        return {}
    out = defaultdict(dict)
    try:
        for region, r in json.load(open(path, encoding="utf-8"))["regions"].items():
            for cid, ch in r.get("channels", {}).items():
                if isinstance(ch.get("chno"), int):
                    out[cid][region] = ch["chno"]
    except (ValueError, KeyError):
        return {}
    return out


def pluto_number(numbers, url, cc):
    """A Pluto TV channel's number in its own country's Pluto TV guide (as viewers there see it); None for a stream
    that is not Pluto TV's, or one Pluto TV lists only in other countries (iptv-org files many Latin American and
    Brazilian feeds under the US: their numbers would put them on top of the US channels)."""
    m = re.match(r"https?://jmp2\.uk/plu-([0-9a-f]+)", url)
    if not m:
        return None
    return numbers.get(m.group(1), {}).get({"UK": "gb"}.get(cc, cc.lower()))


def regions_of(data, codes):
    regions = {r["code"]: set(r["countries"]) for r in json.load(open(os.path.join(data, "regions.json"), encoding="utf-8"))}
    out = {}
    for c in codes:
        out[c] = REGION_FIXED.get(c) or next((name for name, code in REGIONS if code and c in regions.get(code, ())),
                                             "Other")
    return out


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import build_channels as B
    root = B.ROOT
    out_path = os.path.join(root, "lineup-world.json")
    if "--out" in sys.argv:
        out_path = os.path.abspath(sys.argv[sys.argv.index("--out") + 1])
    # Not B.OUT: build_channels reads "--out" from the command line too, and here that is the World list.
    main_path = os.path.join(root, "app", "src", "main", "assets", "channels.json")
    if "--main" in sys.argv:
        main_path = os.path.abspath(sys.argv[sys.argv.index("--main") + 1])
    if "--offline" not in sys.argv or not os.path.exists(os.path.join(B.DATA, "index.m3u")):
        B.fetch("--offline" in sys.argv)
    names_in_use = {c["name"].lower() for c in json.load(open(main_path, encoding="utf-8"))["channels"]}

    A = Abroad(B)
    country_names = {c["code"]: c["name"] for c in
                     json.load(open(os.path.join(B.DATA, "countries.json"), encoding="utf-8"))}
    kept, excluded, seen_urls = [], [], set()
    for e, i, d, why in A.abroad():
        if d in ("main", "anime"):
            # In the main line-up: Indian languages, and the anime genres.
            continue
        if d == "out":
            excluded.append((i["name"], country_names.get(i["cc"], i["cc"]), site(urlparse(e["url"]).hostname or ""), why))
            continue
        if e["url"] in seen_urls:
            continue
        seen_urls.add(e["url"])
        c = {"name": i["name"], "cat": i["band"], "lang": i["lang"], "url": e["url"], "id": i["tvg"], "cc": i["cc"]}
        if i["quality"] != "0":
            c["q"] = i["quality"] + "p"
        if e["attrs"].get("tvg-logo"):
            c["logo"] = e["attrs"]["tvg-logo"]
        if "http-user-agent" in e["opts"]:
            c["ua"] = e["opts"]["http-user-agent"]
        if i["part"]:
            c["part"] = 1
        c["_q"] = int(i["quality"])
        kept.append(c)

    # One stream per channel and country: the best quality stays.
    best = {}
    for c in kept:
        k = (c["name"].lower(), c["cc"])
        if k not in best or c["_q"] > best[k]["_q"]:
            best[k] = c
    for c in kept:
        if best[(c["name"].lower(), c["cc"])] is not c:
            excluded.append((c["name"], country_names.get(c["cc"], c["cc"]), site(urlparse(c["url"]).hostname or ""),
                             "another stream of the same channel"))
    kept = [c for c in kept if best[(c["name"].lower(), c["cc"])] is c]

    def natural(name):
        return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name.lower())]

    def country_rank(cc):
        return (ENGLISH_FIRST.index(cc), "") if cc in ENGLISH_FIRST else (len(ENGLISH_FIRST), country_names.get(cc, cc))

    # Inside a country, the order its viewers see where it is known: Pluto TV's channels in Pluto TV's own numbers
    # (i.mjh.nz's public Pluto TV data), then the other channels by name.
    pluto = pluto_numbers(B.DATA)

    def system_rank(c):
        n = pluto_number(pluto, c["url"], c["cc"])
        return (0, n) if n is not None else (1, 0)

    numbered = []
    for band, start in WORLD_START.items():
        group = sorted((c for c in kept if c["cat"] == band),
                       key=lambda c: (country_rank(c["cc"]), system_rank(c), natural(c["name"])))
        if len(group) > 9999:
            raise SystemExit(f"{band} World has {len(group)} channels, more than its ten thousand numbers")
        for n, c in enumerate(group):
            c["n"] = start + n
            c["cat"] = band + WORLD_SUFFIX
            del c["_q"]
            # Names are the guide's identity for a channel (favourites, recent, stream tests): one that the
            # Indian list or another country already uses gets the country added.
            if c["name"].lower() in names_in_use:
                c["name"] = f"{c['name']} ({country_names.get(c['cc'], c['cc'])})"
            k = 2
            base = c["name"]
            while c["name"].lower() in names_in_use:
                c["name"] = f"{base} {k}"
                k += 1
            names_in_use.add(c["name"].lower())
            numbered.append(c)

    # Programme listings: the i.mjh.nz guide file that lists each channel ("gi", an index into "guides") and the
    # channel's id there ("xt"). The guide downloads only the files its ticked countries' channels need.
    guides, guide_files = mjh_guides(B.DATA), []
    pluto_ids = pluto_numbers(B.DATA)
    for c in numbered:
        g = pick_guide(guides, pluto_ids, c)
        if g:
            if g[0] not in guide_files:
                guide_files.append(g[0])
            c["gi"] = guide_files.index(g[0])
            c["xt"] = g[1]
    counts = Counter(c["cc"] for c in numbered)
    region = regions_of(B.DATA, counts)
    countries = sorted(({"cc": cc, "name": country_names.get(cc, cc), "region": region[cc], "n": n}
                        for cc, n in counts.items()), key=lambda c: c["name"])
    payload = {"generated": datetime.date.today().isoformat(), "version": int(datetime.datetime.now().timestamp()),
               "checked": False, "source": INDEX_URL,
               "bands": [b + WORLD_SUFFIX for b in B.BAND_DISPLAY if any(c["cat"] == b + WORLD_SUFFIX for c in numbered)],
               "regions": [name for name, _ in REGIONS if any(c["region"] == name for c in countries)],
               "countries": countries, "guides": [GUIDE_BASE + f + ".xml.gz" for f in guide_files],
               "channels": numbered}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(B.DATA, "world_excluded.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["channel", "country", "stream site", "reason"])
        w.writerows(sorted(excluded, key=lambda r: (r[3], r[1], r[0].lower())))

    print(f"World: {len(numbered)} channels from {len(countries)} countries; left out {len(excluded)}")
    for reason, n in Counter(r[3].split(":")[0].split(";")[0] for r in excluded).most_common():
        print(f"  left out {n:5}  {reason}")
    for band in payload["bands"]:
        nums = [c["n"] for c in numbered if c["cat"] == band]
        print(f"  {band:20} {len(nums):5} channels  {min(nums)}-{max(nums)}")
    print("regions:", {r: sum(1 for c in countries if c["region"] == r) for r in payload["regions"]})
    print(f"programme listings: {sum(1 for c in numbered if 'gi' in c)} of {len(numbered)} channels, "
          f"from {len(guide_files)} i.mjh.nz guide files")
    print(f"wrote {out_path} ({os.path.getsize(out_path) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
