# Live TV guide: channel list

The weekly channel list for a private Live TV guide app on a Fire TV Stick. A GitHub Actions job
(`.github/workflows/lineup.yml`) rebuilds it every Monday from public sources and publishes:

- `lineup/channels.json`: the channels, genres, languages, free stream addresses and official-app links
- `lineup/channels.json.sig`: its signature; the app only accepts a list whose signature matches its built-in key

Sources: [iptv-org](https://github.com/iptv-org/iptv)'s public India playlist and database, JioTV's public channel
list, the Samsung TV Plus India guide from i.mjh.nz, and the lists in `tools/`. `tools/build_channels.py` leaves out
pirate re-broadcasts and pay channels outside licensed free platforms; pay channels are only linked to their
official apps (ZEE5, JioTV+, JioHotstar).

There are no streams, keys or personal data here.
