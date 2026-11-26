"""Refresh daily.json for Bertie's Austin.

Asks Claude (with web search) for today's listings, checks the answer,
and only overwrites daily.json when the new data looks right.
"""
import json, os, re, sys, urllib.request, urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = os.environ.get("CLAUDE_MODEL") or "claude-sonnet-5-5"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "daily.json")
SECTION_IDS = ["outings", "theater", "museums", "ut", "online", "books"]

if not API_KEY:
    sys.exit("ANTHROPIC_API_KEY secret is missing. Add it under Settings > Secrets and variables > Actions.")

now = datetime.now(ZoneInfo("America/Chicago"))
today = now.strftime("%Y-%m-%d")
pretty = now.strftime("%A, %B %-d, %Y")
current = open(DATA, encoding="utf-8").read()

PROMPT = f"""Today is {pretty} in Austin, Texas (America/Chicago time).

You keep a gentle daily guide called "Bertie's Austin" for a 91-year-old woman, Mama Lou, who lives in Austin. She has limited mobility and loves theater, museums and books. Her family reads the Longhorns section so she can chat about the game.

Below is yesterday's data file. Produce TODAY's version, in exactly the same JSON shape.

Research with web search:
1. weather: today's Austin forecast. "condition" must be one of: sunny, cloudy, rain, storm. Also set "summary" (2-4 words), "high", "low" (°F integers), "sunrise" and "sunset" (24-hour "H:MM", Central), and a source "url".
2. austinToday: 3-5 gentle, non-political local news items from the last few days. Each item has "text" (one or two plain sentences), "url" and "label" (the publication's name).
3. sections: keep the ids, in this order: {", ".join(SECTION_IDS)}. Keep each section's title, subtitle and icon. For each section, drop items whose "end" is before {today}. Keep good items that are still current, and add new ones happening in the next 4 weeks, aiming for 3-5 items per section.
   - outings, theater, museums: these must need little walking (seated, indoors, or seen from a car). Good sources: ZACH Theatre, the Paramount and Stateside, the Long Center, Ballet Austin, Austin Opera, the Blanton, the Harry Ransom Center, the Bullock Museum, the LBJ Library, Zilker Botanical Garden, the Lady Bird Johnson Wildflower Center and Umlauf.
   - ut: public lectures, talks, readings and concerts at UT Austin, from any college or graduate school (calendar.utexas.edu, LBJ School, Law, McCombs, Fine Arts, Liberal Arts, Natural Sciences).
   - online: live-streamed lectures, virtual museum tours and theater streams anyone can join. Give times in Central.
   - books: 2-4 notable recent books she'd enjoy, preferably ones the New York Times reviewed or listed, with "libby": true and "evergreen": true.
   Every item needs: "title", "start" and "end" (YYYY-MM-DD; the same day for one-day events), "when" (friendly date and time), "place", "blurb" (one warm sentence), "chips" (2-3 short tags such as "Free", "Seated", "Indoors", "Watch from home"), "url" (the real page for tickets or info, https only) and "linkLabel". Optional: "todayWhen", "phone", "evergreen": true for ongoing resources, "libby": true for books.
4. longhorns: "headline"; "last" ({{"us": "Texas", "usScore", "them", "themScore", "date"}}) for the most recent game in football, or in basketball during basketball season; "next" (date, time in Central, opponent, TV); "lines" (3-4 friendly, verifiable talking points); and "url".

Rules:
- Use only facts you found today on real web pages. Never invent events, dates, times, prices or scores. If you can't verify something, leave it out.
- Every url must start with https:// and point to the page where you found the item.
- Write warmly and plainly, for a 91-year-old. No politics.
- Set "updated" to "{today}".
- Reply with ONLY the finished JSON object: start with {{ and end with }}. No other text.

Yesterday's file:
{current}
"""

def call(messages):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 16000,
        "messages": messages,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 25,
                   "user_location": {"type": "approximate", "city": "Austin", "region": "Texas", "country": "US", "timezone": "America/Chicago"}}],
    }).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST", headers={
        "x-api-key": API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"Claude API error {e.code}: {e.read().decode()[:800]}")

def ask(messages):
    """Run one request, continuing if web search pauses or the reply is cut off."""
    out_text = ""
    for _ in range(8):
        resp = call(messages)
        out_text += "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        stop = resp.get("stop_reason")
        print(f"Claude stop_reason: {stop}")
        if stop in ("pause_turn", "max_tokens"):
            messages = messages + [{"role": "assistant", "content": resp["content"]}]
            if stop == "max_tokens":
                messages.append({"role": "user", "content": "Continue exactly where you stopped. Output only the rest of the JSON."})
            continue
        break
    return out_text

def extract(text):
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object found")
    return json.loads(text[a:b+1])

old = json.loads(current)
text = ask([{"role": "user", "content": PROMPT}])
try:
    data = extract(text)
except Exception as e:
    print(f"First reply wasn't usable JSON ({e}). Asking Claude to fix it.")
    print("Reply started with:", text[:600].replace("\n", " "))
    text = ask([{"role": "user", "content": "Turn the following into ONE valid JSON object in exactly the shape described. Output only the JSON, starting with { and ending with }.\n\n" + text[-60000:]}])
    try:
        data = extract(text)
    except Exception as e2:
        sys.exit(f"Still no valid JSON ({e2}). Keeping yesterday's file. Reply started with: {text[:800]}")

def good_url(u): return isinstance(u, str) and u.startswith("https://")
def good_date(d): return d is None or bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d)))

# --- repair instead of rejecting: drop bad items, fall back to yesterday per part ---
notes = []
w = data.get("weather") if isinstance(data.get("weather"), dict) else {}
if w.get("condition") not in ("sunny", "cloudy", "rain", "storm") or not all(isinstance(w.get(k), (int, float)) for k in ("high", "low")):
    notes.append("weather looked wrong, kept yesterday's"); w = old.get("weather", {})
data["weather"] = w

news = [n for n in (data.get("austinToday") or []) if isinstance(n, dict) and n.get("text") and good_url(n.get("url"))]
if not news: notes.append("no usable news, kept yesterday's"); news = old.get("austinToday", [])
data["austinToday"] = news

by_id_new = {s.get("id"): s for s in (data.get("sections") or []) if isinstance(s, dict)}
by_id_old = {s.get("id"): s for s in old.get("sections", [])}
fixed = []
for sid in SECTION_IDS:
    sec = by_id_new.get(sid) or by_id_old.get(sid)
    if not sec: continue
    base = by_id_old.get(sid, {})
    for k in ("title", "subtitle", "icon"):
        sec[k] = sec.get(k) or base.get(k)
    items = []
    for it in sec.get("items") or []:
        if not isinstance(it, dict) or not it.get("title"): continue
        if not good_url(it.get("url")) or not good_date(it.get("start")) or not good_date(it.get("end")):
            notes.append(f"dropped '{it.get('title')}' (bad link or date)"); continue
        if it.get("end") and it["end"] < today: continue
        items.append(it)
    if not items and base.get("items"):
        notes.append(f"{sid}: nothing usable, kept yesterday's"); items = [i for i in base["items"] if not i.get("end") or i["end"] >= today]
    sec["id"] = sid; sec["items"] = items
    fixed.append(sec)
data["sections"] = fixed

lh = data.get("longhorns")
if not (isinstance(lh, dict) and lh.get("next")): data["longhorns"] = old.get("longhorns")
elif lh.get("url") and not good_url(lh["url"]): lh.pop("url")

total = sum(len(s["items"]) for s in fixed)
if total < 6:
    sys.exit(f"Only {total} usable items. Keeping yesterday's file. Notes: {notes}")

data["updated"] = today
with open(DATA, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=1, ensure_ascii=False)
for n in notes: print("note:", n)
print(f"daily.json updated for {today}: {total} items.")
