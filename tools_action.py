# tools_action.py
from urllib.parse import quote, urlencode

# ---------- Apple Maps ----------
def open_apple_maps(source: str, destination: str, transport: str = "d"):
    """
    Build Apple Maps directions URL.
    transport: d (drive), w (walk), r (transit), c (cycling where available)
    """
    return f"maps://?saddr={quote(source)}&daddr={quote(destination)}&dirflg={quote(transport)}"

# ---------- FaceTime ----------
def open_facetime(phone_or_email: str):
    """
    Initiate FaceTime with a phone number or Apple ID email.
    For audio-only, use facetime-audio:// (not implemented here).
    """
    return f"facetime://{quote(phone_or_email)}"

# ---------- Mail (mailto) ----------
def open_mailto(recipient: str, cc: str = None, bcc: str = None, subject: str = None, body: str = None):
    """
    Open compose window with prefilled fields.
    """
    query = {}
    if cc: query["cc"] = cc
    if bcc: query["bcc"] = bcc
    if subject: query["subject"] = subject
    if body: query["body"] = body
    if query:
        return f"mailto:{quote(recipient)}?{urlencode(query, safe=':/(), ')}"
    return f"mailto:{quote(recipient)}"

# ---------- Settings ----------
def open_settings_pane(root: str = "WIFI", path: str = None):
    """
    Open a specific Settings pane.
    WARNING: App-Prefs: is private API and can be rejected by App Review.
    """
    if path:
        return f"App-Prefs:root={quote(root)}&path={quote(path)}"
    return f"App-Prefs:root={quote(root)}"

# ---------- Calendar ----------
def open_calendar_date(date_yyyy_mm_dd: str):
    """
    Open Calendar to a specific date (YYYY-MM-DD). Uses CFAbsoluteTime seconds since 2001-01-01 00:00:00 GMT.
    We set time to local 12:00 to avoid timezone/DST rolling to the previous/next day.
    """
    try:
        # parse as local noon
        dt = datetime.strptime(date_yyyy_mm_dd, "%Y-%m-%d").replace(hour=12, minute=0, second=0, microsecond=0)
        # UNIX timestamp in local time
        unix_ts_local = int(time.mktime(dt.timetuple()))
        # CFAbsoluteTime (Apple reference time) = UNIX - 978307200
        cfabs = unix_ts_local - 978307200
        return f"calshow://{cfabs}"
    except Exception:
        # Fallback: open the app if parsing fails
        return "calshow://"

# ---------- Google Maps (Street View or search) ----------
def open_google_maps_streetview(q: str = None, center_lat: str = None, center_lng: str = None):
    """
    Launch Google Maps Street View centered at lat/lng with optional query.
    """
    params = {}
    if q: params["q"] = q
    if center_lat and center_lng:
        params["center"] = f"{center_lat},{center_lng}"
    params["mapmode"] = "streetview"
    return "comgooglemaps://?" + urlencode(params, safe=",:")

# ---------- Spotify ----------
def open_spotify_search(query: str):
    """
    Open Spotify search results for the given query.
    """
    return f"spotify:search:{quote(query)}"

# ---------- Things 3 ----------
def open_things_add(title: str, notes: str = None, when: str = None, deadline: str = None, tags: str = None, list_name: str = None):
    """
    Add a new to-do in Things 3.
    - when/deadline can be relative words like 'today' or absolute like '2025-12-31'
    - tags should be comma-separated string
    """
    params = {"title": title}
    if notes: params["notes"] = notes
    if when: params["when"] = when
    if deadline: params["deadline"] = deadline
    if tags: params["tags"] = tags
    if list_name: params["list"] = list_name
    return "things:///add?" + urlencode(params, safe=":,/ ")

# ---------- WhatsApp ----------
def open_whatsapp_send(phone: str, text: str = None):
    """
    Open WhatsApp chat with a phone number and optional prefilled text.
    """
    params = {"phone": phone}
    if text: params["text"] = text
    return "whatsapp://send?" + urlencode(params, safe="+, ")

# ---------- Uber ----------
def open_uber_setpickup(pickup_lat: str, pickup_lng: str, pickup_nickname: str = None,
                        dropoff_lat: str = None, dropoff_lng: str = None,
                        dropoff_nickname: str = None, product_id: str = None):
    """
    Prepare an Uber ride with pickup/dropoff and optional product ID.
    """
    params = {"action": "setPickup",
              "pickup[latitude]": str(pickup_lat),
              "pickup[longitude]": str(pickup_lng)}
    if pickup_nickname:
        params["pickup[nickname]"] = pickup_nickname
    if dropoff_lat and dropoff_lng:
        params["dropoff[latitude]"] = str(dropoff_lat)
        params["dropoff[longitude]"] = str(dropoff_lng)
    if dropoff_nickname:
        params["dropoff[nickname]"] = dropoff_nickname
    if product_id:
        params["product_id"] = product_id
    return "uber://?" + urlencode(params, safe="[]:,.")


def open_phone_call(phone: str):
    """
    Start a phone call with tel: scheme.
    """
    return f"tel:{quote(phone)}"


def open_sms(phone: str, body: str = None):
    """
    Open Messages for SMS/MMS to a number with optional body.
    Note: body parameter support varies across iOS versions.
    """
    if body:
        return f"sms:{quote(phone)}&body={quote(body)}"
    return f"sms:{quote(phone)}"


def open_app_store(url: str):
    """
    Open App Store using itms or itms-apps URL.
    Pass a full URL such as itms-apps://itunes.apple.com/app/id123456789
    """
    return url


def open_shortcuts(name: str = None):
    """
    Open Shortcuts or run a shortcut by name.
    If name is provided, uses shortcuts://run-shortcut?name=<name>
    """
    if name:
        return f"shortcuts://run-shortcut?name={quote(name)}"
    return "shortcuts://"


def open_notes():
    """
    Open Apple Notes (undocumented).
    """
    return "mobilenotes://"


def open_reminders():
    """
    Open Apple Reminders (undocumented).
    """
    return "x-apple-reminderkit://"


def open_photos():
    """
    Open Apple Photos (undocumented).
    """
    return "photos-redirect://"


def open_books():
    """
    Open Apple Books (iBooks).
    """
    return "ibooks://"


def open_podcasts(feed: str = None):
    """
    Open Apple Podcasts. If feed provided, attempt to add/preview the podcast.
    """
    if feed:
        return f"podcast://{quote(feed)}"
    return "podcast://"


def open_music():
    """
    Open Apple Music (behavior may vary).
    """
    return "music://"


def open_wallet():
    """
    Open Apple Wallet (shoebox).
    """
    return "shoebox://"


def open_findmy(tab: str = None):
    """
    Open Find My app, optionally to a specific tab like items, people, devices.
    """
    if tab:
        return f"findmy://{quote(tab)}"
    return "findmy://"
