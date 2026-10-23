"""Undelivered VINs past 6h SLA -> Slack.

Every run: fetch the live VIN list, render a summary card (PNG), write the full VIN list (CSV)
and post both to Slack in one message.

Data source (first one configured wins):
  LOCAL_CSV                          read a CSV already exported (testing)
  MB_URL + MB_API_KEY + MB_CARD_ID   Metabase saved question, via API key
  CH_URL + CH_USER + CH_PASSWORD     ClickHouse HTTP, runs undelivered_6h_live.sql
Slack: SLACK_TOKEN + CHANNEL_ID (if unset, files are only saved locally).
"""
import csv
import html
import io
import os
import urllib.request
from base64 import b64encode
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

IST = timezone(timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.environ.get("OUT_DIR", HERE)

# reason -> (colour, what it means / where to look)
REASONS = {
    "Not started processing":        ("#c2410c", "Never picked up by AI processing"),
    "Stuck in AI processing":        ("#b45309", "Processing started, never finished"),
    "Processing failed":             ("#b91c1c", "AI processing ended in Failed"),
    "Waiting for QC":                ("#7c3aed", "Processed, QC not started"),
    "In QC review":                  ("#6d28d9", "QC session open"),
    "On QC hold":                    ("#9333ea", "Parked on QC hold"),
    "QC done, FTP push missing":     ("#0369a1", "Rooftop expects a feed push, none sent"),
    "Processed, no delivery record": ("#475569", "Done, but no push, webhook or QC done logged"),
    "Waiting on dealer input":       ("#a16207", "Draft / not uploaded / insufficient assets"),
    "Other":                         ("#64748b", "Unclassified"),
}

CSV_COLUMNS = [  # (source column, CSV header)
    ("vin", "VIN"), ("sku_id", "SKU ID"), ("dealer_vin_id", "Dealer VIN ID"),
    ("reason", "Reason"), ("ageing_hrs", "VIN Ageing (hrs)"),
    ("current_status", "Current Status"), ("qc_status", "QC Status"),
    ("last_activity", "Last Activity"), ("last_activity_ist", "Last Activity (IST)"),
    ("hrs_since_last_activity", "Hrs Since Last Activity"),
    ("enterprise", "Enterprise"), ("enterprise_id", "Enterprise ID"),
    ("rooftop", "Rooftop"), ("team_id", "Team ID"),
    ("segment", "Segment"), ("region", "Region"), ("platform", "Platform"),
    ("qc_mode", "QC Mode"), ("input_source", "Input Source"),
    ("sku_created_ist", "SKU Created (IST)"), ("processing_start_ist", "Processing Start (IST)"),
    ("sla_due_ist", "SLA Due (IST)"), ("qc_done_ist", "QC Done (IST)"),
    ("ftp_push_expected", "FTP Push Expected"), ("last_known_push_ist", "Last Known Push (IST)"),
    ("had_input_wait_or_hold", "Had Input Wait / QC Hold"), ("frames", "Frames"),
]


# ---------------------------------------------------------------- data
def fetch_rows():
    if os.environ.get("LOCAL_CSV"):
        with open(os.environ["LOCAL_CSV"], newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    if os.environ.get("MB_API_KEY"):
        url = f"{os.environ['MB_URL'].rstrip('/')}/api/card/{os.environ['MB_CARD_ID']}/query/csv"
        req = urllib.request.Request(url, method="POST", headers={"x-api-key": os.environ["MB_API_KEY"]})
    else:
        with open(os.path.join(HERE, "undelivered_6h_live.sql"), encoding="utf-8") as f:
            sql = f.read().rstrip().rstrip(";") + "\nFORMAT CSVWithNames"
        auth = b64encode(f"{os.environ['CH_USER']}:{os.environ['CH_PASSWORD']}".encode()).decode()
        req = urllib.request.Request(os.environ["CH_URL"], data=sql.encode(), method="POST",
                                     headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- summary
def summarise(rows):
    by_reason = defaultdict(list)
    for r in rows:
        by_reason[r["reason"] if r["reason"] in REASONS else "Other"].append(r)
    table = []
    for reason, rs in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        top, top_n = Counter(r["enterprise"] or "Unknown" for r in rs).most_common(1)[0]
        table.append({
            "reason": reason, "n": len(rs),
            "over24": sum(num(r["ageing_hrs"]) > 24 for r in rs),
            "oldest": max(num(r["ageing_hrs"]) for r in rs),
            "top": top, "top_n": top_n,
        })
    seg = Counter(r["segment"] for r in rows)
    return {
        "total": len(rows),
        "over24": sum(num(r["ageing_hrs"]) > 24 for r in rows),
        "over48": sum(num(r["ageing_hrs"]) > 48 for r in rows),
        "enterprises": len({r["enterprise_id"] for r in rows}),
        "table": table,
        "segments": [(s, seg.get(s, 0)) for s in ("Ent", "Mid", "SMB", "Resellers") if seg.get(s)]
                    + [(s, n) for s, n in seg.items() if s not in ("Ent", "Mid", "SMB", "Resellers")],
    }


def pct(n, total):
    p = n / total * 100
    return "<1%" if 0 < p < 1 else f"{p:.0f}%"


def render_html(s, now):
    e = html.escape
    max_n = max([t["n"] for t in s["table"]] or [1])
    rows_html = "".join(f"""
      <tr>
        <td><div class="reason"><span class="dot" style="background:{REASONS[t['reason']][0]}"></span>
            <div><div class="rname">{e(t['reason'])}</div><div class="rdesc">{e(REASONS[t['reason']][1])}</div></div></div></td>
        <td class="num strong">{t['n']:,}</td>
        <td><div class="bar"><span style="width:{t['n'] / max_n * 100:.1f}%;background:{REASONS[t['reason']][0]}"></span></div>
            <div class="pct">{pct(t['n'], s['total'])}</div></td>
        <td class="num">{t['over24']:,}</td>
        <td class="num">{t['oldest']:.0f}h</td>
        <td class="top">{e(t['top'])} <span class="muted">· {t['top_n']}</span></td>
      </tr>""" for t in s["table"])
    seg_html = "".join(f'<span class="chip">{e(name)} <b>{n:,}</b></span>' for name, n in s["segments"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
  * {{ box-sizing: border-box; margin: 0; }}
  body {{ background: #eef1f4; font-family: -apple-system, "Segoe UI", Inter, Roboto, Helvetica, Arial, sans-serif; color: #0f172a; padding: 24px; }}
  .card {{ width: 1040px; background: #fff; border-radius: 14px; box-shadow: 0 1px 3px rgba(15,23,42,.08); overflow: hidden; }}
  .head {{ background: #12352f; color: #fff; padding: 22px 28px; display: flex; justify-content: space-between; align-items: flex-end; }}
  .head h1 {{ font-size: 22px; font-weight: 650; letter-spacing: -.01em; }}
  .head .sub {{ font-size: 13px; color: #b9d3cc; margin-top: 4px; }}
  .head .ts {{ font-size: 13px; color: #b9d3cc; text-align: right; }}
  .head .ts b {{ color: #fff; font-size: 15px; display: block; }}
  .kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); border-bottom: 1px solid #e5e9ee; }}
  .kpi {{ padding: 18px 28px; border-right: 1px solid #e5e9ee; }}
  .kpi:last-child {{ border-right: 0; }}
  .kpi .l {{ font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: .06em; font-weight: 600; }}
  .kpi .v {{ font-size: 30px; font-weight: 700; margin-top: 4px; font-variant-numeric: tabular-nums; }}
  .kpi.alert .v {{ color: #b91c1c; }}
  .body {{ padding: 8px 28px 6px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ font-size: 11.5px; color: #64748b; text-transform: uppercase; letter-spacing: .06em; font-weight: 600; text-align: left; padding: 12px 8px; border-bottom: 1px solid #e5e9ee; }}
  th.num {{ text-align: right; }}
  td {{ padding: 11px 8px; border-bottom: 1px solid #f0f2f5; font-size: 14px; vertical-align: middle; }}
  tr:last-child td {{ border-bottom: 0; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .strong {{ font-weight: 700; font-size: 15px; }}
  .reason {{ display: flex; gap: 10px; align-items: center; }}
  .dot {{ width: 10px; height: 10px; border-radius: 3px; flex: none; }}
  .rname {{ font-weight: 600; }}
  .rdesc {{ font-size: 12px; color: #64748b; margin-top: 1px; }}
  .bar {{ width: 150px; height: 8px; background: #f1f4f7; border-radius: 4px; overflow: hidden; display: inline-block; vertical-align: middle; }}
  .bar span {{ display: block; height: 100%; border-radius: 4px; }}
  .pct {{ display: inline-block; width: 38px; text-align: right; font-size: 12.5px; color: #475569; font-variant-numeric: tabular-nums; vertical-align: middle; }}
  .top {{ font-size: 13px; color: #334155; max-width: 230px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .muted {{ color: #94a3b8; }}
  .foot {{ display: flex; justify-content: space-between; align-items: center; padding: 14px 28px; background: #f8fafb; border-top: 1px solid #e5e9ee; font-size: 12.5px; color: #64748b; }}
  .chip {{ display: inline-block; background: #fff; border: 1px solid #e2e8f0; border-radius: 999px; padding: 3px 10px; margin-right: 6px; color: #334155; }}
  .chip b {{ color: #0f172a; }}
</style></head><body><div class="card">
  <div class="head">
    <div><h1>Undelivered VINs past 6h SLA</h1>
      <div class="sub">All segments · VINs created in the last 7 days · clock starts at processing start</div></div>
    <div class="ts"><b>{now:%d %b %Y, %H:%M} IST</b>Live snapshot</div>
  </div>
  <div class="kpis">
    <div class="kpi alert"><div class="l">Undelivered past 6h</div><div class="v">{s['total']:,}</div></div>
    <div class="kpi"><div class="l">Over 24 hours</div><div class="v">{s['over24']:,}</div></div>
    <div class="kpi"><div class="l">Over 48 hours</div><div class="v">{s['over48']:,}</div></div>
    <div class="kpi"><div class="l">Enterprises affected</div><div class="v">{s['enterprises']:,}</div></div>
  </div>
  <div class="body"><table>
    <tr><th>Reason</th><th class="num">VINs</th><th>Share</th><th class="num">Over 24h</th><th class="num">Oldest</th><th>Most affected enterprise</th></tr>
    {rows_html}
  </table></div>
  <div class="foot"><div>{seg_html}</div><div>Full VIN list attached as CSV</div></div>
</div></body></html>"""


def render_png(page_html, path):
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(device_scale_factor=2, viewport={"width": 1100, "height": 800})
        pg.set_content(page_html)
        pg.locator(".card").screenshot(path=path)
        b.close()


def write_csv(rows, path):
    order = {r: i for i, r in enumerate(t for t in REASONS)}
    rows = sorted(rows, key=lambda r: (order.get(r["reason"], 99), -num(r["ageing_hrs"])))
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads UTF-8
        w = csv.writer(f)
        w.writerow([h for _, h in CSV_COLUMNS])
        for r in rows:
            w.writerow(["" if r.get(c) in (None, "\\N") else r[c] for c, _ in CSV_COLUMNS])  # \N = ClickHouse NULL


# ---------------------------------------------------------------- main
def main():
    now = datetime.now(IST)
    rows = fetch_rows()
    s = summarise(rows)
    stamp = now.strftime("%Y-%m-%d_%H%M")
    png = os.path.join(OUT_DIR, f"undelivered_6h_summary_{stamp}IST.png")
    csv_path = os.path.join(OUT_DIR, f"undelivered_vins_6h_{stamp}IST.csv")
    write_csv(rows, csv_path)
    render_png(render_html(s, now), png)
    print(f"{s['total']} VINs; saved {png} and {csv_path}")

    token, channel = os.environ.get("SLACK_TOKEN"), os.environ.get("CHANNEL_ID")
    if not (token and channel):
        print("SLACK_TOKEN / CHANNEL_ID not set; not posting")
        return
    from slack_sdk import WebClient
    top = s["table"][0] if s["table"] else None
    text = (f"*Undelivered VINs past 6h SLA* · {now:%d %b, %H:%M} IST\n"
            f"*{s['total']:,}* VINs undelivered 6h+ after processing start "
            f"({s['over24']:,} over 24h)."
            + (f" Biggest bucket: *{top['reason']}* ({top['n']:,})." if top else "")
            + " Full VIN list in the CSV.")
    WebClient(token=token).files_upload_v2(
        channel=channel, initial_comment=text,
        file_uploads=[{"file": png, "title": "Summary"},
                      {"file": csv_path, "title": f"Undelivered VINs (6h+) {now:%d %b %H:%M} IST"}],
    )
    print("posted to Slack")


if __name__ == "__main__":
    main()
