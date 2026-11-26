"""Refresh daily.json for Bertie's Austin.

Two kinds of run, to keep costs down:
  light (most days): today's weather (free, from Open-Meteo), plus a few
                     searches for Austin news and the Longhorns.
  full  (Mon & Thu): everything in light, plus the upcoming listings.
                     Good listings already in the file are kept, and the
                     searches go to finding new things.

Set MODE=light / full to force one; otherwise it picks by weekday.
Only overwrites daily.json with data that passes its checks.
"""
import json, os, re, sys, urllib.request, urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = os.environ.get("CLAUDE_MODEL") or "claude-sonnet-5-5"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "daily.json")
SECTION_IDS = ["outings", "theater", "museums", "ut", "online", "books"]
FULL_DAYS = {0, 3}            # Monday and Thursday
LIGHT_SEARCHES = 4
FULL_SEARCHES = 15

if not API_KEY:
    sys.exit("ANTHROPIC_API_KEY secret is missing. Add it under Settings > Secrets and variables > Actions.")

now = datetime.now(ZoneInfo("America/Chicago"))
today = now.strftime("%Y-%m-%d")
pretty = now.strftime("%A, %B %-d, %Y")
mode = (os.environ.get("MODE") or "auto").strip().lower()
if mode not in ("light", "full"):
    mode = "full" if now.weekday() in FULL_DAYS else "light"
print(f"{pretty}: {mode} refresh")

old = json.load(open(DATA, encoding="utf-8"))
data = json.loads(json.dumps(old))   # working copy

def good_url(u): return isinstance(u, str) and u.startswith("https://")
def good_date(d): return d is None or bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d)))
def still_on(it): return not it.get("end") or it["end"] >= today

# ---------------------------------------------------------------- weather (free)
def weather():
    url = ("https://api.open-meteo.com/v1/forecast?latitude=30.2672&longitude=-97.7431"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset"
           "&temperature_unit=fahrenheit&timezone=America%2FChicago&forecast_days=1")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.load(r)["daily"]
        c = d["weather_code"][0]
        cond = ("storm" if c >= 95 else "rain" if (51 <= c <= 67 or 80 <= c <= 82)
                else "cloudy" if c >= 2 else "sunny")
        hi, lo = round(d["temperature_2m_max"][0]), round(d["temperature_2m_min"][0])
        feel = "chilly" if hi < 60 else "mild" if hi < 80 else "warm" if hi < 92 else "hot"
        summary = {"sunny": f"Sunny and {feel}", "cloudy": "Partly cloudy" if c == 2 else "Cloudy",
                   "rain": "Rainy", "storm": "Thunderstorms"}[cond]
        hm = lambda s: s.split("T")[1][:5].lstrip("0")
        return {"condition": cond, "summary": summary, "high": hi, "low": lo,
                "sunrise": hm(d["sunrise"][0]), "sunset": hm(d["sunset"][0]),
                "url": "https://forecast.weather.gov/MapClick.php?lat=30.2672&lon=-97.7431"}
    except Exception as e:
        print(f"note: weather lookup failed ({e}); keeping yesterday's")
        return None

w = weather()
if w: data["weather"] = w

# ---------------------------------------------------------------- Claude helpers
def call(messages, max_searches):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 16000,
        "messages": messages,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches,
                   "user_location": {"type": "approximate", "city": "Austin", "region": "Texas",
                                     "country": "US", "timezone": "America/Chicago"}}],
    }).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST", headers={
        "x-api-key": API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"Claude API error {e.code}: {e.read().decode()[:800]}")

def ask(prompt, max_searches):
    messages = [{"role": "user", "content": prompt}]
    out = ""
    for _ in range(8):
        resp = call(messages, max_searches)
        u = resp.get("usage", {})
        print(f"stop={resp.get('stop_reason')} input_tokens={u.get('input_tokens')} output_tokens={u.get('output_tokens')} "
              f"searches={u.get('server_tool_use', {}).get('web_search_requests')}")
        out += "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        stop = resp.get("stop_reason")
        if stop in ("pause_turn", "max_tokens"):
            messages = messages + [{"role": "assistant", "content": resp["content"]}]
            if stop == "max_tokens":
                messages.append({"role": "user", "content": "Continue exactly where you stopped. Output only the rest of the JSON."})
            continue
        break
    a, b = out.find("{"), out.rfind("}")
    if a < 0 or b <= a:
        print("note: no JSON in reply. Reply began: " + out[:500].replace("\n", " "))
        return None
    try:
        return json.loads(out[a:b+1])
    except json.JSONDecodeError as e:
        print(f"note: reply wasn't valid JSON ({e})")
        return None

RULES = f"""Rules:
- Use only facts you found on real web pages. Never invent events, dates, times, prices or scores. If you can't verify something, leave it out.
- Every url must start with https:// and be the page where you found the item.
- Write warmly and plainly, for a 91-year-old. No politics.
- Reply with ONLY one JSON object: start with {{ and end with }}. No other text."""

ABOUT = f"""Today is {pretty} in Austin, Texas (Central time). You help keep "Bertie's Austin", a gentle daily guide for Mama Lou, a 91-year-old in Austin with limited mobility who loves theater, museums and books. Her family reads the Longhorns section so she can chat about the games."""

# ---------------------------------------------------------------- light part: news + sports (every day)
light_prompt = f"""{ABOUT}

Find just two things, using as few searches as you can (about {LIGHT_SEARCHES}):
1. "austinToday": 3-4 gentle, non-political Austin news items from the last day or two (weather, holidays, parks, arts, food, community). Each item is {{"text": one or two plain sentences, "url", "label": the publication's name}}.
2. "longhorns": {{"headline", "last": {{"us": "Texas", "usScore", "them", "themScore", "date"}}, "next": date, time in Central, opponent, TV channel, "lines": 3-4 friendly talking points, "url"}}. Cover the most recent Texas football game, or men's or women's basketball when football is over.

Yesterday's versions, for reference. Keep anything that is still true:
{json.dumps({"austinToday": old.get("austinToday"), "longhorns": old.get("longhorns")}, ensure_ascii=False)}

{RULES}
Shape: {{"austinToday": [...], "longhorns": {{...}}}}"""

notes = []
lite = ask(light_prompt, LIGHT_SEARCHES) or {}
news = [n for n in (lite.get("austinToday") or []) if isinstance(n, dict) and n.get("text") and good_url(n.get("url"))]
if news: data["austinToday"] = news
else: notes.append("news: nothing usable, kept yesterday's")
lh = lite.get("longhorns")
if isinstance(lh, dict) and lh.get("next"):
    if lh.get("url") and not good_url(lh["url"]): lh.pop("url")
    data["longhorns"] = lh
else: notes.append("longhorns: nothing usable, kept yesterday's")

# ---------------------------------------------------------------- full part: listings (Mon & Thu)
for s in data.get("sections", []):            # always drop things that have ended
    s["items"] = [i for i in s.get("items", []) if still_on(i)]

if mode == "full":
    counts = {s["id"]: sum(1 for i in s["items"] if not i.get("evergreen")) for s in data["sections"]}
    full_prompt = f"""{ABOUT}

Below are the listings we already have; anything that has ended is already removed. The existing items were checked earlier, so KEEP them as they are and don't spend searches re-checking them. Spend your searches (about {FULL_SEARCHES} in total) on finding NEW items for the next 4 weeks, starting with the sections that have the fewest upcoming items. Current counts of upcoming items: {json.dumps(counts)}. Aim for 4-5 items per section.

What belongs where:
- outings, theater, museums: things that need little walking (seated, indoors, or seen from a car). Good sources: ZACH Theatre, the Paramount and Stateside, the Long Center, Ballet Austin, Austin Opera, the Blanton, the Harry Ransom Center, the Bullock Museum, the LBJ Library, Zilker Botanical Garden, the Lady Bird Johnson Wildflower Center and Umlauf. One search of a good events roundup (CultureMap, Austin Chronicle, Do512) can cover several sections at once.
- ut: public lectures, talks, readings and concerts at UT Austin, from any college or graduate school. calendar.utexas.edu covers most of them in one search.
- online: live-streamed lectures, virtual museum tours and theater streams anyone can join. Give times in Central.
- books: 2-4 notable recent books she'd enjoy, preferably ones the New York Times reviewed, with "libby": true and "evergreen": true. Only add one if you come across it; don't search specially.

Each new item: {{"title", "start", "end" (YYYY-MM-DD; same day for one-day events), "when" (friendly date and time), "place", "blurb" (one warm sentence), "chips" (2-3 short tags like "Free", "Seated", "Indoors", "Watch from home"), "url", "linkLabel"}}. Optional: "todayWhen", "phone", "evergreen", "libby".

Return ALL sections (the existing items plus your new ones), keeping each section's id, title, subtitle and icon, in this order: {", ".join(SECTION_IDS)}.

Current listings:
{json.dumps(data["sections"], ensure_ascii=False)}

{RULES}
Shape: {{"sections": [...]}}"""
    full = ask(full_prompt, FULL_SEARCHES) or {}
    new_by_id = {s.get("id"): s for s in (full.get("sections") or []) if isinstance(s, dict)}
    if not new_by_id: notes.append("listings: nothing usable, kept the current ones")
    merged = []
    for cur in data["sections"]:
        sid = cur["id"]; got = new_by_id.get(sid)
        items = []
        for it in (got.get("items") if got else None) or cur["items"]:
            if not isinstance(it, dict) or not it.get("title"): continue
            if not good_url(it.get("url")) or not good_date(it.get("start")) or not good_date(it.get("end")):
                notes.append(f"dropped '{it.get('title')}' (bad link or date)"); continue
            if still_on(it): items.append(it)
        if not items: items = cur["items"]
        merged.append({**cur, "items": items})
    data["sections"] = merged

total = sum(len(s["items"]) for s in data["sections"])
if total < 6:
    sys.exit(f"Only {total} listings left. Keeping yesterday's file. Notes: {notes}")

data["updated"] = today
with open(DATA, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=1, ensure_ascii=False)
for n in notes: print("note:", n)
print(f"daily.json updated for {today} ({mode}): {total} listings.")
