#!/usr/bin/env python3
"""template.html + dataset.json -> index.html (the published page)."""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(HERE, "dataset.json"), encoding="utf-8"))

teams = d.get("teams", [])
if len(teams) < 10:
    sys.exit(f"ABORT: dataset has only {len(teams)} teams, refusing to publish over a good page.")
matches = sum(len(t["fixtures"]) + len(t["results"]) for t in teams)
if matches < 20:
    sys.exit(f"ABORT: only {matches} matches in the dataset, that is implausible.")

tpl = open(os.path.join(HERE, "template.html"), encoding="utf-8").read()
seed = json.dumps(d, ensure_ascii=False, separators=(",", ":")).replace("</script", "<\\/script")
body = tpl.replace("/*__SEED__*/null/*__END__*/", "/*SEED_START*/" + seed + "/*SEED_END*/")
if "__SEED__" in body:
    sys.exit("ABORT: template placeholder not found.")

m = re.match(r"\s*<title>(.*?)</title>\s*", body, re.S)
title, rest = (m.group(1), body[m.end():]) if m else ("Ballincollig RFC Match Centre", body)

html = (
    '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    f"<title>{title}</title>\n"
    '<meta name="description" content="Fixtures, results and league tables for every '
    'Ballincollig RFC side in Munster domestic rugby.">\n'
    '<meta property="og:title" content="Ballincollig RFC Match Centre">\n'
    '<meta property="og:description" content="Fixtures, results and tables for every '
    'Ballincollig side, plus an opponent scout.">\n'
    '<meta property="og:type" content="website">\n'
    '<style>html{color-scheme:light dark}body{margin:0}img{max-width:100%}'
    '[hidden]{display:none!important}</style>\n'
    "</head>\n<body>\n" + rest + "\n</body>\n</html>\n")

open(os.path.join(HERE, "index.html"), "w", encoding="utf-8").write(html)
print(f"index.html written: {len(html)} bytes, {len(teams)} teams, {matches} matches, "
      f"data as of {d.get('generatedAt')}")
