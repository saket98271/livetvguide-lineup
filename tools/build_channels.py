"""Build the guide's channel line-up from iptv-org's public India playlist.

Usage:  python -I tools/build_channels.py            (downloads fresh sources into data/, tests every stream)
        python -I tools/build_channels.py --offline  (reuses data/)
        add --no-check to skip the stream test (it takes a few minutes)
        add --out <file> to write the line-up elsewhere (the weekly GitHub build writes lineup/channels.json)

The weekly GitHub build runs with --no-check: GitHub's machines are outside India, where India-only streams would
look dead. Its line-up says "checked": false and the guide tests the streams on the TV itself (StreamCheck.java).
A source that cannot be downloaded (JioTV's list may refuse a foreign machine) is read from its last good copy.

The stream test asks each kept stream for its first bytes once, with a plain browser identity, from this
computer; a stream that does not answer with a playlist or video is left out as "offline when checked", unless it
was in the last build and answered the check before (data/stream_failures.json): one miss is forgiven.

Writes app/src/main/assets/channels.json (the line-up the app ships with) and data/excluded.csv
(every stream left out, with the reason).

What is left out, and why:
  - every stream on a server that re-broadcasts pay channels (a bare IP or other host carrying Colors, Sony,
    Star ...): that server is a pirate re-broadcaster, so nothing on it is trusted. A bare-IP server that
    carries only free-to-air channels (the government's E-Vidya education feeds, for one) stays.
  - a bare-IP copy of a channel that also has a stream on a named host: the named one is the official feed.
  - known anonymous re-hosts (raw.githubusercontent.com, workers.dev).
  - pay channels (Colors, Sony, Star, Zee entertainment, Nick, MTV, Discovery ...) unless the stream is
    on a licensed free-streaming platform (Amagi, Samsung TV Plus, YuppTV ...): a pay channel streaming
    free from anywhere else is an unauthorised re-broadcast. These are reachable legitimately through
    JioTV+, JioHotstar, Sony LIV and ZEE5.
  - streams that only play when the request pretends to come from the broadcaster's own web page
    (#EXTVLCOPT http-referrer): that is hotlink protection, and faking it gets around access control.
  - foreign OTT redistributors of Indian pay channels (Ayna).
  - channels India does not permit: Peace TV (refused permission to telecast in India, I&B Ministry 2016; the
    owner's choice, 2026-11-09).
"""
import csv
import datetime
import gzip
import json
import os
import re
import sys
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

BROWSER_UA = "Mozilla/5.0 (Linux; Android 9; TV) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def stream_alive(url, user_agent):
    """True when the stream answers with an HLS playlist or media bytes within a few seconds."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": user_agent or BROWSER_UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            if r.status // 100 != 2:
                return False
            head = r.read(2048)
            ctype = (r.headers.get("Content-Type") or "").lower()
            return (head.lstrip().startswith(b"#EXTM3U") or "mpegurl" in ctype or "video" in ctype
                    or head[:1] == b"\x47" or "octet-stream" in ctype)
    except Exception:
        return False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "app", "src", "main", "assets", "channels.json")
if "--out" in sys.argv:
    OUT = os.path.abspath(sys.argv[sys.argv.index("--out") + 1])

SOURCES = {
    "in.m3u": "https://iptv-org.github.io/iptv/countries/in.m3u",
    "channels.json": "https://iptv-org.github.io/api/channels.json",
    "feeds.json": "https://iptv-org.github.io/api/feeds.json",
    "languages.json": "https://iptv-org.github.io/api/languages.json",
    # JioTV's channel list: the app reads JioTV's public guide (jiotvapi.cdn.jio.com, a plain request with no
    # sign-in) for "Now / Next", matched here by name. JioTV's own public list comes first (current: it has the
    # Star, Zee and Sony channels); iptv-org/epg's older copy fills in names the live list lacks.
    "jiotv_channels.json": "https://jiotvapi.cdn.jio.com/apis/v1.3/getMobileChannelList/get/?os=android&devicetype=phone",
    "jiotv.com.channels.xml": "https://raw.githubusercontent.com/iptv-org/epg/master/sites/jiotv.com/jiotv.com.channels.xml",
    # Samsung TV Plus India's guide (public XMLTV from i.mjh.nz): listings for free channels JioTV lacks.
    "samsungtvplus_in.xml.gz": "https://i.mjh.nz/SamsungTVPlus/in.xml.gz",
    # Logos for the channels the guide opens in their official apps (tools/linked_channels.json).
    "logos.json": "https://iptv-org.github.io/api/logos.json",
    # iptv-org's whole playlist: channels from abroad in an Indian language join this list (build_world.py's
    # rules); every other one goes to the World list (build_world.py), with countries and regions for its checklist.
    "index.m3u": "https://iptv-org.github.io/iptv/index.m3u",
    "countries.json": "https://iptv-org.github.io/api/countries.json",
    "regions.json": "https://iptv-org.github.io/api/regions.json",
    # Pluto TV's channel numbers in each country (i.mjh.nz's public data): World channels follow that order.
    "plutotv_channels.json": "https://i.mjh.nz/PlutoTV/.channels.json",
    # Which public guides list each channel (iptv-org): World channels get their listings from i.mjh.nz's files.
    "guides.json": "https://iptv-org.github.io/api/guides.json",
}
LINKED = os.path.join(ROOT, "tools", "linked_channels.json")
AIRTEL = os.path.join(ROOT, "tools", "airtel_numbers.tsv")
# Channels renamed since the Airtel list was published: our name's key -> the key Airtel lists it under.
AIRTEL_ALIASES = {"zeebharat": "zeehindustan", "republicbharat": "rbharat", "zeemadhyapradeshchhattisgarh": "zeempcg",
                  "zeeuttarpradeshuttarakhand": "zeeupuk", "zeeuputtarakhand": "zeeupuk",
                  "zeerajasthan": "zeerajasthannews", "zeeanmolcinema": "zeeanmolcinema",
                  "zeemarathihd": "zeemarathi", "zeepunjabharyanahimachal": "zeepunjabharyanahimachalpradesh",
                  "zeedelhincrharyana": "zeedelhincrharyana", "asianetsuvarnanews": "asianetsuvarnanews",
                  "news18uttarpradeshuttarakhand": "news18uttarpradeshuttarakhand",
                  "news18madhyapradeshchhattisgarh": "news18mpchhattisgarh",
                  # JioTV+ names for channels Airtel lists under older or shorter names.
                  "sonyentertainmenttelevision": "sony", "sonysportsten1": "sonyten1", "sonysportsten2": "sonyten2",
                  "sonysportsten3hindi": "sonyten3", "sonysportsten5": "sonysix", "discovery": "discoverychannel"}


def load_airtel():
    """{guide key: lowest Airtel DTH number} from tools/airtel_numbers.tsv (see fetch_airtel_numbers.py)."""
    numbers = {}
    if not os.path.exists(AIRTEL):
        return numbers
    for line in open(AIRTEL, encoding="utf-8"):
        if line.startswith("#") or "\t" not in line:
            continue
        name, number = line.rstrip("\n").rsplit("\t", 1)
        key = guide_key(name)
        numbers[key] = min(numbers.get(key, 10_000), int(number))
    return numbers


def airtel_number(name, numbers):
    key = guide_key(name.replace("/", " "))
    return numbers.get(key) or numbers.get(AIRTEL_ALIASES.get(key, ""))


def load_linked(channels_by_id, free_keys, jio_ids):
    """Channels opened in their official app (ZEE5, JioTV+, JioHotstar), from tools/linked_channels.json.

    A channel listed by several apps becomes one channel with a "links" list in the order the apps are listed
    in "apps"; the guide uses the first app installed on the device (JioTV+ on the Fire TV, the JioHotstar page
    on the projector). Apps marked "optional" are only offered where installed, so a channel only they carry is
    left out on devices without them. A channel that already has a free stream in the line-up keeps the free one:
    it plays in the guide itself. Logos come from iptv-org (matched by name among Indian channels).
    """
    spec = json.load(open(LINKED, encoding="utf-8"))
    by_key = {}
    for c in channels_by_id.values():
        if c.get("country") != "IN":
            continue
        for n in [c.get("name", "")] + (c.get("alt_names") or []):
            by_key.setdefault(guide_key(n), c["id"])
    logo_of = {}
    for l in json.load(open(os.path.join(DATA, "logos.json"), encoding="utf-8")):
        if l.get("channel") and not l.get("feed"):
            logo_of.setdefault(l["channel"], l.get("url"))
    app_rank = {k: i for i, k in enumerate(spec["apps"])}
    rows = sorted(spec["channels"], key=lambda r: app_rank[r[0]])
    out, skipped, by_channel, alt = [], [], {}, {}
    for app_key, name, path, lang, band, *app_name in rows:
        app = spec["apps"][app_key]
        key = guide_key(name)
        packages = app["package"] if isinstance(app["package"], list) else [app["package"]]
        links = [{"u": app["base"] + path, "pkg": p, "app": app["name"]} for p in packages]
        for link in links:
            if app.get("live"):
                link["live"] = 1
            if app.get("player"):
                link["rw"] = app["player"]
            if app.get("optional"):
                link["opt"] = 1
            if app_name:
                # The app's own name for the channel ("Star Plus" for StarPlus): the guide searches the app for it
                # when it looks the channel's id up again (JioTvPlusLookup.refreshIfDue).
                link["t"] = app_name[0]
        if key in free_keys:
            # The free stream plays in the guide; the app is its fallback when the TV finds the stream dead.
            skipped.append(name)
            alt.setdefault(key, []).extend(links)
            continue
        # Grouped by exact name, not guide_key: "Zee TV" and "Zee TV HD" stay two channels.
        if name.lower() in by_channel:
            # Another app already listed this channel: this one is a fallback (name, genre, language stay).
            by_channel[name.lower()]["links"].extend(links)
            continue
        c = {"name": name, "cat": band, "lang": lang, "links": links}
        by_channel[name.lower()] = c
        iptv_id = by_key.get(key)
        if iptv_id:
            c["id"] = iptv_id
            if logo_of.get(iptv_id):
                c["logo"] = logo_of[iptv_id]
        jio = jio_id(name, jio_ids, lang)
        if jio:
            c["jio"] = jio
        out.append(c)
    return out, skipped, alt


def guide_key(name):
    """Channel name reduced for matching across lists: 'Aaj Tak HD' and 'AAJ TAK' both give 'aajtak'."""
    # "Zee Café" and "&Privé" match "Zee Cafe" and "&Prive".
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = n.lower().replace("&", " and ").replace("/", " ")
    # Only a bracketed region is dropped: "NDTV India" (Hindi) and "NDTV" (English) are different channels.
    n = re.sub(r"\((india|in)\)", "", n)
    # State names as the lists abbreviate them: "Zee Madhya Pradesh Chhattisgarh" = "Zee MPCG" = "Zee MP Chhattisgarh".
    for full, short in (("madhya pradesh", "mp"), ("chhattisgarh", "cg"), ("chattisgarh", "cg"), ("uttar pradesh", "up"),
                        ("uttarakhand", "uk"), ("chg", "cg")):
        n = re.sub(r"\b" + full + r"\b", short, n)
    n = re.sub(r"\bhd\b|\bsd\b", "", n)
    without_tv = re.sub(r"\btv\b|[^a-z0-9]", "", n)
    # "Zee TV" and "Zee" are the same channel name, but "&TV" must not shrink to nothing.
    return without_tv if len(without_tv) >= 3 else re.sub(r"[^a-z0-9]", "", n)


# Our name's key -> the key JioTV's list uses, where the two name a channel differently.
JIO_ALIASES = {"sonyentertainmenttelevision": "set", "sonysportsten1": "sonyten1", "sonysportsten2": "sonyten2",
               "sonysportsten3hindi": "sonyten3hindi", "sonysportsten5": "sonyten5", "sonyyay": "sonyyayhindi",
               "starsuvarna": "suvarna", "starsuvarnaplus": "suvarnaplus", "ndtvgoodtimes": "goodtimes",
               "ddchandana": "dd9chandanakannada", "nepal1": "nepalone", "mhonedilse": "mh1dilse",
               "sarvdharmsangam": "sarvdharamsangam", "santvanichannel": "santvani", "tamilan": "tamilantelevision",
               "nickjr": "nickjunior", "sonicnickelodeon": "sonic", "discoverychannel": "discovery",
               # Madhya Pradesh / Chhattisgarh and other Hindi news, as JioTV writes them.
               "newsstatempandcg": "newsstatempcg", "news24mpandcg": "news24mpcg", "newsstateupanduk": "newsstateukup",
               "sadhnaplusnews": "sadhnanewsplus", "sudarshannews": "sudarshan", "apn": "apnnews",
               "samacharplus24x7": "samacharplus", "sansad2": "sansadrajyasabha", "gyandarshan": "ddgyandarshan",
               "colorscineplexsuperhits": "colorscineplexsuperhit", "shemaroofilmigaane": "shemaroofilmygaane",
               "mhoneshraddha": "mh1shraddha", "odisha": "otv"}
# JioTV ids by our name where matching picks the wrong one: iptv-org/epg's older copy of JioTV's list still calls
# 1263 "Total Tv Haryana", an id JioTV has since given to BRK News; Total TV Haryana's programmes ("Total Haryana",
# "Haryana Prime") are on "Total TV".
JIO_FIXED = {"Total TV Haryana": 855}
# Our name's key -> the key in the Samsung TV Plus guide.
XMLTV_ALIASES = {"zcomedynation": "zeecomedynation", "zdilse": "zeedilse", "zhorrornights": "zeehorrornights"}


def load_jio_ids():
    """{(guide_key, has_hd or None): JioTV channel id} from JioTV's public list, then iptv-org/epg's copy."""
    names = []
    live = os.path.join(DATA, "jiotv_channels.json")
    if os.path.exists(live):
        rows = json.load(open(live, encoding="utf-8")).get("result") or []
        # The plain entry before its "... STB" / "... EPG" twins (same guide, different player).
        rows.sort(key=lambda r: bool(re.search(r"\b(stb|epg)\s*$", r.get("channel_name", ""), re.I)))
        names += [(int(r["channel_id"]), r.get("channel_name", "")) for r in rows if r.get("channel_id")]
    path = os.path.join(DATA, "jiotv.com.channels.xml")
    names += [(int(i), n) for i, n in
              re.findall(r'site_id="(\d+)"[^>]*>([^<]+)</channel>', open(path, encoding="utf-8").read())]
    out = {}
    for site_id, name in names:
        name = re.sub(r"\s+(stb|epg)\s*$", "", name.strip(), flags=re.I)
        key, hd = guide_key(name), bool(re.search(r"\bhd\b", name, re.I))
        # Prefer the SD feed's id for an SD channel and the HD one for HD; the first seen otherwise.
        out.setdefault((key, hd), site_id)
        out.setdefault((key, None), site_id)
    return out


# Shorter names for the guide's narrow name column ("sn" in channels.json); the full name stays the channel's
# identity (favourites, recent, matching). Patterns first, then single channels.
SHORT_PATTERNS = [
    (r"\s*\((india|in)\)", ""),
    (r"madhya pradesh\s*[/&]?\s*chhattisgarh|mp\s*&\s*(chhattisgarh|chg)", "MP CG"),
    (r"uttar pradesh\s*[/&]?\s*uttarakhand", "UP UK"),
    (r"punjab\s*[/ ]\s*haryana\s*[/ ]\s*himachal", "Punjab HR HP"),
    (r"^sony sports ten", "Sony Ten"),
    (r"^colors cineplex (?=\w)", "Cineplex "),
    (r"entertainment television", "TV"),
    (r"national geographic", "Nat Geo"),
]
SHORT_NAMES = {"Qello Concerts by Stingray": "Qello Concerts", "The Pet Collective India": "The Pet Collective",
               "Disney International HD": "Disney Intl HD", "Shree Naga Sai Temple Darshan": "Naga Sai Darshan",
               "Mahavir Mandir Patna Darshan": "Mahavir Mandir Darshan",
               "Puthiya Thalaimurai Express": "Puthiya Thalaimurai Exp.", "News State MP & CHG": "News State MP CG",
               "Investigation Discovery HD": "Inv. Discovery HD", "Investigation Discovery": "Inv. Discovery"}


def short_name(name):
    """A shorter display name for a long channel name, or None to keep it as it is."""
    if name in SHORT_NAMES:
        return SHORT_NAMES[name]
    if len(name) <= 20:
        return None
    s = name
    for pattern, repl in SHORT_PATTERNS:
        s = re.sub(pattern, repl, s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) < len(name) else None


SAMSUNG_GUIDE = "https://i.mjh.nz/SamsungTVPlus/in.xml.gz"


def xmltv_key(name):
    """guide_key without a trailing "India": "KCM India" is "KCM" in the Samsung TV Plus guide."""
    k = guide_key(name)
    k = k[:-5] if k.endswith("india") and len(k) > 8 else k
    return XMLTV_ALIASES.get(k, k)


def load_samsung_ids():
    """{xmltv_key(name): channel id} from the Samsung TV Plus India XMLTV guide (data/samsungtvplus_in.xml.gz)."""
    path = os.path.join(DATA, "samsungtvplus_in.xml.gz")
    if not os.path.exists(path):
        return {}
    text = gzip.open(path).read().decode("utf-8", "replace")
    out = {}
    for cid, name in re.findall(r'<channel id="([^"]+)">\s*<display-name>([^<]+)</display-name>', text):
        out.setdefault(xmltv_key(name), cid)
    return out


def jio_id(name, jio_ids, lang=""):
    """JioTV id for a channel name: the same SD/HD feed if JioTV has it, else the other one (same programmes).

    JioTV names many channels by language ("Pogo Hindi", "Discovery Kids Hindi", "Animal Planet HD English"), so
    the name with the channel's language is tried before the bare name.
    """
    key, hd = guide_key(name), bool(re.search(r"\bhd\b", name, re.I))
    suffix = re.sub(r"[^a-z]", "", lang.lower())
    if name in JIO_FIXED:
        return JIO_FIXED[name]
    for base in (key, JIO_ALIASES.get(key)):
        for k in ((base + suffix, base) if base and suffix else (base,)):
            if k and (jio_ids.get((k, hd)) or jio_ids.get((k, None))):
                return jio_ids.get((k, hd)) or jio_ids.get((k, None))
    return None

ANONYMOUS_HOSTS = {"githubusercontent.com", "workers.dev"}
UNLICENSED_REDISTRIBUTORS = {"aynaott.com", "aynascope.net"}
# Free ad-supported (FAST) and licensed streaming platforms: a pay brand here is the broadcaster's own free feed.
LICENSED_PLATFORMS = {"amagi.tv", "jmp2.uk", "stingray.com", "yuppcdn.net", "wiseplayout.com", "shemaroo.com"}
PAY_BRANDS = re.compile(
    r"^(asianet( hd| movies| plus)?$|colors\b|sony\b|star (plus|bharat|gold|movies|sports|utsav|world|jalsha|pravah|maa|vijay|suvarna)|zee (tv|cinema|anmol|action|classic|bollywood|zest|cafe|marathi|bangla|tamil|telugu|kannada|keralam|punjabi|yuva|talkies|biskope|thirai|picchar)|&\s?(tv|pictures|flix|prive|explorhd|xplor)|and tv|nick|nickelodeon|sonic\b|mtv|vh1|animal planet|discovery|nat ?geo|national geographic|cartoon network|pogo|disney|hungama|sun (tv|music|news|life)|ktv\b|gemini|udaya|surya|hbo|movies now|mn\+|romedy|comedy central|history tv18|epic\b|tlc\b|investigation discovery|ten \d|dsport|eurosport|sports18|colors cineplex|cinema tv)",
    re.I,
)

CATEGORY_MAP = {
    "entertainment": "Entertainment", "general": "Entertainment", "undefined": "Entertainment",
    "lifestyle": "Entertainment", "comedy": "Entertainment", "series": "Entertainment", "family": "Entertainment",
    "culture": "Entertainment", "legislative": "News", "movies": "Movies", "classic": "Movies",
    "music": "Music", "kids": "Kids", "animation": "Kids", "sports": "Sports", "outdoor": "Sports",
    "documentary": "Infotainment", "education": "Infotainment", "science": "Infotainment",
    "travel": "Infotainment", "cooking": "Infotainment", "auto": "Infotainment", "weather": "News",
    # Shopping channels (none in the Indian list so far) are Entertainment: 10001 and up are the World channels'.
    "news": "News", "business": "Business News", "religious": "Devotional", "shop": "Entertainment",
}
BAND_ORDER = ["Entertainment", "Movies", "Music", "Kids", "Sports", "Infotainment", "News", "Business News",
              "Devotional"]
# The genre list's order on screen (the owner's), which differs from the number order of BAND_START.
BAND_DISPLAY = ["Entertainment", "Movies", "News", "Music", "Sports", "Kids", "Anime English", "Anime Japanese",
                "Anime other languages", "Infotainment", "Devotional", "Business News"]
# Anime genres (the owner's, 2026-11-09): anime channels from abroad by language (build_world.ANIME), numbered in
# the unused part of Kids' thousand. Doraemon, Shin-chan and the Indian cartoon channels stay in Kids.
ANIME_START = {"Anime English": 4501, "Anime Japanese": 4601, "Anime other languages": 4701}
LANGUAGE_FIRST_BANDS = {"Entertainment", "Movies", "News"}
# Every channel was checked on 2026-11-03 (the owner asked for it): channels JioTV carries against what their
# programmes are (JioTV's guide gives each programme a category, genre and language), the Samsung TV Plus channels
# by their programme categories, the rest by name, iptv-org's tags and the network. A change is made only where a web
# source confirms it too (the owner's rule); where the sources disagree the channel keeps its genre.
# Channels whose language iptv-org records wrongly.
LANG_FIXES = {"ET Now Swadesh": "Hindi", "Saam TV": "Marathi", "Kaumudy TV": "Malayalam",
              "Bharat Express": "Hindi", "Zee Punjab Haryana Himachal": "Hindi", "News18 Assam North-East": "Assamese",
              "Argus News": "Odia", "Rongeen TV": "Bengali", "Balle Balle": "Punjabi", "Balle Balle HD": "Punjabi",
              "7X Music": "Punjabi", "Mh 1 Prime": "Punjabi", "Anand Wari TV": "Marathi", "NDTV Good Times": "English",
              # A Gujarati devotional channel (the owner checked; JioTV's listings show Swaminarayan discourses).
              "Shiksha TV": "Gujarati",
              # An Islamic channel from Hyderabad in Urdu, Hindi and Telugu; its programmes are Urdu.
              "Mercy TV (India)": "Urdu",
              # Became a Hindi news channel (N1, rebroadcasting APN News).
              "Nepal 1": "Hindi",
              # Channels the first cloud list added, checked on the web and approved by the owner (2026-11-04).
              "DD Urdu": "Urdu", "DD Kashir": "Urdu", "Tehzeeb TV": "Urdu", "Gulistan News": "Urdu",
              "DD Assam": "Assamese", "DD Manipur": "Manipuri", "DD Mizoram": "Mizo", "Fateh TV": "Punjabi",
              "FashionTV (India)": "English",
              # From the owner's own research (2026-11-04).
              "Apna Punjab TV": "Punjabi",
              # Doordarshan's Shillong channel: Khasi and Garo programmes, not one of the English channels.
              "DD Meghalaya": "Khasi",
              # Anime genres (2026-11-09): iptv-org lists Animax Asia as Japanese first, then English; the Spanish
              # Yu-Gi-Oh! feed is listed as English.
              "Animax Asia India": "Japanese", "Yu-Gi-Oh! en espanol": "Spanish"}
# Genres iptv-org gets wrong or leaves out (a channel without one used to land in Entertainment; the owner found
# Santvani, Sharnam, Total TV Haryana ... among the serials).
CAT_FIXES = {
    "Network 10": "News", "Jan TV": "News", "Raftaar Media": "News", "News 11": "News", "Taaza TV": "News",
    "Total TV Haryana": "News", "Saam TV": "News", "R Plus": "News", "Tamilan TV": "News", "Odisha TV": "News",
    "Nandighosha TV": "News", "TV BRICS English": "News", "DD India": "News", "Nepal 1": "News",
    "Shubh TV": "Devotional", "Santvani Channel": "Devotional", "Sarv Dharm Sangam": "Devotional",
    "Sharnam TV": "Devotional", "Sai TV": "Devotional", "Prarthana Life": "Devotional", "Astroscience": "Devotional",
    "Shiksha TV": "Devotional",
    # Murasu TV: Kalaignar's second music channel (older songs), per the trade press.
    "Kappa TV": "Music", "Andy Haryana": "Music", "Ramdhenu": "Music", "Kalaignar Murasu": "Music",
    "Investigation Discovery": "Infotainment", "Investigation Discovery HD": "Infotainment",
    "NDTV Good Times": "Infotainment", "CVR Health": "Infotainment", "Ayush TV": "Infotainment",
    "INFAST": "Infotainment", "WeatherSpy (India)": "Infotainment",
    "Disney International HD": "Kids",
    # Shemaroo MarathiBana is a 24-hour Marathi movie channel; Kairali We is becoming "Kairali Movies".
    "Shemaroo Marathi Bana": "Movies", "PTC Punjabi Gold": "Movies", "Kairali We": "Movies",
    # Hybrids (serials and films) since August 2025: Zee Power replaced Zee Picchar, Zee BanglaSonar Zee Bangla Cinema.
    "Zee Bangla Sonar": "Entertainment", "Zee Power HD": "Entertainment",
    "BT TV": "Business News",
    # Channels the first cloud list added (web-checked, owner-approved 2026-11-04).
    "TV 100 (India)": "News", "Jaya Plus": "News", "Win TV": "News", "MBC TV": "News",
    "ETV Life": "Devotional", "Disha TV": "Devotional", "Prarthana Bhawan TV": "Devotional",
    "MH One": "Music", "DD Mizoram": "Entertainment",
    # From the owner's own research (2026-11-04).
    "Calvary TV": "Devotional", "Shekinah TV": "Devotional", "Peppers TV": "Entertainment",
    # From abroad (2026-11-09): Gurdwara Dukh Nivaran Sahib's live Gurbani kirtan (SikhNet lists it).
    "Dukh Nivaran": "Devotional",
    # Sony's anime channel (mostly Japanese with English): the Anime Japanese genre, not Kids.
    "Animax Asia India": "Anime Japanese",
}
# A thousand numbers per genre (the owner's choice: Movies outgrew 401-500 and would have renumbered every genre
# after it).
BAND_START = {"Entertainment": 1001, "Movies": 2001, "Music": 3001, "Kids": 4001, "Sports": 5001,
              "Infotainment": 6001, "News": 7001, "Business News": 8001, "Devotional": 9001}
# The World channels (build_world.py) are numbered 10001 and up: Indian channels must stay below.
WORLD_FIRST_NUMBER = 10001
LANG_ORDER = ["Hindi", "English", "Marathi", "Bengali", "Tamil", "Telugu", "Kannada", "Malayalam", "Gujarati",
              "Punjabi", "Odia", "Assamese", "Bhojpuri", "Urdu"]
LANG_ALIASES = {"Panjabi": "Punjabi", "Oriya (macrolanguage)": "Odia", "Oriya": "Odia",
                "Konkani (macrolanguage)": "Konkani",
                # Some Pluto TV feeds carry the bare code "jap" (One Piece).
                "jap": "Japanese"}
# Channels serving Chhattisgarh. iptv-org lists them as Hindi (their main language), so they get a region tag
# the guide's language filter offers as "Chhattisgarhi".
# Hindi news channels for one state or region, by the name (News18 Rajasthan, Zee UP UK, ABP Ganga ...): in News
# they follow the national Hindi channels, Chhattisgarh's and English.
STATE_NEWS = re.compile(r"\b(rajasthan|up|uk|uttar pradesh|uttarakhand|bihar|jharkhand|punjab|haryana|himachal|hr|"
                        r"hp|delhi|ncr|jk|kashmir|ganga|gujarat|maharashtra|madhya pradesh|mp)\b", re.I)
CHHATTISGARH = re.compile(r"chhattisgarh|\bchg\b|\bcg\b|mpcg|ibc ?24|swaraj express smbc|raipur|bilaspur", re.I)


def fetch(offline):
    os.makedirs(DATA, exist_ok=True)
    for name, url in SOURCES.items():
        path = os.path.join(DATA, name)
        if offline and os.path.exists(path):
            continue
        print(f"downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "LiveTVGuide-builder"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
        except Exception as e:
            if not os.path.exists(path):
                raise
            print(f"  could not download ({e}); using the last good copy")
            continue
        with open(path, "wb") as f:
            f.write(body)


def domain(host):
    if re.fullmatch(r"[\d.]+", host or ""):
        return host
    parts = (host or "?").split(".")
    return ".".join(parts[-3:]) if parts[-2:] in (["co", "in"], ["gov", "in"]) else ".".join(parts[-2:])


def parse_m3u(path):
    entries, info, opts = [], None, {}
    for line in open(path, encoding="utf-8", errors="replace").read().splitlines():
        if line.startswith("#EXTINF"):
            info, opts = line, {}
        elif line.startswith("#EXTVLCOPT:") and info:
            key, _, val = line[len("#EXTVLCOPT:"):].partition("=")
            opts[key.strip()] = val.strip()
        elif line and not line.startswith("#") and info:
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', info))
            entries.append({"title": info.rsplit(",", 1)[-1].strip(), "attrs": attrs, "opts": opts, "url": line.strip()})
            info, opts = None, {}
    return entries


def main():
    fetch("--offline" in sys.argv)
    entries = parse_m3u(os.path.join(DATA, "in.m3u"))
    # Channels from abroad in an Indian language (Nepal's, Bangladesh's, Sri Lankan Tamil, the UK's and Canada's
    # Bengali, Punjabi, Malayalam ...), passed by build_world.py's rules, join this list; the guide holds them in
    # New until kept. (Run with -I, Python does not look in this folder by itself.)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import build_world
    abroad = [] if "--no-abroad" in sys.argv else build_world.indian_abroad(sys.modules[__name__])
    print(f"channels from abroad (Indian languages, anime): {len(abroad)}")
    for e in abroad:
        e["abroad"] = 1
    entries += abroad
    channels = {c["id"]: c for c in json.load(open(os.path.join(DATA, "channels.json"), encoding="utf-8"))}
    feeds = defaultdict(list)
    for f in json.load(open(os.path.join(DATA, "feeds.json"), encoding="utf-8")):
        feeds[f["channel"]].append(f)
    lang_names = {l["code"]: l["name"] for l in json.load(open(os.path.join(DATA, "languages.json"), encoding="utf-8"))}
    jio_ids = load_jio_ids()

    def clean_name(title):
        return re.sub(r"\s*\(\d{3,4}[pi]\)|\s*\[[^\]]+\]", "", title).strip()

    def is_ip(host):
        return bool(re.fullmatch(r"[\d.]+", host or ""))

    # Servers that re-broadcast any pay channel are pirate re-broadcasters: nothing on them is kept.
    pirate_hosts = {urlparse(e["url"]).hostname for e in entries
                    if PAY_BRANDS.search(clean_name(e["title"]))
                    and domain(urlparse(e["url"]).hostname) not in LICENSED_PLATFORMS}
    named_stream = {clean_name(e["title"]).lower() for e in entries if not is_ip(urlparse(e["url"]).hostname)}

    kept, excluded, seen_urls = [], [], set()
    for e in entries:
        title = e["title"]
        flags = re.findall(r"\[([^\]]+)\]", title)
        quality = (re.findall(r"\((\d{3,4}[pi])\)", title) or [""])[0]
        name = clean_name(title)
        host = urlparse(e["url"]).hostname or ""
        dom = domain(host)

        reason = None
        if build_world.NOT_PERMITTED.search(name):
            # The owner's choice (2026-11-09): Peace TV Bangla and Urdu came through iptv-org's India playlist.
            reason = "not permitted in India (Peace TV: refused permission to telecast, I&B Ministry 2016)"
        elif "http-referrer" in e["opts"]:
            reason = "needs a faked referring page (hotlink protection)"
        elif host in pirate_hosts and is_ip(host):
            reason = "server re-broadcasts pay channels (bare IP)"
        elif is_ip(host) and name.lower() in named_stream:
            reason = "bare-IP copy of a channel with an official stream"
        elif dom in ANONYMOUS_HOSTS:
            reason = f"anonymous re-host ({dom})"
        elif dom in UNLICENSED_REDISTRIBUTORS:
            reason = f"foreign redistributor of Indian pay channels ({dom})"
        elif PAY_BRANDS.search(name) and dom not in LICENSED_PLATFORMS:
            reason = f"pay channel outside a licensed platform ({dom})"
        elif e["url"] in seen_urls:
            reason = "duplicate stream"
        if reason:
            excluded.append((title, dom, reason))
            continue
        seen_urls.add(e["url"])

        tvg = e["attrs"].get("tvg-id", "")
        cid, _, feed_id = tvg.partition("@")
        ch = channels.get(cid, {})
        cats = [c.lower() for c in (ch.get("categories") or [])] or \
               [g.strip().lower() for g in e["attrs"].get("group-title", "undefined").split(";")]
        band = e.get("anime_band") or next((CATEGORY_MAP[c] for c in cats if c in CATEGORY_MAP), "Entertainment")
        feed = next((f for f in feeds.get(cid, []) if f["id"] == feed_id), None) or \
            next((f for f in feeds.get(cid, []) if f.get("is_main")), None)
        langs = [LANG_ALIASES.get(n, n) for n in (lang_names.get(code, code) for code in (feed or {}).get("languages", []))]
        lang = next((l for l in LANG_ORDER if l in langs), langs[0] if langs else "Other")

        c = {"name": name, "cat": band, "lang": lang, "url": e["url"], "id": tvg}
        if quality:
            c["q"] = quality
        if e["attrs"].get("tvg-logo"):
            c["logo"] = e["attrs"]["tvg-logo"]
        if "http-user-agent" in e["opts"]:
            c["ua"] = e["opts"]["http-user-agent"]
        if "Geo-blocked" in flags:
            c["geo"] = 1
        if "Not 24/7" in flags:
            c["part"] = 1
        jio = jio_id(name, jio_ids, lang)
        if jio:
            c["jio"] = jio
        if e.get("abroad"):
            c["_abroad"] = 1
        kept.append(c)

    if "--no-check" not in sys.argv:
        print(f"testing {len(kept)} streams ...")
        with ThreadPoolExecutor(max_workers=32) as pool:
            alive = list(pool.map(lambda c: stream_alive(c["url"], c.get("ua")), kept))
        # A stream offline at the moment of one check is often back minutes later (Raj TV, Balle Balle HD ...), so
        # a channel that was in the last build stays unless it was also offline in the check before (the owner's
        # choice). data/stream_failures.json counts each channel's checks missed in a row.
        previous = set()
        if os.path.exists(OUT):
            previous = {c["name"] for c in json.load(open(OUT, encoding="utf-8"))["channels"]}
        failures_path = os.path.join(DATA, "stream_failures.json")
        failures = json.load(open(failures_path, encoding="utf-8")) if os.path.exists(failures_path) else {}
        # A channel an official app also carries (9XM, Good News Today in ZEE5) opens there instead at once.
        in_apps = {guide_key(r[1]) for r in json.load(open(LINKED, encoding="utf-8"))["channels"]}
        missed, still, second_chance = {}, [], []
        for c, ok in zip(kept, alive):
            if ok:
                still.append(c)
                continue
            missed[c["name"]] = failures.get(c["name"], 0) + 1
            if missed[c["name"]] < 2 and c["name"] in previous and guide_key(c["name"]) not in in_apps:
                still.append(c)
                second_chance.append(c["name"])
            else:
                excluded.append((c["name"], domain(urlparse(c["url"]).hostname), "offline when checked"))
        with open(failures_path, "w", encoding="utf-8") as f:
            json.dump(missed, f, ensure_ascii=False, indent=0)
        kept = still
        if second_chance:
            print(f"offline now but kept (first miss): {', '.join(sorted(second_chance))}")

    linked, linked_skipped, app_alternatives = load_linked(channels, {guide_key(c["name"]) for c in kept}, jio_ids)
    for c in kept:
        # "alt": the official-app links the guide falls back to if the TV finds this free stream dead.
        if guide_key(c["name"]) in app_alternatives:
            c["alt"] = app_alternatives[guide_key(c["name"])]
    kept.extend(linked)

    # Number DTH-style: each genre band starts on its fixed number. In most bands, channels an Airtel DTH box
    # carries come first, in Airtel's own order (the big channels on top, then each region's block, as the owner
    # knows it from home); the rest follow by language (Hindi, English, the regional languages), official-app
    # channels first, then by name. Entertainment, Movies and News go by language first (the owner's choice:
    # Airtel's numbers mix English channels in among the Hindi ones there), with Airtel's order inside each
    # language: Hindi, English, then the regional languages; in News, Hindi, then the Chhattisgarh channels,
    # then English, then the rest.
    airtel = load_airtel()

    def lang_rank(l):
        return (LANG_ORDER.index(l), "") if l in LANG_ORDER else (len(LANG_ORDER), l)

    def natural(name):
        return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name.lower())]

    def language_group(c):
        if c["cat"] != "News":
            return lang_rank(c["lang"])
        # News (the owner's order): national Hindi, Chhattisgarh, English, other states' Hindi, then the rest.
        if c.get("region") == "Chhattisgarhi":
            return (0, 1, "")
        if c["lang"] == "English":
            return (0, 2, "")
        if c["lang"] == "Hindi":
            return (0, 3 if STATE_NEWS.search(c["name"]) else 0, "")
        return (1,) + lang_rank(c["lang"])

    def order(c):
        # Channels from abroad come after India's in every genre, so adding them moves no Indian channel's number.
        return (c.get("_abroad", 0),) + order_in_india(c)

    def order_in_india(c):
        n = airtel_number(c["name"], airtel)
        hd = 1 if re.search(r"\bhd\b", c["name"], re.I) else 0
        linked_first = 0 if "links" in c else 1
        if c["cat"] in LANGUAGE_FIRST_BANDS:
            g = language_group(c)
            return (g, 0, n, hd, 0, natural(c["name"])) if n else (g, 1, 0, 0, linked_first, natural(c["name"]))
        if n:
            return (0, n, hd, (), 0, natural(c["name"]))
        return (1, 0, 0, lang_rank(c["lang"]), linked_first, natural(c["name"]))

    for c in kept:
        c["lang"] = LANG_FIXES.get(c["name"], c["lang"])
        c["cat"] = CAT_FIXES.get(c["name"], c["cat"])
        if CHHATTISGARH.search(c["name"]):
            c["region"] = "Chhattisgarhi"

    # Each band starts on a fixed number, so numbers stay put when a band gains or loses a few channels between
    # builds (Entertainment used to push every later band up). A band that ever outgrows its thousand carries on
    # from the next free thousand instead of overlapping the next band.
    numbered, next_free = [], 1001
    for band in BAND_ORDER:
        group = sorted((c for c in kept if c["cat"] == band), key=order)
        start = max(BAND_START[band], next_free)
        for i, c in enumerate(group):
            c["n"] = start + i
            c.pop("_abroad", None)
            numbered.append(c)
        if group:
            next_free = ((start + len(group) - 1) // 1000 + 1) * 1000 + 1
    # The anime genres sit in the unused part of Kids' thousand, each from its fixed start; none may run into the
    # next one (or Kids into the first).
    limits = list(ANIME_START.values())[1:] + [BAND_START["Kids"] + 1000]
    kids_end = max((c["n"] for c in numbered if c["cat"] == "Kids"), default=0)
    if kids_end >= min(ANIME_START.values()):
        raise SystemExit(f"Kids reached {kids_end}, where the anime genres start")
    for (band, start), limit in zip(ANIME_START.items(), limits):
        group = sorted((c for c in kept if c["cat"] == band), key=order)
        if start + len(group) > limit:
            raise SystemExit(f"{band} has {len(group)} channels, more than fit before {limit}")
        for i, c in enumerate(group):
            c["n"] = start + i
            c.pop("_abroad", None)
            numbered.append(c)
    if any(c["n"] >= WORLD_FIRST_NUMBER for c in numbered):
        raise SystemExit(f"an Indian genre reached {WORLD_FIRST_NUMBER}, where the World channels start")

    # Listings for channels JioTV does not carry, from a public XMLTV file (no special headers): the Samsung TV
    # Plus India guide as i.mjh.nz publishes it. The app downloads it and matches programmes on "xt".
    samsung = load_samsung_ids()
    for c in numbered:
        if not c.get("jio") and xmltv_key(c["name"]) in samsung:
            c["xt"] = samsung[xmltv_key(c["name"])]
        if short_name(c["name"]):
            c["sn"] = short_name(c["name"])

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    # "version" orders line-ups (the guide takes a downloaded one only if it is newer than the one it has);
    # "checked" says whether the streams were tested here (the guide tests unchecked ones itself).
    payload = {"generated": datetime.date.today().isoformat(), "version": int(datetime.datetime.now().timestamp()),
               "checked": "--no-check" not in sys.argv, "source": SOURCES["in.m3u"],
               "bands": [b for b in BAND_DISPLAY if any(c["cat"] == b for c in numbered)], "channels": numbered}
    if any("xt" in c for c in numbered):
        payload["xmltv"] = SAMSUNG_GUIDE
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    # Channels that show "No programme information": neither JioTV nor the Samsung TV Plus guide lists them.
    with open(os.path.join(DATA, "no_listings.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["number", "channel", "genre", "language", "plays in"])
        for c in numbered:
            if "jio" not in c and "xt" not in c:
                w.writerow([c["n"], c["name"], c["cat"], c["lang"],
                            c["links"][0]["app"] if "links" in c else "the guide"])
    with open(os.path.join(DATA, "excluded.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["channel", "stream domain", "reason"])
        w.writerows(sorted(excluded, key=lambda r: (r[2], r[0].lower())))

    print(f"playlist entries: {len(entries)}  kept: {len(numbered)}  left out: {len(excluded)}")
    for reason, n in Counter(r[2].split(" (")[0] for r in excluded).most_common():
        print(f"  left out {n:3}  {reason}")
    for band in payload["bands"]:
        nums = [c["n"] for c in numbered if c["cat"] == band]
        print(f"  {band:14} {len(nums):4} channels  {min(nums)}-{max(nums)}")
    print("languages:", Counter(c["lang"] for c in numbered).most_common(12))
    print(f"programme guide (JioTV) matched: {sum(1 for c in numbered if 'jio' in c)} of {len(numbered)}; "
          f"Samsung TV Plus guide: {sum(1 for c in numbered if 'xt' in c)} more; "
          f"none: {sum(1 for c in numbered if 'jio' not in c and 'xt' not in c)}")
    print(f"in Airtel DTH order (top of their genre): {sum(1 for c in numbered if airtel_number(c['name'], airtel))} of {len(numbered)}")
    by_app = Counter(app for c in numbered for app in {l["app"] for l in c.get("links", [])})
    print(f"official-app channels by app (a channel counts once per app): {dict(by_app)}; "
          f"kept the free stream instead for: {', '.join(sorted(set(linked_skipped))) or 'none'}")
    print(f"Chhattisgarh region: {', '.join(c['name'] for c in numbered if c.get('region'))}")
    print(f"wrote {OUT} ({os.path.getsize(OUT) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
