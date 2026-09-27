#!/usr/bin/env python3
"""Consolidate scraped SportLoMo competition JSON into one dataset for the
Ballincollig RFC app: teams, fixtures, results, tables, opponent scouting and
a green/amber/red difficulty rating per upcoming fixture."""

import json, glob, os, re, datetime, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "dataset.json")

# --- team definition: competition id -> which Ballincollig side plays in it ---
TEAMS = [
    dict(id="men1", name="Men's 1st XV", group="Senior Men", short="1st XV",
         comps=["216454", "216436", "217574", "216092"]),
    dict(id="men2", name="Men's 2nd XV", group="Senior Men", short="2nd XV",
         comps=["219174", "218733"]),
    dict(id="women", name="Women's XV", group="Senior Women", short="Women",
         comps=["218360"]),
    dict(id="bu185", name="Boys U18.5", group="Boys Youth", short="U18.5",
         comps=["219967", "219620", "216122"]),
    dict(id="bu16black", name="Boys U16 Black", group="Boys Youth", short="U16 Black",
         comps=["219989", "219625"]),
    dict(id="bu16white", name="Boys U16 White", group="Boys Youth", short="U16 White",
         comps=["219998", "219627"]),
    dict(id="bu14", name="Boys U14", group="Boys Youth", short="U14",
         comps=["220164", "220174", "216125"]),
    dict(id="bu13", name="Boys U13", group="Boys Youth", short="U13",
         comps=["216124"]),
    dict(id="gu16", name="Girls U16", group="Girls Youth", short="U16",
         comps=["216132"]),
    dict(id="gu185", name="Girls U18.5", group="Girls Youth", short="U18.5",
         comps=["216134"]),
]

HOME_GROUND = "Tanner Park"


def load_comps():
    comps = {}
    for f in sorted(glob.glob(os.path.join(DATA, "comp_*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        comps[str(d["compId"])] = d
    return comps


def is_ballincollig(name):
    return bool(name) and "ballincollig" in name.lower()


def norm(s):
    """Identity key for a specific TEAM.

    Deliberately keeps the XV number and colour suffix: 'Ballincollig RFC 1st XV'
    and 'Ballincollig RFC 2nd XV' are different sides and must never be merged,
    and the same is true of an opponent's 1st and 2nd XVs.
    """
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"\brugby (football )?club\b", " rfc ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def club_of(name):
    """Key used to decide whether two results are against the SAME opposition.

    Same as the identity key - a club's 1st and 2nd XV are different strengths,
    so treating them as one common opponent would corrupt the comparison.
    """
    return norm(name)


def match_key(m):
    return (m.get("date"), m.get("time"), m.get("home"), m.get("away"))


def build():
    comps = load_comps()
    today = datetime.date.today().isoformat()

    # ---- index every result by club, for form and common-opponent analysis ----
    # results_by_team[normalised team name] = list of result dicts w/ perspective
    results_by_team = {}

    def record(team, opp, tf, ta, date, venue, comp, compId):
        if tf is None or ta is None:
            outcome = None
            margin = None
        else:
            margin = tf - ta
            outcome = "W" if margin > 0 else ("L" if margin < 0 else "D")
        results_by_team.setdefault(norm(team), []).append(dict(
            team=team, opponent=opp, oppKey=club_of(opp), pf=tf, pa=ta,
            margin=margin, outcome=outcome, date=date, venue=venue,
            comp=comp, compId=compId,
            home=(venue == HOME_GROUND) if venue else None,
        ))

    for cid, c in comps.items():
        for r in c.get("results", []):
            record(r["home"], r["away"], r.get("homeScore"), r.get("awayScore"),
                   r.get("date"), r.get("venue"), c["name"], cid)
            record(r["away"], r["home"], r.get("awayScore"), r.get("homeScore"),
                   r.get("date"), r.get("venue"), c["name"], cid)

    for k in results_by_team:
        results_by_team[k].sort(key=lambda x: x.get("date") or "", reverse=True)

    # ---- table row lookup ----
    def table_row(compId, team):
        c = comps.get(compId) or {}
        for i, row in enumerate(c.get("table", [])):
            if norm(row.get("team")) == norm(team):
                r = dict(row)
                r["rowIndex"] = i + 1
                return r
        return None

    def form_summary(team, limit=5):
        rs = [r for r in results_by_team.get(norm(team), []) if r["outcome"]]
        rs = rs[:limit]
        if not rs:
            return dict(played=0, w=0, d=0, l=0, pf=0, pa=0, avgMargin=None,
                        winRate=None, last5=[])
        w = sum(1 for r in rs if r["outcome"] == "W")
        d = sum(1 for r in rs if r["outcome"] == "D")
        l = sum(1 for r in rs if r["outcome"] == "L")
        pf = sum(r["pf"] for r in rs)
        pa = sum(r["pa"] for r in rs)
        return dict(played=len(rs), w=w, d=d, l=l, pf=pf, pa=pa,
                    avgMargin=round((pf - pa) / len(rs), 1),
                    winRate=round(w / len(rs), 3),
                    last5=rs)

    # ---- difficulty rating ------------------------------------------------
    def rating(bTeam, oppTeam, compId, venue):
        """Weighted blend. Positive score = Ballincollig favoured."""
        signals = []          # (label, weight, value -1..1, detail)

        # 1. common opponents (heaviest)
        b_res = {r["oppKey"]: r for r in results_by_team.get(norm(bTeam), []) if r["margin"] is not None}
        o_res = {}
        for r in results_by_team.get(norm(oppTeam), []):
            if r["margin"] is not None and r["oppKey"] not in o_res:
                o_res[r["oppKey"]] = r
        shared = [k for k in b_res if k in o_res and k not in (club_of(bTeam), club_of(oppTeam))]
        common_detail = []
        if shared:
            diffs = []
            for k in shared:
                diff = b_res[k]["margin"] - o_res[k]["margin"]
                diffs.append(diff)
                common_detail.append(dict(
                    opponent=o_res[k]["opponent"], ballincolligMargin=b_res[k]["margin"],
                    opponentMargin=o_res[k]["margin"], diff=diff,
                    ballincolligScore=f'{b_res[k]["pf"]}-{b_res[k]["pa"]}',
                    opponentScore=f'{o_res[k]["pf"]}-{o_res[k]["pa"]}'))
            avg = statistics.mean(diffs)
            signals.append(("Common opponents", 0.45, max(-1, min(1, avg / 30.0)),
                            f"{len(shared)} shared opponent(s), average margin swing {avg:+.0f} pts"))

        # 2. league table standing
        br = table_row(compId, bTeam)
        orow = table_row(compId, oppTeam)
        if br and orow and (br.get("pld") or 0) > 0 and (orow.get("pld") or 0) > 0:
            bppg = (br.get("pts") or 0) / max(1, br.get("pld") or 1)
            oppg = (orow.get("pts") or 0) / max(1, orow.get("pld") or 1)
            signals.append(("League form", 0.25, max(-1, min(1, (bppg - oppg) / 4.0)),
                            f"{bppg:.1f} vs {oppg:.1f} league points per game"))

        # 3. recent form (last 5, any competition we hold)
        bf, of = form_summary(bTeam), form_summary(oppTeam)
        if bf["played"] and of["played"]:
            # Damp the weight when either side has only a game or two on record,
            # so one thumping win does not swing a fixture to red or green.
            damp = min(bf["played"], of["played"], 3) / 3.0
            signals.append(("Recent form", round(0.20 * damp, 3),
                            max(-1, min(1, (bf["avgMargin"] - of["avgMargin"]) / 30.0)),
                            f'Ballincollig {bf["w"]}W-{bf["d"]}D-{bf["l"]}L ({bf["avgMargin"]:+.0f} avg over '
                            f'{bf["played"]}), opponent {of["w"]}W-{of["d"]}D-{of["l"]}L '
                            f'({of["avgMargin"]:+.0f} avg over {of["played"]})'))

        # 4. home advantage
        if venue:
            at_home = venue == HOME_GROUND
            signals.append(("Venue", 0.10, 0.5 if at_home else -0.5,
                            "At Tanner Park" if at_home else f"Away at {venue}"))

        # A rating must rest on played rugby. Home advantage on its own is not
        # evidence, so a fixture with only the venue signal stays unrated.
        substantive = [s for s in signals if s[0] != "Venue"]
        if not substantive:
            return dict(band="grey", score=None, confidence="none",
                        signals=[dict(label=s[0], weight=s[1], value=round(s[2], 3), detail=s[3])
                                 for s in signals],
                        commonOpponents=[],
                        summary="Neither side has enough played matches on record yet to rate this one.")

        tw = sum(s[1] for s in signals)
        score = sum(s[1] * s[2] for s in signals) / tw
        heavy = any(s[0] == "Common opponents" for s in signals)
        conf = "high" if heavy and len(signals) >= 3 else ("medium" if len(signals) >= 3 or heavy else "low")
        if score >= 0.20:
            band, summary = "green", "Ballincollig should be favourites."
        elif score <= -0.20:
            band, summary = "red", "A hard one. Expect to be underdogs."
        else:
            band, summary = "yellow", "Line ball. Could go either way."
        if conf == "low":
            summary += " Based on limited data so far."
        return dict(band=band, score=round(score, 3), confidence=conf,
                    signals=[dict(label=s[0], weight=s[1], value=round(s[2], 3), detail=s[3]) for s in signals],
                    commonOpponents=common_detail, summary=summary)

    # ---- per-team assembly -------------------------------------------------
    out_teams = []
    for t in TEAMS:
        fixtures, results, tables = [], [], []
        opponents = {}
        our_in_comp = {}
        for cid in t["comps"]:
            c = comps.get(cid)
            if not c:
                continue
            bteams = [x for x in c.get("ballincolligTeams", [])]
            bset = {norm(x) for x in bteams}
            if bteams:
                our_in_comp[cid] = bteams[0]

            if c.get("table"):
                tables.append(dict(compId=cid, name=c["name"], url=c["url"],
                                   columns=c.get("tableColumns", []),
                                   rows=c["table"],
                                   ourTeams=bteams))

            for m in c.get("fixtures", []):
                if norm(m["home"]) in bset or norm(m["away"]) in bset:
                    ours = m["home"] if norm(m["home"]) in bset else m["away"]
                    opp = m["away"] if norm(m["home"]) in bset else m["home"]
                    at_home = (m.get("venue") == HOME_GROUND) if m.get("venue") else None
                    fixtures.append(dict(
                        date=m.get("date"), time=m.get("time"), comp=c["name"], compId=cid,
                        compUrl=c["url"], ourTeam=ours, opponent=opp, venue=m.get("venue"),
                        home=at_home, round=m.get("round"),
                        rating=rating(ours, opp, cid, m.get("venue"))))
                    opponents.setdefault(opp, dict(name=opp, compId=cid, comp=c["name"]))

            for m in c.get("results", []):
                if norm(m["home"]) in bset or norm(m["away"]) in bset:
                    we_home = norm(m["home"]) in bset
                    ours = m["home"] if we_home else m["away"]
                    opp = m["away"] if we_home else m["home"]
                    pf = m.get("homeScore") if we_home else m.get("awayScore")
                    pa = m.get("awayScore") if we_home else m.get("homeScore")
                    tf = m.get("homeTries") if we_home else m.get("awayTries")
                    ta = m.get("awayTries") if we_home else m.get("homeTries")
                    outcome = None
                    if pf is not None and pa is not None:
                        outcome = "W" if pf > pa else ("L" if pf < pa else "D")
                    results.append(dict(
                        date=m.get("date"), comp=c["name"], compId=cid, compUrl=c["url"],
                        ourTeam=ours, opponent=opp, pf=pf, pa=pa, triesFor=tf, triesAgainst=ta,
                        venue=m.get("venue"),
                        home=(m.get("venue") == HOME_GROUND) if m.get("venue") else None,
                        round=m.get("round"), outcome=outcome))
                    opponents.setdefault(opp, dict(name=opp, compId=cid, comp=c["name"]))

        fixtures.sort(key=lambda x: (x.get("date") or "9999", x.get("time") or ""))
        results.sort(key=lambda x: (x.get("date") or ""), reverse=True)

        # opponent scout cards
        scouts = []
        for name, meta in sorted(opponents.items()):
            f = form_summary(name)
            # head to head with any Ballincollig side
            h2h = [r for r in results if norm(r["opponent"]) == norm(name)]
            # next meeting
            nxt = next((x for x in fixtures if norm(x["opponent"]) == norm(name)), None)
            scouts.append(dict(
                name=name, comp=meta["comp"], compId=meta["compId"],
                tableRow=table_row(meta["compId"], name),
                form=f,
                headToHead=h2h,
                nextMeeting=nxt,
                rating=(nxt or {}).get("rating") or rating(
                    our_in_comp.get(meta["compId"], t["name"]),
                    name, meta["compId"], None)))

        played = [r for r in results if r["outcome"]]
        rec = dict(
            p=len(played),
            w=sum(1 for r in played if r["outcome"] == "W"),
            d=sum(1 for r in played if r["outcome"] == "D"),
            l=sum(1 for r in played if r["outcome"] == "L"),
            pf=sum(r["pf"] or 0 for r in played),
            pa=sum(r["pa"] or 0 for r in played))

        out_teams.append(dict(
            id=t["id"], name=t["name"], group=t["group"], short=t["short"],
            competitions=[dict(id=cid, name=comps[cid]["name"], url=comps[cid]["url"],
                               route=comps[cid].get("route"))
                          for cid in t["comps"] if cid in comps],
            record=rec, fixtures=fixtures, results=results, tables=tables,
            opponents=scouts))

    dataset = dict(
        club="Ballincollig RFC",
        homeGround=HOME_GROUND,
        season="2026/27",
        generatedAt=datetime.datetime.now().isoformat(timespec="seconds"),
        generatedDate=today,
        source="munsterrugby.sportlomo.com (the data engine behind munsterrugby.ie domestic fixtures & results)",
        teams=out_teams)

    json.dump(dataset, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")
    for t in out_teams:
        print(f'  {t["name"]:16s} comps={len(t["competitions"])} fix={len(t["fixtures"])} '
              f'res={len(t["results"])} tables={len(t["tables"])} opps={len(t["opponents"])} '
              f'record={t["record"]["w"]}W-{t["record"]["d"]}D-{t["record"]["l"]}L')


if __name__ == "__main__":
    build()
