#!/usr/bin/env python3
"""Scrape Munster Rugby's SportLoMo competition pages into data/comp_<ID>.json.

Written to survive markup changes: league tables are read from real <table>
elements by their header names, and match rows are read from whichever
container holds a date, a pair of team names and an optional score. Every run
prints a format sample for one competition so the log alone is enough to
diagnose a parsing failure.
"""

import json, os, re, sys, time, datetime
import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data")
BASE = "https://munsterrugby.sportlomo.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# compId -> (display name, route, ballincollig-only?)
COMPS = {
    "216454": ("Junior League Division 2", "league", False),
    "219174": ("South - Junior 3 League", "league", False),
    "218360": ("Womens Division 2B", "league", False),
    "219967": ("Boys Clubs U18.5 Munster Conference 5", "league", False),
    "219620": ("Boys Clubs U18.5 South Munster League Group 3", "league", False),
    "219998": ("Boys Clubs U16 Munster Conference 7", "league", False),
    "219627": ("Boys Clubs U16 South Munster League Group 4", "league", False),
    "219989": ("Boys Clubs U16 Munster 15 S", "league", False),
    "219625": ("Boys Clubs U16 South Munster League Group 3", "league", False),
    "220164": ("Boys Clubs U14 South Munster League Group 1", "league", False),
    "220174": ("Boys Clubs U14 South Munster Dev 2014 Group 2 NC", "league", False),
    "216436": ("South - O'Neill Cup", "league-diagram", False),
    "217574": ("Junior Clubs Challenge Shield", "league-diagram", False),
    "218733": ("South - Muskerry (O'Sullivan) Cup", "league-diagram", False),
    "216092": ("Junior 1 Friendly", "competition_fixtures_results", True),
    "216122": ("Boys Club/School U18 Friendly", "competition_fixtures_results", True),
    "216124": ("Boys Clubs U13 Friendly", "competition_fixtures_results", True),
    "216125": ("Boys Clubs U14 Friendly", "competition_fixtures_results", True),
    "216132": ("Girls Clubs U16 Friendly", "competition_fixtures_results", True),
    "216134": ("Girls Clubs U18.5 Friendly", "competition_fixtures_results", True),
}

# Season boundary: months Jul-Dec belong to the earlier year.
SEASON_START = int(os.environ.get("SEASON_START_YEAR", "0")) or None

MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

RE_DMY = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b")
RE_DMONY = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})\b")
RE_DMON = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\b")
RE_TIME = re.compile(r"\b([0-2]?\d):([0-5]\d)\b")
RE_SCORE = re.compile(r"^\s*(\d{1,3})\s*(?:\(\s*(\d*)\s*T\s*\))?\s*$", re.I)
RE_VS = re.compile(r"^\s*(\d{1,3})\s*(?:\(\s*(\d*)\s*T\s*\))?\s*[Vv]\.?\s*"
                   r"(\d{1,3})\s*(?:\(\s*(\d*)\s*T\s*\))?\s*$")
RE_ROUND = re.compile(r"^(?:\d+(?:st|nd|rd|th)\s+Round|Round\s+\d+|Quarter[- ]?Final|"
                      r"Semi[- ]?Final|Final|Group\s+\w+|Section\s+\w+|Preliminary.*)$", re.I)


def season_year(month):
    """Map a month onto the right calendar year for the current season."""
    start = SEASON_START
    if start is None:
        today = datetime.date.today()
        start = today.year if today.month >= 7 else today.year - 1
    return start if month >= 7 else start + 1


def parse_date(text):
    m = RE_DMY.search(text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = RE_DMONY.search(text)
    if m and m.group(2)[:3].lower() in MONTHS:
        return f"{int(m.group(3)):04d}-{MONTHS[m.group(2)[:3].lower()]:02d}-{int(m.group(1)):02d}"
    m = RE_DMON.search(text)
    if m and m.group(2)[:3].lower() in MONTHS:
        mo = MONTHS[m.group(2)[:3].lower()]
        return f"{season_year(mo):04d}-{mo:02d}-{int(m.group(1)):02d}"
    return None


def parse_time(text):
    m = RE_TIME.search(text)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if (hh, mm) == (0, 0):      # site's placeholder for "to be confirmed"
        return None
    return f"{hh:02d}:{mm:02d}"


def looks_like_team(s):
    if not s or len(s) < 3 or len(s) > 80:
        return False
    if RE_SCORE.match(s) or RE_VS.match(s):
        return False
    if parse_date(s) and not re.search(r"[A-Za-z]{4}", s):
        return False
    return bool(re.search(r"[A-Za-z]{3}", s))


def cell_texts(el):
    """Direct-ish child texts of a container, blanks removed."""
    out = []
    for ch in el.find_all(["li", "td", "th", "span", "div", "p"], recursive=False):
        t = " ".join(ch.get_text(" ", strip=True).split())
        if t:
            out.append(t)
    if not out:
        t = " ".join(el.get_text("\n", strip=True).split("\n"))
        out = [x.strip() for x in t.split("\n") if x.strip()]
    return out


def parse_table(soup):
    """Return (columns, rows) for the standings table, or ([], [])."""
    best = None
    for tb in soup.find_all("table"):
        heads = [" ".join(th.get_text(" ", strip=True).split())
                 for th in tb.find_all("th")]
        if not heads:
            first = tb.find("tr")
            heads = [" ".join(td.get_text(" ", strip=True).split())
                     for td in (first.find_all(["td", "th"]) if first else [])]
        low = [h.lower() for h in heads]
        if any(h == "team" for h in low) and any(h in ("pts", "points") for h in low):
            best = (tb, heads)
            break
    if not best:
        return [], []

    tb, heads = best
    keymap = {"pos": "pos", "team": "team", "pld": "pld", "p": "pld", "played": "pld",
              "w": "w", "d": "d", "l": "l", "bp": "bp", "pts": "pts", "points": "pts",
              "pf": "f", "f": "f", "for": "f", "pa": "a", "a": "a", "against": "a",
              "diff": "diff", "pd": "diff", "tf": "tries", "t": "tries", "tries": "tries"}
    rows = []
    body = tb.find("tbody") or tb
    for tr in body.find_all("tr"):
        cells = [" ".join(td.get_text(" ", strip=True).split())
                 for td in tr.find_all(["td", "th"])]
        if not cells or len(cells) < 3:
            continue
        if [c.lower() for c in cells[:len(heads)]] == [h.lower() for h in heads]:
            continue
        # the table often carries a leading row-index column the header omits
        off = len(cells) - len(heads)
        vals = cells[off:] if off > 0 else cells
        row = {k: None for k in ("pos", "team", "pld", "w", "d", "l", "bp",
                                 "pts", "f", "a", "diff", "tries")}
        for h, v in zip(heads, vals):
            k = keymap.get(h.strip().lower())
            if not k:
                continue
            if k == "team":
                row["team"] = v
            else:
                try:
                    row[k] = int(re.sub(r"[^\d\-]", "", v) or 0)
                except ValueError:
                    row[k] = None
        if row["team"]:
            rows.append(row)
    return heads, rows


def parse_matches(soup):
    """Pull every match on the page. Returns (fixtures, results)."""
    fixtures, results, seen = [], [], set()
    current_round = None
    section = None          # "fixtures" or "results", from the page's own headings
    current_date = None     # friendly pages put the date in a heading above the row

    for el in soup.find_all(True):
        txt = " ".join(el.get_text(" ", strip=True).split())
        if len(txt) < 40 and el.name in ("h1", "h2", "h3", "h4", "h5", "strong", "b"):
            low = txt.lower()
            if "upcoming fixture" in low:
                section, current_round = "fixtures", None
                continue
            if "latest result" in low or low.startswith("result"):
                section, current_round = "results", None
                continue
            if "current standings" in low or "league table" in low:
                section = None
                continue
            if RE_ROUND.match(txt):
                current_round = txt
                continue
        # a short standalone date, e.g. "Sun 27/09/2026", heading the rows beneath it
        if len(txt) <= 32 and not RE_VS.search(txt):
            d = parse_date(txt)
            if d and not re.search(r"[A-Za-z]{4}", re.sub(r"[A-Za-z]{3}\b", "", txt)):
                current_date = d

        if el.name not in ("ul", "ol", "tr", "li", "div"):
            continue

        cells = [c for c in cell_texts(el) if c.strip().lower() != "team sheet"]
        if not (3 <= len(cells) <= 12):
            continue
        date = parse_date(cells[0]) or (parse_date(cells[1]) if len(cells) > 1 else None)
        own_date = date is not None
        if not date:
            date = current_date
        if not date:
            continue

        teams, scores, venue = [], [], None
        for c in cells:
            if c.lower().startswith("venue"):
                venue = c.split(":", 1)[1].strip() if ":" in c else None
                continue
            m = RE_VS.match(c)
            if m:
                scores = [(int(m.group(1)), m.group(2)), (int(m.group(3)), m.group(4))]
                continue
            m = RE_SCORE.match(c)
            if m:
                scores.append((int(m.group(1)), m.group(2)))
                continue
            if looks_like_team(c):
                teams.append(c)
        if len(teams) < 2:
            continue
        # borrowing the heading's date is only safe for a row that really is a match,
        # so insist on a kick-off time or a score before accepting one
        if not own_date and not scores and not RE_TIME.search(" ".join(cells[:2])):
            continue
        home, away = teams[0], teams[1]
        # these pages give the venue positionally rather than labelled
        if venue is None and len(teams) >= 3:
            venue = teams[2]
        if home == away:
            continue

        key = (date, home, away)
        if key in seen:
            continue
        seen.add(key)

        tries = lambda t: (int(t[1]) if t[1] else None)
        if len(scores) < 2 and section == "results":
            # played but no score entered, usually a concession
            results.append(dict(date=date, home=home, homeScore=None, homeTries=None,
                                away=away, awayScore=None, awayTries=None,
                                venue=venue, round=current_round))
        elif len(scores) >= 2:
            results.append(dict(date=date, home=home, homeScore=scores[0][0],
                                homeTries=tries(scores[0]), away=away,
                                awayScore=scores[1][0], awayTries=tries(scores[1]),
                                venue=venue, round=current_round))
        else:
            fixtures.append(dict(date=date, time=parse_time(" ".join(cells[:3])),
                                 home=home, away=away, venue=venue, round=current_round))
    return fixtures, results


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers={"User-Agent": UA,
                                           "Accept-Language": "en-IE,en;q=0.9"},
                             timeout=45)
            body = r.text or ""
            # One or two of these pages answer HTTP 500 while still serving the
            # full document. Judge the body, not the status line.
            usable = len(body) > 20000 and ("sportlomo" in body.lower() or "<table" in body.lower())
            if r.status_code == 200 and len(body) > 2000:
                return body
            if usable:
                print(f"  note: HTTP {r.status_code} but the page body looks complete "
                      f"({len(body)} bytes), using it")
                return body
            last = f"HTTP {r.status_code}, {len(body)} bytes"
        except Exception as e:                                  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url} failed: {last}")


def sample_log(html, comp_id):
    """Print the shape of one page so a parse failure is diagnosable from the log."""
    soup = BeautifulSoup(html, "html.parser")
    print(f"\n=== FORMAT SAMPLE: competition {comp_id} ===")
    for tb in soup.find_all("table")[:2]:
        heads = [th.get_text(" ", strip=True) for th in tb.find_all("th")][:14]
        print("TABLE HEADERS:", heads)
        for tr in tb.find_all("tr")[1:4]:
            print("  ROW:", [td.get_text(" ", strip=True)
                             for td in tr.find_all(["td", "th"])][:14])
    lines = [l.strip() for l in soup.get_text("\n").split("\n") if l.strip()]
    print("TEXT LINES 1-60:")
    for l in lines[:60]:
        print("  |", l[:120])
    print("=== END SAMPLE ===\n")


def sweep_site_wide(docs):
    """Backfill from Munster Rugby's own all-competition feeds.

    Individual competition pages occasionally answer HTTP 500 and serve a
    truncated document: the standings and fixtures render, the results section
    never does. The site-wide /results/ and /fixtures/ pages carry the same
    matches, so anything a broken page dropped can be recovered from them.

    A match is filed by team name rather than by the feed's own competition
    heading, because those headings are not always right.
    """
    added = 0
    for path, kind in (("results", "results"), ("fixtures", "fixtures")):
        try:
            html = fetch(f"{BASE}/{path}/")
        except Exception as e:                                  # noqa: BLE001
            print(f"  site-wide /{path}/ unavailable, skipping backfill: {e}")
            continue
        fx, rs = parse_matches(BeautifulSoup(html, "html.parser"))
        for m in (rs if kind == "results" else fx):
            if "ballincollig" not in (m["home"] + m["away"]).lower():
                continue
            target, ambiguous = None, False
            for doc in docs.values():
                names = {r["team"] for r in doc["table"]}
                if m["home"] in names and m["away"] in names:
                    if target is not None:
                        ambiguous = True
                    target = doc
            if target is None or ambiguous:
                continue
            key = (m["date"], m["home"], m["away"])
            # A result may legitimately replace a fixture still listed as upcoming,
            # so only compare against the list it is going into.
            if any((x["date"], x["home"], x["away"]) == key for x in target[kind]):
                continue
            if kind == "results":
                # a played match must not also sit in the fixture list
                target["fixtures"] = [x for x in target["fixtures"]
                                      if (x["date"], x["home"], x["away"]) != key]
            elif any((x["date"], x["home"], x["away"]) == key for x in target["results"]):
                continue                      # already played, do not re-add as upcoming
            target[kind].append(m)
            added += 1
            print(f"  backfilled {kind[:-1]}: {m['date']} {m['home']} v {m['away']} "
                  f"-> {target['name']}")
        time.sleep(2)
    return added


def main():
    os.makedirs(OUT, exist_ok=True)
    failures, docs = [], {}
    sampled_ok = False          # one healthy page, for reference
    sampled_empty = 0           # and up to two that yielded nothing

    for cid, (name, route, only_ours) in COMPS.items():
        url = f"{BASE}/{route}/{cid}/"
        try:
            html = fetch(url)
        except Exception as e:                                  # noqa: BLE001
            print(f"FAIL {cid} {name}: {e}", file=sys.stderr)
            failures.append(cid)
            continue

        soup = BeautifulSoup(html, "html.parser")
        cols, rows = parse_table(soup)
        fixtures, results = parse_matches(soup)

        if only_ours:
            keep = lambda m: "ballincollig" in (m["home"] + m["away"]).lower()
            fixtures = [m for m in fixtures if keep(m)]
            results = [m for m in results if keep(m)]
            cols, rows = [], []

        ours = sorted({t for m in fixtures + results for t in (m["home"], m["away"])
                       if "ballincollig" in t.lower()} |
                      {r["team"] for r in rows if "ballincollig" in (r["team"] or "").lower()})

        doc = dict(compId=cid, name=name, route=route, url=url,
                   ballincolligTeams=ours, tableColumns=cols, table=rows,
                   fixtures=fixtures, results=results)
        if only_ours:
            doc["ballincolligOnly"] = True
        docs[cid] = doc

        nothing = not rows and not fixtures and not results
        if nothing and sampled_empty < 2:
            print(f"  nothing parsed from {cid}, dumping its structure:")
            sample_log(html, cid)
            sampled_empty += 1
        elif not sampled_ok and (rows or fixtures or results):
            sample_log(html, cid)
            sampled_ok = True

        flag = "" if (ours or only_ours) else "  <-- NO BALLINCOLLIG TEAM FOUND"
        if nothing:
            flag += "  <-- PARSED NOTHING"
        print(f"ok  {cid} {name[:44]:44s} table={len(rows):2d} fix={len(fixtures):3d} "
              f"res={len(results):3d} ours={ours}{flag}")
        time.sleep(2)

    n = sweep_site_wide(docs)
    print(f"Backfilled {n} match(es) the competition pages had dropped."
          if n else "Nothing needed backfilling.")

    for cid, doc in docs.items():
        doc["fixtures"].sort(key=lambda x: (x.get("date") or "", x.get("time") or ""))
        doc["results"].sort(key=lambda x: (x.get("date") or ""))
        with open(os.path.join(OUT, f"comp_{cid}.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)

    # Refuse to hand on a bad scrape rather than publishing an empty page.
    if len(failures) > 5:
        sys.exit(f"ABORT: {len(failures)} of {len(COMPS)} pages failed: {failures}")
    league_ids = [c for c, (_, r, _) in COMPS.items() if r == "league"]
    empty = []
    for c in league_ids:
        path = os.path.join(OUT, f"comp_{c}.json")
        if not os.path.exists(path):
            continue                      # already counted as a fetch failure
        if not json.load(open(path, encoding="utf-8"))["table"]:
            empty.append(c)
    if len(empty) > len(league_ids) // 2:
        sys.exit(f"ABORT: {len(empty)} league tables came back empty, the page layout has "
                 f"probably changed: {empty}")
    print(f"\nScraped {len(COMPS) - len(failures)}/{len(COMPS)} competitions.")


if __name__ == "__main__":
    main()
