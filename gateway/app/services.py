"""Map domains to the app/service a person would recognise."""

# (service, domain suffixes). First match wins, so keep specific entries first.
_SERVICES: list[tuple[str, tuple[str, ...]]] = [
    ("YouTube", ("youtube.com", "googlevideo.com", "ytimg.com", "youtu.be", "youtubei.googleapis.com")),
    ("Netflix", ("netflix.com", "nflxvideo.net", "nflximg.net", "nflxext.com", "nflxso.net")),
    ("Disney+", ("disneyplus.com", "disney-plus.net", "dssott.com", "bamgrid.com")),
    ("Prime Video", ("primevideo.com", "aiv-cdn.net", "aiv-delivery.net", "pv-cdn.net")),
    ("Twitch", ("twitch.tv", "ttvnw.net", "jtvnw.net")),
    ("Spotify", ("spotify.com", "scdn.co", "spotifycdn.com", "spotilocal.com")),
    ("TikTok", ("tiktok.com", "tiktokcdn.com", "tiktokv.com", "byteoversea.com", "ibytedtos.com",
                "tiktokcdn-us.com", "muscdn.com")),
    ("Instagram", ("instagram.com", "cdninstagram.com")),
    ("WhatsApp", ("whatsapp.net", "whatsapp.com")),
    ("Facebook", ("facebook.com", "fbcdn.net", "facebook.net", "fbsbx.com", "messenger.com")),
    ("Snapchat", ("snapchat.com", "sc-cdn.net", "snap-dev.net", "snapkit.com")),
    ("X / Twitter", ("twitter.com", "twimg.com", "x.com", "t.co")),
    ("Telegram", ("telegram.org", "t.me", "telegram.me", "tdesktop.com")),
    ("Discord", ("discord.com", "discord.gg", "discordapp.com", "discordapp.net", "discord.media")),
    ("Reddit", ("reddit.com", "redd.it", "redditmedia.com", "redditstatic.com")),
    ("PlayStation Network", ("playstation.com", "playstation.net", "sonyentertainmentnetwork.com",
                             "psn.com", "scea.com")),
    ("Xbox", ("xboxlive.com", "xbox.com")),
    ("Steam", ("steampowered.com", "steamcontent.com", "steamstatic.com", "steamcommunity.com",
               "steamserver.net")),
    ("Epic / Fortnite", ("epicgames.com", "epicgames.dev", "unrealengine.com", "fortnite.com")),
    ("Roblox", ("roblox.com", "rbxcdn.com", "rbx.com")),
    ("Nintendo", ("nintendo.net", "nintendo.com")),
    ("Zoom", ("zoom.us", "zoom.com")),
    ("Microsoft Teams", ("teams.microsoft.com", "teams.live.com", "skype.com")),
    ("Apple iCloud", ("icloud.com", "icloud-content.com", "apple-cloudkit.com")),
    ("Apple updates", ("swcdn.apple.com", "updates.cdn-apple.com", "mesu.apple.com", "appldnld.apple.com")),
    ("Apple", ("apple.com", "mzstatic.com", "cdn-apple.com", "aaplimg.com", "apple-dns.net", "apple.news")),
    ("Windows Update", ("windowsupdate.com", "update.microsoft.com", "delivery.mp.microsoft.com",
                        "dl.delivery.mp.microsoft.com")),
    ("Microsoft", ("microsoft.com", "msn.com", "live.com", "office.com", "office.net", "outlook.com",
                   "bing.com", "msedge.net", "azureedge.net", "windows.net", "sharepoint.com", "onedrive.com")),
    ("WiZ lights", ("wiz.world", "wizconnected.com", "wiz.ws")),
    ("Tuya smart home", ("tuya.com", "tuyaeu.com", "tuyaus.com", "tuyacn.com", "tuyain.com")),
    ("Samsung", ("samsung.com", "samsungcloud.com", "samsungosp.com", "samsungqbe.com",
                 "samsungcloudsolution.com", "samsungelectronics.com", "samsungapps.com")),
    ("Amazon", ("amazon.com", "amazonaws.com", "media-amazon.com", "ssl-images-amazon.com",
                "amazon-adsystem.com", "a2z.com")),
    ("Google", ("google.com", "googleapis.com", "gstatic.com", "googleusercontent.com", "gvt1.com",
                "gvt2.com", "1e100.net", "googleadservices.com", "doubleclick.net", "google-analytics.com",
                "googlesyndication.com", "android.com", "gmail.com", "app-measurement.com")),
    ("CDN (various)", ("akamaized.net", "akamaihd.net", "akamai.net", "akamaiedge.net", "edgekey.net",
                       "cloudfront.net", "fastly.net", "fastlylb.net", "cloudflare.com", "llnwd.net",
                       "edgecastcdn.net", "b-cdn.net")),
]

_TWO_PART_TLDS = {"co.uk", "org.uk", "ac.uk", "com.au", "co.jp", "com.br", "co.nz", "co.in", "com.tr",
                  "com.sa", "com.eg", "com.cn", "co.za", "com.mx", "com.ar"}


def classify(domain: str | None) -> str:
    if not domain:
        return "Unknown (no DNS)"
    d = domain.lower().rstrip(".")
    for service, suffixes in _SERVICES:
        for s in suffixes:
            if d == s or d.endswith("." + s):
                return service
    return registrable(d)


def registrable(domain: str) -> str:
    parts = domain.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_PART_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])
