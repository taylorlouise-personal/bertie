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
- Reply with ONLY the finished JSON object, inside one ```json code block.

Yesterday's file:
```json
{current}
```"""

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

messages = [{"role": "user", "content": PROMPT}]
for _ in range(6):  # web search can pause a long turn; continue it
    resp = call(messages)
    if resp.get("stop_reason") == "pause_turn":
        messages = [messages[0], {"role": "assistant", "content": resp["content"]}]
        continue
    break

text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
m = re.search(r"```json\s*(\{.*\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
if not m:
    sys.exit("No JSON in Claude's reply. Keeping yesterday's file.\n" + text[:1500])
try:
    data = json.loads(m.group(1))
except json.JSONDecodeError as e:
    sys.exit(f"Reply wasn't valid JSON ({e}). Keeping yesterday's file.")

# --- sanity checks: keep yesterday's file unless today's looks right ---
problems = []
w = data.get("weather", {})
if w.get("condition") not in ("sunny", "cloudy", "rain", "storm"): problems.append("weather.condition")
for k in ("high", "low"):
    if not isinstance(w.get(k), (int, float)): problems.append(f"weather.{k}")
secs = data.get("sections", [])
if [s.get("id") for s in secs] != SECTION_IDS: problems.append("section ids/order")
def bad_url(u): return not (isinstance(u, str) and u.startswith("https://"))
for s in secs:
    for it in s.get("items", []):
        if bad_url(it.get("url")): problems.append(f"url in {s.get('id')}: {it.get('title')}")
        for k in ("start", "end"):
            if it.get(k) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(it[k])): problems.append(f"{k} in {it.get('title')}")
for n in data.get("austinToday", []):
    if bad_url(n.get("url")): problems.append("austinToday url")
if sum(len(s.get("items", [])) for s in secs) < 8: problems.append("too few items")
if problems:
    sys.exit("Today's data failed checks, keeping yesterday's file: " + "; ".join(problems[:12]))

data["updated"] = today
with open(DATA, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=1, ensure_ascii=False)
print(f"daily.json updated for {today}: {sum(len(s['items']) for s in secs)} items.")
