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

Programme listings ("gi": an index into "guides", "xt": the channel's id in that file): i.mjh.nz's public guide
files where iptv-org links a channel to one, then more of them by exact name (NAMED_GUIDES), then this repository's
own daily listings for channels iptv-org's grabber can collect (tools/collect_listings.py).
"""
import csv
import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect_listings as L  # noqa: E402  (this folder; Python's standard library only)

INDEX_URL = "https://iptv-org.github.io/iptv/index.m3u"
# Which of iptv-org's grabber sites work (green), for the daily listings.
SITES_MD_URL = "https://raw.githubusercontent.com/iptv-org/epg/master/SITES.md"


def fetch_to(url, path):
    """Downloads url to path; without a network answer the last copy stays (an error only if there is none)."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LiveTVGuide-builder"})
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read()
        with open(path, "wb") as f:
            f.write(body)
    except Exception:
        if not os.path.exists(path):
            raise
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


# A channel whose own stream cannot be watched from India, by iptv-org's tvg-id: the same channel on a licensed free
# platform that can (checked from India, every video piece downloading).
STREAM_SWAPS = {
    # Anime English 4503 Naruto: VIZ's own feed (d21vn5eki5qsdi.cloudfront.net) sends its playlist but refuses every
    # video piece outside the US (403 from a CloudFront function, 10 Nov 2026). Pluto TV's official Naruto channel
    # (US, its channel 4020) plays. Owner, 10 Nov 2026: "make the channel number 4503 work".
    "Naruto.us@US": "https://jmp2.uk/plu-5da0c85bd2c9c10009370984.m3u8",
}


# Entries iptv-org files wrongly, by tvg-id: (name, language). They go after their genre's own channels, so the
# fewest numbers move.
ENTRY_FIXES = {
    # Our 4602 "One Piece" (Anime Japanese, iptv-org: US, Japanese) is Pluto TV Italy's channel (its 700; the stream's
    # audio LANGUAGE="it"). Owner, 10 Nov 2026: "4602 - other language".
    "OnePiece.us@US": ("One Piece Italy", "Italian"),
}


class Abroad:
    """index.m3u with iptv-org's data, and the decision for every entry from abroad."""

    def __init__(self, B):
        self.B = B
        data = B.DATA
        self.entries = B.parse_m3u(os.path.join(data, "index.m3u"))
        for e in self.entries:
            swap = STREAM_SWAPS.get(e["attrs"].get("tvg-id", ""))
            if swap:
                e["url"] = swap
                e["title"] = re.sub(r"\s*\(\d{3,4}[pi]\)", "", e["title"])   # the old feed's quality
            fix = ENTRY_FIXES.get(e["attrs"].get("tvg-id", ""))
            if fix:
                e["title"], e["lang"], e["extra"] = fix[0], fix[1], 1
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
        main = e.get("lang") or main   # ENTRY_FIXES
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


# English anime channels iptv-org's playlist does not list (owner, 10 Nov 2026: "Any more anime channels (English
# audio) ... in any possible way"): Pluto TV US channels (a licensed free platform, through i.mjh.nz's jmp2.uk like
# the others) whose stream plays in India with an English audio track, checked from India on 10 Nov 2026 (playlist,
# video pieces, the master playlist's LANGUAGE="en"). They follow the playlist's own anime channels, so no number
# given before moves. Left out: Boruto (Japanese audio), Samsung TV Plus's anime channels (refused in India, except
# Anime All Day, whose language could not be confirmed), Plex's (need a Plex token).
EXTRA_ANIME = [("Ani-Monsters", "6aaadf258cc64f2b2b7dbe3c"), ("Big A Anime", "6a3c5fa46276c42ac6726b27"),
               ("Inuyasha", "67c63b90a20c0399a381b816"), ("It's Anime", "6a4fda4538f47f0af402bdb0"),
               ("One Piece (English)", "5f7790b3ed0c88000720b241"), ("Pokémon", "6675c7868768aa0008d7f1c7"),
               ("Sailor Moon", "637e55347427a40007fac703")]


# Samsung TV Plus India (a licensed free service in India; through i.mjh.nz's jmp2.uk "stvp-" links) channels the
# Indian list lacks (owner, 10 Nov 2026: "add more channels ... as much as possible in Hindi/chhattisgarhi/English
# ... on their respective genre", then "Why leaving the Korean and regional channels, put them in its respective
# genre and language by researching thoroughly"): {Samsung id: (genre, language)}. Each played from India on
# 10 Nov 2026. Languages from Samsung's descriptions ("Korean TV ... Hindi languages", "Hollywood Desi ... dubbed in
# Hindi"), its guide's programme titles and descriptions, and Samsung's own news (its four Warner Bros. channels other
# than Wheel World are in Hindi); Foodxp as its sister Travelxp HD's Samsung feed (Hindi). Not confirmed anywhere,
# taken as Hindi for Indian-made channels: Dishoom Films, Auto Live, Cook & Bake. Left out: channels the list has under
# another name (News State MP CG / PUN HAR HIM / UP UK, News24 MP CG, News18 Keralam / Punjab Haryana, The Pet
# Collective, WeatherSpy, Animax, India Daily 24x7, Malai Murasu Seithikal), and Bollywood Masala and @K-MUSIC (their
# streams did not play). A channel Samsung drops leaves by itself (only its current India line-up's are added).
SAMSUNG_IN = {
    # Hindi, added on the second look
    "IN3600005NF": ("Entertainment", "Hindi"),   # Korean TV (Korean dramas in Hindi)
    "IN3800010SH": ("Movies", "Hindi"),          # Hollywood Desi (dubbed in Hindi)
    "IN460001727": ("Movies", "Hindi"),          # Dishoom Films (not confirmed)
    "IN3600007DC": ("Infotainment", "Hindi"),    # Auto Live (not confirmed)
    "IN46000105Q": ("Infotainment", "Hindi"),    # Cook & Bake (not confirmed)
    "IN3000003QJ": ("Infotainment", "Hindi"),    # Foodxp
    "IN460000313": ("Infotainment", "Hindi"),    # Foodie Hub (Warner Bros.)
    # Korean, Japanese
    "IN49000103E": ("Entertainment", "Korean"),  # CJ ENM K-Reality
    "IN4600019L1": ("Entertainment", "Korean"),  # Series K BY MBC
    "IN490000951": ("Infotainment", "Korean"),   # CJ ENM K-Food & Travel
    "IN3800006RD": ("Music", "Korean"),          # CJ ENM K-POP
    "IN4900008ZP": ("Music", "Korean"),          # SMTOWN
    "IN400057EZ": ("Sports", "Japanese"),        # Golf Network
    # Other Indian languages
    "IN800016BA": ("Entertainment", "Gujarati"),   # JOJO TV
    "IN4600016DM": ("Entertainment", "Marathi"),   # Rangmanch
    "IN39000282A": ("Movies", "Tamil"),            # Thalaivar Hits
    "INBD2500016OR": ("News", "Bengali"),          # R Bangla
    # Hindi
    "IN4600011RZ": ("Devotional", "Hindi"),      # Bhakti Play
    "IN30009003": ("Devotional", "Hindi"),       # Dharma Live (ABP)
    "IN26000170D": ("Devotional", "Hindi"),      # Astro Tak
    "IN3900027S7": ("Entertainment", "Hindi"),   # Aashirwad TV
    "IN3600006RT": ("Entertainment", "Hindi"),   # Comedy King
    "IN2600015OO": ("Entertainment", "Hindi"),   # Crime Tak
    "IN400000830": ("Entertainment", "Hindi"),   # ENT Live (ABP)
    "IN8000171I": ("Entertainment", "Hindi"),    # Epic Crimes
    "IN2600020H9": ("Entertainment", "Hindi"),   # India TV Aap ki Adalat
    "IN46000184H": ("Entertainment", "Hindi"),   # Kings Of Comedy
    "IN300091WU": ("Entertainment", "Hindi"),    # Sansani Live (ABP)
    "IN3800002VK": ("Entertainment", "Hindi"),   # The Lallantop
    "IN300085JT": ("News", "Hindi"),             # News State BR JH
    "IN3900001YE": ("News", "Hindi"),            # News24
    "INBA490002119": ("News", "Hindi"),          # Republic Bharat
    "IN2600021ED": ("Infotainment", "Hindi"),    # India TV Yoga
    "IN4600009F9": ("Kids", "Hindi"),            # Hooray Kids
    "IN4600008T4": ("Kids", "Hindi"),            # KidDo MATIX
    "IN3900016WI": ("Movies", "Hindi"),          # B4U Movies
    "IN8000196E": ("Music", "Hindi"),            # Epic Music
    "IN3000899C": ("Sports", "Hindi"),           # Sports Live (ABP)
    # English
    "INBA5100021WP": ("Business News", "English"),   # Bloomberg TV
    "INBA5100022O4": ("Business News", "English"),   # Bloomberg Originals
    "INBA4900020A5": ("News", "English"),            # Republic TV
    "IN3600009O8": ("Entertainment", "English"),     # Challenge Accepted
    "IN24000030L": ("Entertainment", "English"),     # Crime Scene TV
    "INBA5100030AO": ("Entertainment", "English"),   # FailArmy
    "IN3000022R9": ("Entertainment", "English"),     # Fifth Gear
    "IN4600004HW": ("Entertainment", "Hindi"),       # House of Crime (Warner Bros., Hindi)
    "IN3900022KR": ("Entertainment", "English"),     # Mark Rober TV
    "IN2200004MX": ("Entertainment", "English"),     # Midsomer Murders
    "IN24000052F": ("Entertainment", "English"),     # Star Central
    "IN2200002GZ": ("Entertainment", "English"),     # The Cube
    "IN390002534": ("Entertainment", "English"),     # The Try Guys
    "IN39000216A": ("Entertainment", "English"),     # Epic Gardening TV
    "IN22000018J": ("Entertainment", "English"),     # Homes Under the Hammer
    "IN22000039Q": ("Entertainment", "English"),     # Inside Outside
    "IN2400001L0": ("Entertainment", "English"),     # Life+Style
    "INBA5100031R2": ("Entertainment", "English"),   # People are Awesome
    "IN39000260R": ("Entertainment", "English"),     # TheSorryGirls TV
    "IN3300001P5": ("Entertainment", "English"),     # Time2Rlx
    "IN1600011DE": ("Entertainment", "English"),     # KCM (series and true-crime documentaries)
    "IN4000517J": ("Movies", "English"),             # Best Action TV
    "IN330000528": ("Movies", "English"),            # WedoTV Movies
    "IN400054UF": ("Infotainment", "English"),       # Automotions
    "IN3600008TY": ("Infotainment", "English"),      # Brave Wilderness
    "IN2400002A3": ("Infotainment", "English"),      # Go Wild
    "INBA4900024R1": ("Infotainment", "English"),    # Jack Hanna
    "IN2100014K1": ("Infotainment", "English"),      # NatureTime
    "IN3300006TC": ("Infotainment", "English"),      # WedoTV Big Stories
    "IN3300003TW": ("Infotainment", "English"),      # WedoTV Get Factual
    "IN460000250": ("Infotainment", "English"),      # Wheel World
    "IN460000585": ("Infotainment", "Hindi"),        # Wild Flix (Warner Bros., Hindi)
    "IN800006BS": ("Infotainment", "English"),       # Wild Stuff
    "IN46000011Q": ("Infotainment", "Hindi"),        # XXTreme Jobs (Warner Bros., Hindi)
    "INBA4900023QJ": ("Infotainment", "English"),    # Xplore
    "IN2400004WL": ("Infotainment", "English"),      # Bon Voyage
    "INBA5100020HQ": ("Infotainment", "English"),    # Gusto TV
    "IN400053KM": ("Infotainment", "English"),       # HD Travel
    "IN4000526Y": ("Infotainment", "English"),       # Heritage+
    "IN4900012F9": ("Kids", "English"),              # BABY SHARK TV
    "IN4600007TC": ("Kids", "English"),              # Hooray Rhymes
    "IN2500003JW": ("Kids", "English"),              # PowerKids TV
    "INBA4900026FJ": ("Kids", "English"),            # Toon Goggles
    "IN800005S8": ("Music", "English"),              # Concerto
    "INBA4900001DL": ("Music", "English"),           # Qwest TV
    "IN800007SR": ("Music", "English"),              # TRACE Urban
    "IN800001OD": ("Music", "English"),              # That's 70s
    "IN8000027E": ("Music", "English"),              # That's 80s
    "IN800004YV": ("Music", "English"),              # That's 90s00s
    "IN800003JE": ("Music", "English"),              # That's ROCK
    "IN400056QQ": ("Sports", "English"),             # Ace TV
    "IN1300001NN": ("Sports", "English"),            # Cricket Gold
    "IN270000230": ("Sports", "English"),            # FIFA+
    "IN300086YL": ("Sports", "English"),             # Fight TV
    "INBA49000067X": ("Sports", "English"),          # INTROUBLE
    "INBD4700014DU": ("Sports", "English"),          # MAVTV Motorsports
    "IN4600020Q3": ("Sports", "English"),            # Pickleball Now
    "IN8000085E": ("Sports", "English"),             # TRACE Sport Stars
    "IN3300004EI": ("Sports", "English"),            # WedoTV Cars
    "IN2100015RL": ("Sports", "English"),            # WedoTV Sports
}


def extra_samsung_in(data):
    """SAMSUNG_IN as playlist entries for build_channels.py, named and with logos as Samsung's India line-up has them
    (i.mjh.nz's public data); none without that data."""
    path = os.path.join(data, "samsungtvplus_channels.json")
    if not os.path.exists(path):
        return []
    india = json.load(open(path, encoding="utf-8"))["regions"].get("in", {}).get("channels", {})
    out = []
    for cid, (band, lang) in SAMSUNG_IN.items():
        ch = india.get(cid)
        if not ch or not ch.get("name"):
            continue
        attrs = {"tvg-id": "", "group-title": band.lower()}
        if ch.get("logo"):
            attrs["tvg-logo"] = ch["logo"]
        # "xt": Samsung's India guide names channels by the same id (build_channels keeps it if the guide has it).
        out.append({"title": ch["name"], "url": f"https://jmp2.uk/stvp-{cid}", "attrs": attrs, "opts": {},
                    "band": band, "lang": lang, "extra": 1, "india": 1, "xt": cid})
    return out


def extra_anime(data):
    """EXTRA_ANIME as playlist entries for build_channels.py (Anime English, English), with Pluto TV's logos."""
    logos = {}
    path = os.path.join(data, "plutotv_channels.json")
    if os.path.exists(path):
        us = json.load(open(path, encoding="utf-8"))["regions"].get("us", {}).get("channels", {})
        logos = {cid: ch.get("logo") for cid, ch in us.items()}
    out = []
    for name, cid in EXTRA_ANIME:
        attrs = {"tvg-id": "", "group-title": "Animation"}
        if logos.get(cid):
            attrs["tvg-logo"] = logos[cid]
        out.append({"title": name, "url": f"https://jmp2.uk/plu-{cid}.m3u8", "attrs": attrs, "opts": {},
                    "anime_band": ANIME_BANDS[0], "lang": "English", "extra": 1})
    return out


def indian_abroad(B):
    """For build_channels.py's main line-up: index.m3u entries from abroad whose main language is Indian, and the
    anime channels from abroad (each entry's "anime_band" says which anime genre), then EXTRA_ANIME, then the
    SAMSUNG_IN channels (Indian, "india": 1, with their own "band" and "lang")."""
    path = os.path.join(B.DATA, "index.m3u")
    if not os.path.exists(path):
        return []
    out = []
    for e, i, d, why in Abroad(B).abroad():
        if d == "anime":
            e["anime_band"] = why
        if d in ("main", "anime"):
            out.append(e)
    return out + extra_anime(B.DATA) + extra_samsung_in(B.DATA)


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


# More of i.mjh.nz's public guide files, by country, for channels iptv-org's guides.json does not link to one: a
# channel is matched by its exact name (spaces, punctuation and a trailing "HD" aside) in its own country's files,
# in this order, and only where one file names one channel so (owner's choice "A", 2026-11-10).
NAMED_GUIDES = {
    "US": ("SamsungTVPlus/us", "Plex/us", "PlutoTV/us", "Roku/all", "PBS/all"),
    "UK": ("SamsungTVPlus/gb", "Plex/gb", "PlutoTV/gb"),
    "CA": ("SamsungTVPlus/ca", "Plex/ca", "PlutoTV/ca"),
    "AU": ("Plex/au", "au/Sydney/epg"), "NZ": ("Plex/nz", "nz/epg"), "ZA": ("DStv/za",),
    "DE": ("SamsungTVPlus/de", "PlutoTV/de"), "AT": ("SamsungTVPlus/at",), "CH": ("SamsungTVPlus/ch",),
    "FR": ("SamsungTVPlus/fr", "Plex/fr", "PlutoTV/fr"), "ES": ("SamsungTVPlus/es", "Plex/es", "PlutoTV/es"),
    "IT": ("SamsungTVPlus/it", "PlutoTV/it"), "KR": ("SamsungTVPlus/kr",), "MX": ("Plex/mx", "PlutoTV/mx"),
    "BR": ("PlutoTV/br",), "AR": ("PlutoTV/ar",), "DK": ("PlutoTV/dk",), "SE": ("PlutoTV/se",),
    "NO": ("PlutoTV/no",),
}


def name_key(name):
    """"¡Hola! Play" and "HOLA! Play" alike; a trailing "HD" does not count."""
    k = re.sub(r"[\W_]+", "", name.lower().replace("&", "and").replace("+", "plus"))
    return k[:-2] if k.endswith("hd") and len(k) > 5 else k


def guide_names(file):
    """{name key: {channel id}} for the channels with programmes in one i.mjh.nz guide file; {} without it."""
    import gzip
    import html
    import urllib.request
    try:
        req = urllib.request.Request(GUIDE_BASE + file + ".xml.gz", headers={"User-Agent": "LiveTVGuide-builder"})
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read()
        if len(body) > MAX_GUIDE_BYTES:
            return {}
        xml = gzip.decompress(body).decode("utf-8", "replace")
    except Exception as e:
        print(f"  guide {file}: {e}")
        return {}
    listed = Counter(html.unescape(m) for m in re.findall(r'<programme[^>]*?channel="([^"]+)"', xml))
    out = defaultdict(set)
    for m in re.finditer(r'<channel id="([^"]+)"[^>]*>(.*?)</channel>', xml, re.S):
        cid = html.unescape(m.group(1))
        if listed[cid] < 2:
            continue
        for n in re.findall(r"<display-name[^>]*>(.*?)</display-name>", m.group(2), re.S):
            k = name_key(html.unescape(n))
            if len(k) >= 3:
                out[k].add(cid)
    return out


def named_guides(channels):
    """{id(channel): (file, id there)} for channels without listings that NAMED_GUIDES' files name exactly."""
    files = {}
    out = {}
    for c in channels:
        if "gi" in c or c["cc"] not in NAMED_GUIDES:
            continue
        k = name_key(c["_base"])
        for file in NAMED_GUIDES[c["cc"]]:
            if file not in files:
                files[file] = guide_names(file)
            ids = files[file].get(k, ())
            if len(ids) == 1:
                out[id(c)] = (file, next(iter(ids)))
                break
    return out


# Pluto TV channels iptv-org's playlist does not list (owner, 10 Nov 2026: "add those Pluto TV channels that are
# present with actual English audio ... in their respective genre and respective country list"): every channel of
# Pluto TV's US, UK and Canadian line-ups (i.mjh.nz's public data) whose stream's master playlist names English as
# its only audio language, checked at each build. Country: the first of these line-ups that has it. Genre: from
# Pluto TV's own category. Anime is left to the anime genres, Spanish ("En Español") out by its audio anyway.
PLUTO_REGIONS = (("us", "US"), ("gb", "UK"), ("ca", "CA"))


def pluto_band(group):
    g = (group or "").lower()
    if "anime" in g:
        return None
    for words, band in ((("movie",), "Movies"), (("news", "opinion"), "News"), (("sport",), "Sports"),
                        (("kids",), "Kids"), (("music",), "Music"),
                        (("history", "science", "nature", "animal", "documentar", "adventure"), "Infotainment")):
        if any(w in g for w in words):
            return band
    return "Entertainment"


def pluto_audio(cid):
    """The audio languages Pluto TV's master playlist names for a channel; None when it does not answer."""
    import urllib.request
    try:
        req = urllib.request.Request(f"https://jmp2.uk/plu-{cid}.m3u8", headers={"User-Agent": "LiveTVGuide-builder"})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read(200_000).decode("utf-8", "replace")
    except Exception:
        return None
    return sorted(set(re.findall(r'TYPE=AUDIO[^\n]*?LANGUAGE="([^"]+)"', text)))


def extra_pluto(data, known_ids):
    """World channel entries for Pluto TV's English channels the playlist lacks (known_ids: Pluto ids already in it)."""
    from concurrent.futures import ThreadPoolExecutor
    path = os.path.join(data, "plutotv_channels.json")
    if not os.path.exists(path):
        return []
    regions = json.load(open(path, encoding="utf-8"))["regions"]
    wanted = {}
    for region, cc in PLUTO_REGIONS:
        for cid, ch in regions.get(region, {}).get("channels", {}).items():
            band = pluto_band(ch.get("group"))
            if cid in known_ids or cid in wanted or not band or not ch.get("name"):
                continue
            wanted[cid] = {"name": ch["name"].strip(), "cat": band, "lang": "English", "cc": cc, "id": "",
                           "url": f"https://jmp2.uk/plu-{cid}.m3u8", "logo": ch.get("logo") or "", "_q": 0,
                           "_extra": 1}
    with ThreadPoolExecutor(max_workers=6) as pool:
        audio = dict(zip(wanted, pool.map(pluto_audio, wanted)))
    out = []
    for cid, c in wanted.items():
        if audio[cid] == ["en"]:
            if not c["logo"]:
                del c["logo"]
            out.append(c)
    print(f"Pluto TV channels the playlist lacks: {len(wanted)}; English audio: {len(out)}; "
          f"other or none: {Counter(tuple(a) if a is not None else 'no answer' for a in audio.values()).most_common(6)}")
    return out


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
    # Pluto TV's English channels iptv-org does not list (a licensed free platform, like its others here); the
    # playlist's own come first, and the main list's Pluto TV channels (EXTRA_ANIME, STREAM_SWAPS) are left alone.
    known = set(re.findall(r"jmp2\.uk/plu-([0-9a-f]+)", open(os.path.join(B.DATA, "index.m3u"), encoding="utf-8").read()))
    known |= {cid for _, cid in EXTRA_ANIME}
    known |= {m.group(1) for u in STREAM_SWAPS.values() for m in [re.search(r"plu-([0-9a-f]+)", u)] if m}
    for c in extra_pluto(B.DATA, known):
        if c["url"] not in seen_urls:
            seen_urls.add(c["url"])
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
            c["_base"] = c["name"]   # the playlist's own name, for finding listings by name (dropped below)
            numbered.append(c)
    # Names are the guide's identity for a channel (favourites, recent, stream tests): one that the Indian list or
    # another country already uses gets the country added. The playlist's channels are named first, so a channel
    # added from Pluto TV's line-ups (extra_pluto) takes the country rather than renaming one already there.
    for extra in (False, True):
        for c in numbered:
            if bool(c.get("_extra")) != extra:
                continue
            if c["name"].lower() in names_in_use:
                c["name"] = f"{c['name']} ({country_names.get(c['cc'], c['cc'])})"
            k = 2
            base = c["name"]
            while c["name"].lower() in names_in_use:
                c["name"] = f"{base} {k}"
                k += 1
            names_in_use.add(c["name"].lower())
            c.pop("_extra", None)

    # Programme listings: the i.mjh.nz guide file that lists each channel ("gi", an index into "guides") and the
    # channel's id there ("xt"). The guide downloads only the files its ticked countries' channels need.
    guides, guide_urls = mjh_guides(B.DATA), []

    def use(c, url, xt):
        if url not in guide_urls:
            guide_urls.append(url)
        c["gi"] = guide_urls.index(url)
        c["xt"] = xt

    pluto_ids = pluto_numbers(B.DATA)
    for c in numbered:
        g = pick_guide(guides, pluto_ids, c)
        if g:
            use(c, GUIDE_BASE + g[0] + ".xml.gz", g[1])
    from_mjh = sum(1 for c in numbered if "gi" in c)
    # A: more i.mjh.nz files, by the channel's exact name in its own country.
    named = named_guides(numbered)
    for c in numbered:
        if id(c) in named:
            file, xt = named[id(c)]
            use(c, GUIDE_BASE + file + ".xml.gz", xt)
    # B: the rest that iptv-org's grabber can collect from a working site, from this repository's daily listings
    # (tools/collect_listings.py, .github/workflows/listings.yml): one file per country, the channel's key there.
    collected = 0
    try:
        sites_md = os.path.join(B.DATA, "epg_sites.md")
        fetch_to(SITES_MD_URL, sites_md)
        sources = L.Sources(B.DATA)
    except Exception as e:
        print(f"  daily listings left out: {e}")
        sources = None
    for c in numbered:
        if sources and "gi" not in c and sources.candidates(c):
            use(c, L.LISTINGS_BASE + c["cc"].lower() + ".xml.gz", L.key(sources, c))
            collected += 1
    for c in numbered:
        del c["_base"]
    counts = Counter(c["cc"] for c in numbered)
    region = regions_of(B.DATA, counts)
    countries = sorted(({"cc": cc, "name": country_names.get(cc, cc), "region": region[cc], "n": n}
                        for cc, n in counts.items()), key=lambda c: c["name"])
    payload = {"generated": datetime.date.today().isoformat(), "version": int(datetime.datetime.now().timestamp()),
               "checked": False, "source": INDEX_URL,
               "bands": [b + WORLD_SUFFIX for b in B.BAND_DISPLAY if any(c["cat"] == b + WORLD_SUFFIX for c in numbered)],
               "regions": [name for name, _ in REGIONS if any(c["region"] == name for c in countries)],
               "countries": countries, "guides": guide_urls,
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
    print(f"programme listings: {sum(1 for c in numbered if 'gi' in c)} of {len(numbered)} channels: "
          f"{from_mjh} from iptv-org's i.mjh.nz links, {len(named)} more by name there, "
          f"{collected} from the daily listings; {len(guide_urls)} guide files")
    print(f"wrote {out_path} ({os.path.getsize(out_path) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
