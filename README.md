# Live TV guide: channel list

The weekly channel list for a private Live TV guide app on a Fire TV Stick. A GitHub Actions job
(`.github/workflows/lineup.yml`) rebuilds it every Monday from public sources and publishes:

- `lineup/channels.json`: the channels, genres, languages, free stream addresses and official-app links
- `lineup/channels.json.sig`: its signature; the app only accepts a list whose signature matches its built-in key
- `lineup/world.json` and `lineup/world.json.sig`: the World channels from other countries (`tools/build_world.py`),
  which the app shows only when its owner switches them on and ticks countries, numbered 10001 and up; inside each
  country, Pluto TV's channels in Pluto TV's own order (i.mjh.nz's public Pluto TV data), then the rest by name;
  channels that i.mjh.nz's public XMLTV guides list carry their guide file and id there, for Now / Next
- programme listings for more World channels: i.mjh.nz's files by a channel's exact name in its own country, and
  the `listings` branch: one small file per country (`<cc>.xml.gz`, titles and times only, an hour back to two days
  ahead), rebuilt every day by `.github/workflows/listings.yml`, which runs iptv-org's open-source grabber
  ([iptv-org/epg](https://github.com/iptv-org/epg)) for the World channels it can collect from a working site
  (`tools/collect_listings.py`); a channel the grabber missed keeps the programmes still ahead from the day before
- more listing sources for the channels still without (`tools/listing_sources.py`, run by the weekly job, which
  writes `lineup/listings_plan.json`): i.mjh.nz's files by a stream's own link id (Pluto TV, Roku, Samsung, ABC
  Australia) and PBS by call sign; community stations' own public Cablecast schedules (matched by the stream's own
  address); epgshare01.online's public guide files (US local stations by call sign, else an exact name in the
  channel's own country or FAST service); and the TV-guide sites iptv-org/epg supports, by exact name with the
  country agreeing. The daily job fetches the Cablecast schedules and reads the epgshare01 files itself (streamed, a
  file at a time) and puts the main list's channels in `listings/in.xml.gz`
- anime channels from abroad (and seven English ones from Pluto TV US that iptv-org does not list,
  `tools/build_world.py` EXTRA_ANIME) are in `channels.json`'s three anime genres
- channels iptv-org does not list, from licensed free services through i.mjh.nz's public data: Pluto TV's US, UK and
  Canadian channels with English audio (World list), and Samsung TV Plus India channels by a hand-checked table of
  genre and language (`tools/build_world.py` SAMSUNG_IN, main list); `channels.json` "guides" holds Pluto TV's guide
  files for its Pluto TV channels (Anime English, Anime Japanese, Anime other
  languages; 4501, 4601, 4701 and up)

When a week's list adds, drops or moves channels, the job opens an issue listing them
(`tools/lineup_changes.py`). In the app, new channels wait in a "New" genre until the owner keeps or hides each one.

`app/` holds the app's own updates, built from its private source repository: `LiveTVGuide.apk` and
`release.json` (version and SHA-256 of the APK) with `release.json.sig`, signed with the same key. The app installs
an update only when that signature and the APK's SHA-256 both match, and Android only when the APK carries the
installed app's certificate.

Sources: [iptv-org](https://github.com/iptv-org/iptv)'s public India and world playlists and database, JioTV's
public channel list, the Samsung TV Plus India guide from i.mjh.nz, and the lists in `tools/`. The builders leave out
pirate re-broadcasts, pay channels (Indian or foreign) outside licensed free platforms, re-broadcast servers and
streams that need a faked referring page; Indian pay channels are only linked to their official apps (ZEE5, JioTV+,
JioHotstar). `data/world_excluded.csv`, written by each build, lists what the World list left out and why.

There are no streams, keys or personal data here.
