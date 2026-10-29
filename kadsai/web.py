"""画面。標準ライブラリだけの小さな HTTP サーバー。社内 LAN か、中継（kurage.exbridge.jp/kadsai.php/ のような透過プロキシ）の後ろで動かす。
リンクはすべて相対パス（中継のサブパスの下でも動くように）。"""
import datetime as dt
import hashlib
import hmac
import html
import json
import os
import sqlite3
import threading
import urllib.parse
from collections import defaultdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, db, proposals as prop, report
from .report import KIND_LABEL, SEV_LABEL, _n, _p, _delta
from .util import ensure_dir, load_json, parse_date, today_jst, ymd

STATIC = os.path.join(os.path.dirname(__file__), "static")
e = html.escape
RUN = {"running": False, "log": "", "started": None, "finished": None, "error": None}

NAV = [("", "ダッシュボード"), ("report", "日報"), ("search-terms", "検索語"), ("keywords", "キーワード"),
       ("proposals", "提案と承認"), ("hours", "時間帯"), ("geo", "地域"), ("settings", "設定")]


# ---------------------------------------------------------------- data helpers
def _con(cfg):
    return db.connect(cfg.db)


def daily_series(cfg, days=30, end=None):
    end = end or (today_jst() - dt.timedelta(days=1))
    start = end - dt.timedelta(days=days - 1)
    con = _con(cfg)
    rows = db.q(con, """SELECT date, SUM(cost) cost, SUM(clicks) clicks, SUM(conversions) conversions, SUM(impressions) impressions
                        FROM daily_campaign WHERE customer_id=? AND date BETWEEN ? AND ? GROUP BY date ORDER BY date""",
                (cfg.customer_id, ymd(start), ymd(end)))
    con.close()
    by = {r["date"]: r for r in rows}
    out = []
    for i in range(days):
        d = ymd(start + dt.timedelta(days=i))
        r = by.get(d) or dict(date=d, cost=0, clicks=0, conversions=0, impressions=0)
        out.append(r)
    return out


def report_dates(cfg):
    d = os.path.join(cfg.outputs, "reports")
    if not os.path.isdir(d):
        return []
    return sorted([x for x in os.listdir(d) if os.path.exists(os.path.join(d, x, "findings.json"))], reverse=True)


def load_report(cfg, date):
    p = os.path.join(cfg.outputs, "reports", date, "findings.json")
    return load_json(p) if os.path.exists(p) else None


def last_fetch(cfg):
    con = _con(cfg)
    r = db.one(con, "SELECT * FROM fetch_log WHERE customer_id=? ORDER BY fetched_at DESC LIMIT 1", (cfg.customer_id,))
    con.close()
    return r


# ---------------------------------------------------------------- charts (inline SVG)
def svg_daily(series, unit):
    W, H, L, B, T = 900, 260, 56, 36, 16
    n = len(series)
    if n == 0:
        return "<p class=muted>データがありません。</p>"
    maxc = max((s["cost"] for s in series), default=0) or 1
    maxv = max((s["conversions"] for s in series), default=0) or 1
    bw = (W - L - 10) / n
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="日別の費用とコンバージョン">']
    for k in range(5):
        y = T + (H - T - B) * k / 4
        v = maxc * (1 - k / 4)
        out.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W - 10}" y2="{y:.1f}" stroke="#e3e8ee"/>'
                   f'<text x="{L - 6}" y="{y + 4:.1f}" font-size="11" text-anchor="end" fill="#5b6774">{v:,.0f}</text>')
    pts = []
    for i, s in enumerate(series):
        x = L + i * bw
        h = (H - T - B) * (s["cost"] / maxc)
        out.append(f'<rect x="{x + 1:.1f}" y="{H - B - h:.1f}" width="{max(bw - 2, 1):.1f}" height="{h:.1f}" fill="#9dbdf0"><title>{s["date"]} 費用{s["cost"]:,.0f}{unit} クリック{s["clicks"]} CV{s["conversions"]:g}</title></rect>')
        cy = H - B - (H - T - B) * (s["conversions"] / maxv)
        pts.append((x + bw / 2, cy, s["conversions"]))
        if i % max(1, n // 10) == 0 or i == n - 1:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{H - B + 16}" font-size="11" text-anchor="middle" fill="#5b6774">{s["date"][5:]}</text>')
    if any(p[2] for p in pts):
        out.append('<polyline fill="none" stroke="#c0392b" stroke-width="2" points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in pts) + '"/>')
        for x, y, v in pts:
            if v:
                out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#c0392b"><title>CV {v:g}</title></circle>')
    out.append(f'<text x="{L}" y="{H - 4}" font-size="11" fill="#5b6774">棒＝費用（{unit}）　赤線＝コンバージョン</text></svg>')
    return "".join(out)


# ---------------------------------------------------------------- page shell
def shell(cfg, title, body, active="", notice=None):
    nav = "".join(f'<a href="{k or "."}" class="{"on" if k == active else ""}">{v}</a>' for k, v in NAV)
    acct = f"<small>{e(cfg.account_name)}</small>" if cfg.account_name else ""
    n = f'<div class="notice {"ok" if notice[0] == "ok" else ""}">{e(notice[1])}</div>' if notice else ""
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{e(title)} | Kurage 広告AI分析</title><link rel="stylesheet" href="static/style.css"><link rel="icon" href="static/mascot.png"></head>
<body><header class="top"><div class="topin"><a class="brand" href="."><img src="static/logo-mark-128.png" width="36" height="36" alt="株式会社エクスブリッジ"><img class="mascot" src="static/mascot.png" width="34" height="38" alt="Kurage">Kurage 広告AI分析{acct}</a><nav class="tabs">{nav}</nav></div></header>
<main>{n}{body}</main>
<footer class="foot">Kurage 広告AI分析 kadsai {__version__}。数字は Google 広告の実績から規則で集計したものです。AI の要点は、その数字の言い換えです。設定の変更は承認したものだけ実行します。</footer></body></html>"""


def kpi(label, value, sub=""):
    return f"<div><small>{e(label)}</small><b>{value}</b><small>{sub}</small></div>"


def delta_html(cur, prev):
    if not prev:
        return ""
    r = cur / prev - 1
    cls = "up" if r >= 0 else "down"
    return f'<span class="{cls}">{"+" if r >= 0 else ""}{r * 100:.0f}%</span> 前の期間比'


# ---------------------------------------------------------------- pages
def page_dashboard(cfg):
    u = cfg.currency
    dates = report_dates(cfg)
    latest = load_report(cfg, dates[0]) if dates else None
    s30 = daily_series(cfg, 30)
    yday = s30[-1]
    cur7, prev7 = s30[-7:], s30[-14:-7]
    agg = lambda rows: dict(cost=sum(r["cost"] for r in rows), clicks=sum(r["clicks"] for r in rows), conversions=sum(r["conversions"] for r in rows))
    c7, p7, c30 = agg(cur7), agg(prev7), agg(s30)
    cpa = lambda a: (f"{_n(a['cost'] / a['conversions'])}{u}" if a["conversions"] else "-")
    body = [f"<h1>ダッシュボード <span class=sub>{e(yday['date'])} までの実績</span></h1>"]
    body.append("<h2>昨日</h2><div class=kpi>" + kpi("費用", f"{_n(yday['cost'])}{u}") + kpi("クリック", _n(yday["clicks"])) + kpi("表示", _n(yday["impressions"])) + kpi("コンバージョン", _n(yday["conversions"])) + "</div>")
    body.append("<h2>直近7日</h2><div class=kpi>" + kpi("費用", f"{_n(c7['cost'])}{u}", delta_html(c7["cost"], p7["cost"])) + kpi("クリック", _n(c7["clicks"]), delta_html(c7["clicks"], p7["clicks"]))
                + kpi("クリック単価", f"{_n(c7['cost'] / c7['clicks']) if c7['clicks'] else '-'}{u}") + kpi("コンバージョン", _n(c7["conversions"]), delta_html(c7["conversions"], p7["conversions"])) + kpi("1件あたり費用", cpa(c7)) + "</div>")
    body.append("<h2>直近30日</h2><div class=kpi>" + kpi("費用", f"{_n(c30['cost'])}{u}") + kpi("クリック", _n(c30["clicks"])) + kpi("コンバージョン", _n(c30["conversions"])) + kpi("1件あたり費用", cpa(c30)) + "</div>")
    body.append(f"<div class='card chart' style='margin-top:12px'><h3 style='margin-top:0'>日別の費用とコンバージョン（30日）</h3>{svg_daily(s30, u)}</div>")
    body.append("<div class='grid g2' style='margin-top:12px'>")
    # 指摘
    if latest:
        fs = latest["findings"]
        imp = [f for f in fs if f["severity"] in ("high", "mid")]
        body.append(f"<div class=card><h3 style='margin-top:0'>指摘（{e(dates[0])}・{len(fs)}件、重要{len(imp)}件）</h3>")
        if latest.get("ai"):
            body.append(f"<div class=ai>{e(latest['ai'])}</div>")
        for f in fs[:6]:
            body.append(f"<p><span class='tag {f['severity']}'>{SEV_LABEL[f['severity']]}</span>{e(f['title'])}</p>")
        body.append(f"<p><a class='btn sec sm' href='report?date={e(dates[0])}'>日報を全部見る</a></p></div>")
    else:
        body.append("<div class=card><h3 style='margin-top:0'>指摘</h3><p class=muted>まだ日報がありません。「設定」から今すぐ実行できます。</p></div>")
    # キャンペーン
    body.append("<div class=card><h3 style='margin-top:0'>キャンペーン別（直近7日）</h3>")
    if latest and latest["summary"].get("campaigns"):
        body.append("<div class=tw><table><tr><th>キャンペーン</th><th class=n>費用</th><th class=n>クリック</th><th class=n>CV</th><th>状態</th></tr>")
        for c in latest["summary"]["campaigns"]:
            body.append(f"<tr><td>{e(c['campaign_name'])}</td><td class=n>{_n(c['cost'])}</td><td class=n>{_n(c['clicks'])}</td><td class=n>{_n(c['conversions'])}</td><td><span class=status>{e(c['status'])}</span></td></tr>")
        body.append("</table></div>")
    else:
        body.append("<p class=muted>データがありません。</p>")
    body.append("</div></div>")
    pend = [i for i in prop.load(cfg) if i["status"] == "pending"]
    lf = last_fetch(cfg)
    body.append("<div class='grid g2' style='margin-top:12px'>")
    body.append(f"<div class=card><h3 style='margin-top:0'>承認待ちの提案</h3><p><b style='font-size:22px'>{len(pend)}</b> 件　<a class='btn sm' href='proposals'>見る・承認する</a></p><p class=sub>除外キーワードの追加や予算の変更は、ここで承認したものだけ実行します。</p></div>")
    body.append(f"<div class=card><h3 style='margin-top:0'>データの取り込み</h3><p>最終取得: {e(lf['fetched_at'][:16].replace('T', ' ')) if lf else '未取得'}　"
                f"<span class=sub>{e(lf['date_from'] + '〜' + lf['date_to']) if lf and lf['date_from'] else ''}</span></p>"
                f"<form method=post action='run'><button class='btn sm' {'disabled' if RUN['running'] else ''}>今すぐ取得して分析{'（実行中）' if RUN['running'] else ''}</button></form></div>")
    body.append("</div>")
    return shell(cfg, "ダッシュボード", "".join(body), "")


def page_report(cfg, qs):
    dates = report_dates(cfg)
    date = qs.get("date", [dates[0] if dates else ""])[0]
    r = load_report(cfg, date) if date else None
    sel = "".join(f'<option value="{e(d)}" {"selected" if d == date else ""}>{e(d)}</option>' for d in dates)
    head = f"<h1>日報</h1><form class=inline method=get action='report'><select name=date onchange='this.form.submit()'>{sel}</select></form>"
    if not r:
        return shell(cfg, "日報", head + "<p class=muted>日報がありません。</p>", "report")
    links = f" <a class='btn sec sm' href='report-file?date={e(date)}&f=report.html' target=_blank>HTMLで開く</a> <a class='btn sec sm' href='report-file?date={e(date)}&f=report.md'>Markdown</a> <a class='btn sec sm' href='report-file?date={e(date)}&f=short.txt'>LINE用の短文</a>"
    body = head + links + "<div style='margin-top:8px'>" + report.html_body(cfg, r["summary"], r["findings"], r.get("ai")) + "</div>"
    return shell(cfg, f"日報 {date}", body, "report")


def page_search_terms(cfg, qs, notice=None):
    u = cfg.currency
    days = int(qs.get("days", ["30"])[0])
    sort = qs.get("sort", ["cost"])[0]
    only = qs.get("only", [""])[0]
    end = today_jst() - dt.timedelta(days=1)
    con = _con(cfg)
    rows = db.q(con, f"""SELECT campaign_id, search_term, SUM(impressions) impressions, SUM(clicks) clicks, SUM(cost) cost, SUM(conversions) conversions
                        FROM daily_search_term WHERE customer_id=? AND date BETWEEN ? AND ? GROUP BY campaign_id, search_term""",
                (cfg.customer_id, ymd(end - dt.timedelta(days=days - 1)), ymd(end)))
    cn = {r["campaign_id"]: r["campaign_name"] for r in db.q(con, "SELECT campaign_id, campaign_name FROM daily_campaign WHERE customer_id=? GROUP BY campaign_id", (cfg.customer_id,))}
    negs = {(r["campaign_id"], r["keyword"].lower()) for r in db.q(con, "SELECT campaign_id, keyword FROM negative_keyword WHERE customer_id=?", (cfg.customer_id,))}
    con.close()
    pend = {(i["params"].get("campaign_id"), str(i["params"].get("keyword", "")).lower()) for i in prop.load(cfg) if i["kind"] == "add_negative_keyword" and i["status"] in ("pending", "applied")}
    if only == "zero":
        rows = [r for r in rows if r["conversions"] == 0]
    elif only == "conv":
        rows = [r for r in rows if r["conversions"] > 0]
    key = {"cost": lambda r: -r["cost"], "clicks": lambda r: -r["clicks"], "conv": lambda r: (-r["conversions"], r["cost"]), "term": lambda r: r["search_term"]}[sort if sort in ("cost", "clicks", "conv", "term") else "cost"]
    rows.sort(key=key)
    tot = dict(cost=sum(r["cost"] for r in rows), clicks=sum(r["clicks"] for r in rows), conversions=sum(r["conversions"] for r in rows))
    opt = lambda v, l, cur: f'<option value="{v}" {"selected" if v == cur else ""}>{l}</option>'
    body = [f"<h1>検索語 <span class=sub>実際に検索された言葉。{len(rows)}語・費用{_n(tot['cost'])}{u}・クリック{_n(tot['clicks'])}・CV{_n(tot['conversions'])}</span></h1>",
            f"<form method=get action='search-terms' class=card style='display:flex;gap:8px;flex-wrap:wrap;align-items:center'>期間 <select name=days>{opt('7','7日',str(days))}{opt('14','14日',str(days))}{opt('30','30日',str(days))}{opt('90','90日',str(days))}</select> "
            f"絞り込み <select name=only>{opt('','すべて',only)}{opt('zero','成果ゼロだけ',only)}{opt('conv','成果ありだけ',only)}</select> 並び <select name=sort>{opt('cost','費用',sort)}{opt('clicks','クリック',sort)}{opt('conv','CV',sort)}{opt('term','言葉',sort)}</select> <button class='btn sm'>表示</button></form>",
            "<div class=tw><table><tr><th>検索語</th><th>キャンペーン</th><th class=n>表示</th><th class=n>クリック</th><th class=n>費用</th><th class=n>CV</th><th class=n>1件あたり</th><th>除外</th></tr>"]
    for r in rows[:300]:
        k = (r["campaign_id"], r["search_term"].lower())
        if k in negs:
            act = "<span class=status>除外済み</span>"
        elif k in pend:
            act = "<span class='status st-pending'>提案済み</span>"
        else:
            act = (f"<form class=inline method=post action='proposals'><input type=hidden name=action value=new><input type=hidden name=kind value=add_negative_keyword>"
                   f"<input type=hidden name=campaign_id value='{r['campaign_id']}'><input type=hidden name=keyword value='{e(r['search_term'])}'>"
                   f"<input type=hidden name=reason value='検索語ページから。直近{days}日 費用{r['cost']:.0f}{u}・クリック{r['clicks']}・CV{r['conversions']:g}'><button class='btn sec sm'>除外候補に</button></form>")
        cpa = f"{_n(r['cost'] / r['conversions'])}" if r["conversions"] else "-"
        body.append(f"<tr><td>{e(r['search_term'])}</td><td class=sub>{e(cn.get(r['campaign_id'], str(r['campaign_id'])))}</td><td class=n>{_n(r['impressions'])}</td><td class=n>{_n(r['clicks'])}</td><td class=n>{_n(r['cost'])}</td><td class=n>{_n(r['conversions'])}</td><td class=n>{cpa}</td><td>{act}</td></tr>")
    body.append("</table></div><p class=sub>「除外候補に」を押すと提案に入ります。実際に除外するのは「提案と承認」で承認したときです。</p>")
    return shell(cfg, "検索語", "".join(body), "search-terms", notice)


def page_keywords(cfg, qs):
    u = cfg.currency
    days = int(qs.get("days", ["30"])[0])
    end = today_jst() - dt.timedelta(days=1)
    con = _con(cfg)
    rows = db.q(con, """SELECT adgroup_id, criterion_id, keyword, match_type, MAX(status) status, MAX(quality_score) quality_score,
                        MAX(first_page_cpc) first_page_cpc, MAX(cpc_bid) cpc_bid,
                        SUM(impressions) impressions, SUM(clicks) clicks, SUM(cost) cost, SUM(conversions) conversions
                        FROM daily_keyword WHERE customer_id=? AND date BETWEEN ? AND ? GROUP BY adgroup_id, criterion_id ORDER BY cost DESC, impressions DESC""",
                (cfg.customer_id, ymd(end - dt.timedelta(days=days - 1)), ymd(end)))
    an = {r["adgroup_id"]: r["adgroup_name"] for r in db.q(con, "SELECT adgroup_id, adgroup_name FROM daily_adgroup WHERE customer_id=? GROUP BY adgroup_id", (cfg.customer_id,))}
    con.close()
    body = [f"<h1>キーワード <span class=sub>入札している言葉。直近{days}日・{len(rows)}語</span></h1>",
            "<div class=tw><table><tr><th>キーワード</th><th>一致</th><th>広告グループ</th><th class=n>品質</th><th class=n>入札</th><th class=n>1ページ目目安</th><th class=n>表示</th><th class=n>クリック</th><th class=n>費用</th><th class=n>CV</th><th>状態</th></tr>"]
    for r in rows:
        q = r["quality_score"]
        qh = f"<span class='{'down' if q and q <= 4 else ''}'>{q}</span>" if q else "-"
        bid = f"{_n(r['cpc_bid'])}" if r["cpc_bid"] else "-"
        fp = f"<span class='{'down' if r['first_page_cpc'] and r['cpc_bid'] and r['cpc_bid'] < r['first_page_cpc'] else ''}'>{_n(r['first_page_cpc'])}</span>" if r["first_page_cpc"] else "-"
        body.append(f"<tr><td>{e(r['keyword'])}</td><td class=sub>{e(r['match_type'])}</td><td class=sub>{e(an.get(r['adgroup_id'], ''))}</td><td class=n>{qh}</td><td class=n>{bid}</td><td class=n>{fp}</td><td class=n>{_n(r['impressions'])}</td><td class=n>{_n(r['clicks'])}</td><td class=n>{_n(r['cost'])}</td><td class=n>{_n(r['conversions'])}</td><td><span class=status>{e(r['status'])}</span></td></tr>")
    if not rows:
        body.append("<tr><td colspan=11 class=muted>キーワードのデータがありません（ショッピングや P-MAX の口座にはキーワードがありません）。</td></tr>")
    body.append("</table></div><p class=sub>品質は10点満点。赤は4以下。「1ページ目目安」が赤は、入札がその目安に届いていません。</p>")
    return shell(cfg, "キーワード", "".join(body), "keywords")


def page_proposals(cfg, qs, notice=None):
    items = prop.load(cfg)
    show_all = qs.get("all", [""])[0] == "1"
    rows = [i for i in items if show_all or i["status"] == "pending"]
    rows.sort(key=lambda i: -i["id"])
    body = [f"<h1>提案と承認 <span class=sub>承認したものだけ Google 広告に反映します</span></h1>",
            f"<p><a class='btn sec sm' href='proposals{'' if show_all else '?all=1'}'>{'承認待ちだけ' if show_all else '履歴も見る'}</a></p>"]
    if not rows:
        body.append("<div class=card><p class=muted>承認待ちの提案はありません。</p></div>")
    for it in rows:
        p = it["params"]
        label = {"add_negative_keyword": f"除外キーワードを追加：「{p.get('keyword')}」（{p.get('match_type', 'EXACT')}）",
                 "raise_budget": f"日予算を {p.get('ratio', 1.2):.0%} に（キャンペーン {p.get('campaign_id')}）",
                 "pause_keyword": "キーワードを停止", "pause_campaign": "キャンペーンを停止"}.get(it["kind"], it["kind"])
        act = ""
        if it["status"] == "pending":
            act = (f"<form class=inline method=post action='proposals' onsubmit=\"return confirm('Google 広告に反映します。よろしいですか？')\"><input type=hidden name=action value=apply><input type=hidden name=id value={it['id']}><button class='btn sm'>承認して実行</button></form> "
                   f"<form class=inline method=post action='proposals'><input type=hidden name=action value=reject><input type=hidden name=id value={it['id']}><button class='btn sec sm'>却下</button></form>")
        res = f"<p class=sub>結果: {e(str(it.get('result', '')))}</p>" if it.get("result") else ""
        body.append(f"<div class=f><span class='status st-{it['status']}'>{ {'pending': '承認待ち', 'applied': '実行済み', 'rejected': '却下', 'failed': '失敗'}.get(it['status'], it['status']) }</span> <span class=sub>#{it['id']}・{e(it['date'])}</span>"
                    f"<h3>{e(label)}</h3><p>{e(it['reason'])}</p>{res}<p>{act}</p></div>")
    return shell(cfg, "提案と承認", "".join(body), "proposals", notice)


def page_hours(cfg, qs):
    u = cfg.currency
    days = int(qs.get("days", ["30"])[0])
    end = today_jst() - dt.timedelta(days=1)
    con = _con(cfg)
    rows = db.q(con, "SELECT date, hour, SUM(cost) cost, SUM(clicks) clicks, SUM(conversions) conversions FROM daily_hour WHERE customer_id=? AND date BETWEEN ? AND ? GROUP BY date, hour",
                (cfg.customer_id, ymd(end - dt.timedelta(days=days - 1)), ymd(end)))
    con.close()
    grid = defaultdict(lambda: dict(cost=0.0, clicks=0, conversions=0.0))
    for r in rows:
        wd = dt.date.fromisoformat(r["date"]).weekday()
        g = grid[(wd, r["hour"])]
        g["cost"] += r["cost"] or 0
        g["clicks"] += r["clicks"] or 0
        g["conversions"] += r["conversions"] or 0
    mx = max((g["cost"] for g in grid.values()), default=0) or 1
    body = [f"<h1>時間帯 <span class=sub>曜日×時間帯の費用（直近{days}日）。数字はコンバージョン</span></h1>", "<div class=tw><table class=heat><tr><th></th>" + "".join(f"<th>{h}</th>" for h in range(24)) + "</tr>"]
    for wd, name in enumerate("月火水木金土日"):
        body.append(f"<tr><th>{name}</th>")
        for h in range(24):
            g = grid.get((wd, h))
            if not g or g["cost"] == 0:
                body.append("<td></td>")
                continue
            a = 0.12 + 0.88 * (g["cost"] / mx)
            body.append(f"<td style='background:rgba(47,111,209,{a:.2f});color:{'#fff' if a > 0.55 else '#1f2a37'}' title='{name} {h}時 費用{g['cost']:.0f}{u} クリック{g['clicks']} CV{g['conversions']:g}'>{g['conversions']:g}</td>")
        body.append("</tr>")
    body.append("</table></div><p class=sub>色が濃いほど費用が多い時間帯。費用が多いのにコンバージョンが0の時間帯は、広告スケジュールで外す候補です。</p>")
    return shell(cfg, "時間帯", "".join(body), "hours")


def page_geo(cfg, qs):
    u = cfg.currency
    days = int(qs.get("days", ["30"])[0])
    end = today_jst() - dt.timedelta(days=1)
    con = _con(cfg)
    rows = db.q(con, "SELECT geo_name, SUM(impressions) impressions, SUM(clicks) clicks, SUM(cost) cost, SUM(conversions) conversions FROM daily_geo WHERE customer_id=? AND date BETWEEN ? AND ? GROUP BY geo_id ORDER BY cost DESC",
                (cfg.customer_id, ymd(end - dt.timedelta(days=days - 1)), ymd(end)))
    con.close()
    body = [f"<h1>地域 <span class=sub>市区町村まで分かった分（直近{days}日）</span></h1>", "<div class=tw><table><tr><th>地域</th><th class=n>表示</th><th class=n>クリック</th><th class=n>費用</th><th class=n>CV</th><th class=n>1件あたり</th></tr>"]
    for r in rows:
        body.append(f"<tr><td>{e(r['geo_name'] or '-')}</td><td class=n>{_n(r['impressions'])}</td><td class=n>{_n(r['clicks'])}</td><td class=n>{_n(r['cost'])}</td><td class=n>{_n(r['conversions'])}</td><td class=n>{_n(r['cost'] / r['conversions']) if r['conversions'] else '-'}</td></tr>")
    if not rows:
        body.append("<tr><td colspan=6 class=muted>地域のデータがありません。</td></tr>")
    body.append("</table></div>")
    return shell(cfg, "地域", "".join(body), "geo")


def page_settings(cfg, notice=None):
    raw = json.loads(json.dumps(cfg.raw))
    for k in ("web",):
        if k in raw and "password" in raw[k]:
            raw[k]["password"] = "（伏せ）"
    body = [f"<h1>設定</h1><div class='grid g2'>",
            f"<div class=card><h3 style='margin-top:0'>今すぐ実行</h3><p>Google 広告から取り込み、規則で分析し、日報を作ります（AI 要約は設定で有効なときだけ）。通知は送りません。</p>"
            f"<form method=post action='run'><button class=btn {'disabled' if RUN['running'] else ''}>{'実行中…' if RUN['running'] else '取得して分析'}</button></form>"
            + (f"<p class=sub>前回: {e(str(RUN['finished'] or RUN['started']))} {('エラー: ' + e(str(RUN['error']))) if RUN['error'] else ''}</p>" if RUN["started"] else "") + "</div>",
            f"<div class=card><h3 style='margin-top:0'>毎朝の自動実行</h3><p>サーバーの cron か systemd timer で <code>python3 -m kadsai run</code> を毎朝動かします。例は examples/ にあります。</p><p class=sub>AI: {'有効' if cfg.ai.get('enabled') else '無効'}（{e(cfg.ai.get('command') or cfg.ai.get('openai_base_url') or '-')}）　LINE: {'有効' if cfg.line.get('enabled') else '無効'}　メール: {'有効' if cfg.email.get('enabled') else '無効'}</p></div>",
            "</div><h2>設定ファイル（kadsai.json）</h2><div class=card><pre style='white-space:pre-wrap;margin:0;font-size:13px'>" + e(json.dumps(raw, ensure_ascii=False, indent=1)) + "</pre></div>",
            "<h2>閾値の意味</h2><div class=card><div class=tw><table><tr><th>項目</th><th>意味</th></tr>"]
    desc = {"anomaly_weeks": "同じ曜日を何週さかのぼって平均するか", "anomaly_threshold": "平均との差がこの割合を超えたら「急な増減」（0.5＝±50%）", "anomaly_min_cost": "費用がこれ未満の日は急な増減を言わない",
            "anomaly_min_clicks": "クリックがこれ未満の日は急な増減を言わない", "search_term_days": "検索語の集計期間（日）", "wasted_min_cost": "成果ゼロでこの費用を超えた検索語を除外候補に",
            "wasted_min_clicks": "成果ゼロでこのクリックを超えた検索語を除外候補に", "ngram_min_cost": "語単位で成果ゼロのままこの費用を超えたら指摘", "is_days": "インプレッションシェアの集計期間（日）",
            "is_lost_budget": "予算が理由で逃した割合がこれを超えたら指摘", "is_lost_rank": "順位が理由で逃した割合がこれを超えたら指摘", "quality_score_low": "この品質スコア以下を指摘",
            "report_days": "日報の「直近N日」", "ad_min_impressions": "広告文の比較に必要な表示回数", "hour_days": "時間帯分析の期間（日）"}
    for k, v in cfg.rules.items():
        body.append(f"<tr><td><code>{e(k)}</code> = {e(str(v))}</td><td>{e(desc.get(k, ''))}</td></tr>")
    body.append("</table></div></div>")
    return shell(cfg, "設定", "".join(body), "settings", notice)


def page_login(cfg, bad=False):
    body = f"""<div class='card login'><h1 style='margin-top:0'>Kurage 広告AI分析</h1><p class=sub>パスワードを入れてください。</p>
<form method=post action='login'><p><input type=password name=password autofocus required style='width:100%'></p>{'<p class=down>パスワードが違います。</p>' if bad else ''}<p><button class=btn style='width:100%'>ログイン</button></p></form></div>"""
    return shell(cfg, "ログイン", body, "")


# ---------------------------------------------------------------- run job
def start_run(cfg):
    if RUN["running"]:
        return False
    RUN.update(running=True, started=dt.datetime.now().isoformat(timespec="seconds"), finished=None, error=None)

    def job():
        try:
            from .cli import _analyze
            from .fetch import fetch
            date = today_jst() - dt.timedelta(days=1)
            fetch(cfg, days=35, date_to=date)
            _analyze(cfg, date, with_ai=bool(cfg.ai.get("enabled")))
        except Exception as ex:  # noqa
            RUN["error"] = str(ex)[:300]
        finally:
            RUN["running"] = False
            RUN["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    threading.Thread(target=job, daemon=True).start()
    return True


# ---------------------------------------------------------------- server
def make_handler(cfg):
    web = cfg.raw.get("web", {}) or {}
    password = web.get("password") or os.environ.get(web.get("password_env", "KADSAI_WEB_PASSWORD"), "")
    secret = hashlib.sha256(("kadsai|" + password + "|" + cfg.customer_id).encode()).hexdigest()
    token = hmac.new(secret.encode(), b"ok", hashlib.sha256).hexdigest()

    class H(BaseHTTPRequestHandler):
        server_version = "kadsai/" + __version__

        def log_message(self, fmt, *a):
            pass

        # ---- helpers
        def _authed(self):
            if not password:
                return True
            c = self.headers.get("Cookie", "")
            return f"kadsai_s={token}" in c

        def _send(self, body, status=200, ctype="text/html; charset=utf-8", extra=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or []):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, to, extra=None):
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", to)
            self.send_header("Content-Length", "0")
            for k, v in (extra or []):
                self.send_header(k, v)
            self.end_headers()

        def _path(self):
            u = urllib.parse.urlsplit(self.path)
            return u.path.strip("/"), urllib.parse.parse_qs(u.query)

        def _form(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8") if n else ""
            return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

        # ---- GET
        def do_GET(self):
            path, qs = self._path()
            if path.startswith("static/"):
                return self._static(path[7:])
            if path == "login":
                return self._send(page_login(cfg))
            if not self._authed():
                return self._redirect("login")
            try:
                if path == "":
                    return self._send(page_dashboard(cfg))
                if path == "report":
                    return self._send(page_report(cfg, qs))
                if path == "report-file":
                    date = qs.get("date", [""])[0]
                    f = qs.get("f", ["report.html"])[0]
                    if f not in ("report.html", "report.md", "short.txt") or not date.replace("-", "").isdigit():
                        return self._send("not found", 404, "text/plain")
                    p = os.path.join(cfg.outputs, "reports", date, f)
                    if not os.path.exists(p):
                        return self._send("not found", 404, "text/plain")
                    ct = "text/html; charset=utf-8" if f.endswith(".html") else "text/plain; charset=utf-8"
                    return self._send(open(p, "rb").read(), 200, ct)
                if path == "search-terms":
                    return self._send(page_search_terms(cfg, qs))
                if path == "keywords":
                    return self._send(page_keywords(cfg, qs))
                if path == "proposals":
                    return self._send(page_proposals(cfg, qs))
                if path == "hours":
                    return self._send(page_hours(cfg, qs))
                if path == "geo":
                    return self._send(page_geo(cfg, qs))
                if path == "settings":
                    return self._send(page_settings(cfg))
                if path == "logout":
                    return self._redirect("login", [("Set-Cookie", "kadsai_s=; Max-Age=0; Path=/")])
                if path == "api/summary.json":
                    dates = report_dates(cfg)
                    return self._send(json.dumps(load_report(cfg, dates[0]) if dates else {}, ensure_ascii=False), 200, "application/json; charset=utf-8")
                return self._send(shell(cfg, "見つかりません", "<h1>ページがありません</h1><p><a href='.'>ダッシュボードへ</a></p>"), 404)
            except Exception as ex:  # noqa
                return self._send(shell(cfg, "エラー", f"<h1>エラー</h1><pre>{e(str(ex))}</pre>"), 500)

        def _static(self, name):
            p = os.path.join(STATIC, os.path.basename(name))
            if not os.path.isfile(p):
                return self._send("not found", 404, "text/plain")
            ct = {"css": "text/css; charset=utf-8", "png": "image/png", "svg": "image/svg+xml", "js": "text/javascript"}.get(p.rsplit(".", 1)[-1], "application/octet-stream")
            self._send(open(p, "rb").read(), 200, ct, [("Cache-Control", "public, max-age=3600")])

        # ---- POST
        def do_POST(self):
            path, qs = self._path()
            form = self._form()
            if path == "login":
                if password and hmac.compare_digest(form.get("password", ""), password):
                    return self._redirect(".", [("Set-Cookie", f"kadsai_s={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=2592000")])
                return self._send(page_login(cfg, bad=True), 401)
            if not self._authed():
                return self._redirect("login")
            if path == "run":
                started = start_run(cfg)
                return self._send(page_settings(cfg, ("ok", "取り込みと分析を始めました。1分ほどで終わります。") if started else ("warn", "すでに実行中です。")))
            if path == "proposals":
                act = form.get("action")
                if act == "new":
                    kind = form.get("kind", "add_negative_keyword")
                    params = dict(campaign_id=int(form.get("campaign_id", "0")), keyword=form.get("keyword", "").strip(), match_type=form.get("match_type", "EXACT"))
                    if not params["keyword"]:
                        return self._send(page_search_terms(cfg, {}, ("warn", "キーワードが空です。")))
                    nid = prop.add_manual(cfg, kind, params, form.get("reason", "画面から追加"))
                    return self._send(page_search_terms(cfg, {}, ("ok", f"提案 #{nid} に入れました。「提案と承認」で承認すると反映されます。")))
                try:
                    pid = int(form.get("id", "0"))
                except ValueError:
                    pid = 0
                if act == "apply":
                    done = prop.apply(cfg, {pid})
                    msg = ("ok", f"#{pid} を実行しました: {done[0].get('result', '')}") if done and done[0]["status"] == "applied" else ("warn", f"#{pid} は実行できませんでした: {done[0].get('result', '') if done else '見つかりません'}")
                    return self._send(page_proposals(cfg, {}, msg))
                if act == "reject":
                    prop.reject(cfg, {pid})
                    return self._send(page_proposals(cfg, {}, ("ok", f"#{pid} を却下しました。")))
            return self._send("bad request", 400, "text/plain")

    return H


def serve(cfg, host="127.0.0.1", port=8080):
    ensure_dir(cfg.outputs)
    srv = ThreadingHTTPServer((host, port), make_handler(cfg))
    print(f"kadsai web: http://{host}:{port}/  (Ctrl+C で停止)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
