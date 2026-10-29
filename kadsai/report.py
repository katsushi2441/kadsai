"""日報を Markdown・HTML・LINE 用の短い文にする。"""
import html
import os

from .util import ensure_dir

KIND_LABEL = {
    "anomaly": "急な増減", "wasted_search_term": "成果ゼロの検索語", "wasted_ngram": "成果ゼロの語",
    "negative_conflict": "除外の衝突", "lost_is_budget": "予算負け", "lost_is_rank": "順位負け",
    "bid_below_first_page": "入札が低い", "low_quality_score": "品質スコア", "weak_ad": "弱い広告文",
    "dead_hour": "成果の出ない時間帯",
}
SEV_LABEL = {"high": "重要", "mid": "注意", "low": "参考"}


def _n(v):
    if v is None:
        return "-"
    v = float(v)
    if v == int(v):
        return f"{v:,.0f}"
    return f"{v:,.2f}" if abs(v) < 1 else f"{v:,.1f}"


def _p(v):
    return f"{(v or 0) * 100:.1f}%"


def _delta(cur, prev):
    if not prev:
        return ""
    r = cur / prev - 1
    return f"（前の期間比 {'+' if r >= 0 else ''}{r * 100:.0f}%）"


def _kpi_lines(cfg, s):
    c, p = s["current"], s["previous"]
    u = cfg.currency
    lines = [
        f"費用 {_n(c['cost'])}{u} {_delta(c['cost'], p['cost'])}",
        f"クリック {_n(c['clicks'])}回 {_delta(c['clicks'], p['clicks'])}",
        f"クリック単価 {_n(c['cpc'])}{u} {_delta(c['cpc'], p['cpc'])}",
        f"コンバージョン {_n(c['conversions'])}件 {_delta(c['conversions'], p['conversions'])}",
        f"1件あたり費用 {_n(c['cpa']) + u if c['cpa'] else '-'}",
    ]
    return lines


def markdown(cfg, summary, findings, ai_text=None):
    s = summary
    u = cfg.currency
    title = f"広告日報 {s['date']}" + (f"（{cfg.account_name}）" if cfg.account_name else "")
    out = [f"# {title}", ""]
    if ai_text:
        out += ["## 要点（AI）", "", ai_text.strip(), ""]
    d = s["day"]
    out += [f"## {s['date']} の実績", "",
            f"費用 {_n(d['cost'])}{u}／クリック {_n(d['clicks'])}回／表示 {_n(d['impressions'])}回／コンバージョン {_n(d['conversions'])}件", ""]
    out += [f"## 直近{s['days']}日の実績", ""] + [f"- {l}" for l in _kpi_lines(cfg, s)] + [""]
    if s["campaigns"]:
        out += ["### キャンペーン別", "", "| キャンペーン | 費用 | クリック | CV | 1件あたり |", "|---|---:|---:|---:|---:|"]
        for c in s["campaigns"]:
            out.append(f"| {c['campaign_name']} | {_n(c['cost'])} | {_n(c['clicks'])} | {_n(c['conversions'])} | {_n(c['cpa']) if c['cpa'] else '-'} |")
        out.append("")
    out += [f"## 指摘（{len(findings)}件）", ""]
    if not findings:
        out += ["指摘はありません。", ""]
    for i, f in enumerate(findings, 1):
        out += [f"### {i}. [{SEV_LABEL[f['severity']]}] {f['title']}", "", f["detail"], ""]
        if f.get("suggestion"):
            out += [f"対応: {f['suggestion']}", ""]
    if s.get("top_search_terms"):
        out += ["## 成果が出た検索語（上位）", "", "| 検索語 | CV | クリック | 費用 |", "|---|---:|---:|---:|"]
        for t in s["top_search_terms"]:
            out.append(f"| {t['search_term']} | {_n(t['conversions'])} | {_n(t['clicks'])} | {_n(t['cost'])} |")
        out.append("")
    if s.get("geo"):
        out += ["## 地域別（費用の多い順）", "", "市区町村まで分かった分だけの集計です。", "", "| 地域 | 費用 | クリック | CV |", "|---|---:|---:|---:|"]
        for g in s["geo"][:10]:
            out.append(f"| {g['geo_name']} | {_n(g['cost'])} | {_n(g['clicks'])} | {_n(g['conversions'])} |")
        out.append("")
    out += ["---", "", "Kurage 広告AI分析 kadsai。数字は Google 広告の実績から規則で集計したものです。", ""]
    return "\n".join(out)


def short_text(cfg, summary, findings, ai_text=None, limit=1800):
    """LINE・メール本文用。要点と重要な指摘だけ。"""
    s = summary
    u = cfg.currency
    d = s["day"]
    lines = [f"【広告日報 {s['date']}】" + (f" {cfg.account_name}" if cfg.account_name else "")]
    if ai_text:
        lines += ["", ai_text.strip()]
    lines += ["", f"昨日: 費用{_n(d['cost'])}{u}・クリック{_n(d['clicks'])}・CV{_n(d['conversions'])}",
              f"直近{s['days']}日:"] + ["・" + l for l in _kpi_lines(cfg, s)]
    imp = [f for f in findings if f["severity"] in ("high", "mid")]
    if imp:
        lines += ["", f"指摘（{len(findings)}件のうち重要{len(imp)}件）"]
        for f in imp[:6]:
            lines.append(f"・{f['title']}")
    else:
        lines += ["", f"指摘 {len(findings)}件（重要なものはありません）"]
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit - 1] + "…"


HTML_HEAD = """<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
:root{{--ink:#1f2a37;--muted:#5b6774;--line:#e3e8ee;--bg:#fff;--card:#f6f8fa;--high:#c0392b;--mid:#d68910;--low:#2e86c1}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:"Noto Sans JP","Hiragino Sans",sans-serif;line-height:1.7}}
main{{max-width:900px;margin:0 auto;padding:24px 16px}}h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:17px;margin:28px 0 8px;border-left:4px solid #4f8ad6;padding-left:10px}}
.kpi{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}.kpi div{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}}
.kpi b{{display:block;font-size:20px}}.kpi small{{color:var(--muted)}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left}}td.n,th.n{{text-align:right}}
.f{{border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0;background:#fff}}.f .tag{{display:inline-block;font-size:12px;color:#fff;border-radius:4px;padding:1px 8px;margin-right:6px}}
.high{{background:var(--high)}}.mid{{background:var(--mid)}}.low{{background:var(--low)}}.f h3{{margin:4px 0;font-size:15px}}.f p{{margin:4px 0}}.sug{{color:var(--muted)}}
.ai{{background:#eef5ff;border:1px solid #cfe0fb;border-radius:10px;padding:12px 14px;white-space:pre-wrap}}footer{{color:var(--muted);font-size:12px;margin-top:32px}}
</style></head><body><main>"""


def html_report(cfg, summary, findings, ai_text=None):
    s = summary
    u = cfg.currency
    e = html.escape
    title = f"広告日報 {s['date']}" + (f"（{cfg.account_name}）" if cfg.account_name else "")
    out = [HTML_HEAD.format(title=e(title)), f"<h1>{e(title)}</h1><p style='color:var(--muted);margin:0'>Kurage 広告AI分析 kadsai</p>"]
    if ai_text:
        out.append(f"<h2>要点（AI）</h2><div class='ai'>{e(ai_text.strip())}</div>")
    d, c, p = s["day"], s["current"], s["previous"]
    out.append(f"<h2>{e(s['date'])} の実績</h2><div class='kpi'>"
               f"<div><small>費用</small><b>{_n(d['cost'])}{u}</b></div><div><small>クリック</small><b>{_n(d['clicks'])}</b></div>"
               f"<div><small>表示</small><b>{_n(d['impressions'])}</b></div><div><small>コンバージョン</small><b>{_n(d['conversions'])}</b></div></div>")
    out.append(f"<h2>直近{s['days']}日</h2><div class='kpi'>"
               f"<div><small>費用</small><b>{_n(c['cost'])}{u}</b><small>{e(_delta(c['cost'], p['cost']))}</small></div>"
               f"<div><small>クリック</small><b>{_n(c['clicks'])}</b><small>{e(_delta(c['clicks'], p['clicks']))}</small></div>"
               f"<div><small>クリック単価</small><b>{_n(c['cpc'])}{u}</b><small>{e(_delta(c['cpc'], p['cpc']))}</small></div>"
               f"<div><small>コンバージョン</small><b>{_n(c['conversions'])}</b><small>{e(_delta(c['conversions'], p['conversions']))}</small></div>"
               f"<div><small>1件あたり費用</small><b>{(_n(c['cpa']) + u) if c['cpa'] else '-'}</b></div></div>")
    if s["campaigns"]:
        out.append("<h3>キャンペーン別</h3><table><tr><th>キャンペーン</th><th class=n>費用</th><th class=n>クリック</th><th class=n>CV</th><th class=n>1件あたり</th><th>状態</th></tr>")
        for cc in s["campaigns"]:
            out.append(f"<tr><td>{e(cc['campaign_name'])}</td><td class=n>{_n(cc['cost'])}</td><td class=n>{_n(cc['clicks'])}</td><td class=n>{_n(cc['conversions'])}</td><td class=n>{_n(cc['cpa']) if cc['cpa'] else '-'}</td><td>{e(cc['status'])}</td></tr>")
        out.append("</table>")
    out.append(f"<h2>指摘（{len(findings)}件）</h2>")
    if not findings:
        out.append("<p>指摘はありません。</p>")
    for f in findings:
        out.append(f"<div class='f'><span class='tag {f['severity']}'>{SEV_LABEL[f['severity']]}</span><small>{e(KIND_LABEL.get(f['kind'], f['kind']))}</small>"
                   f"<h3>{e(f['title'])}</h3><p>{e(f['detail'])}</p>" + (f"<p class='sug'>対応: {e(f['suggestion'])}</p>" if f.get("suggestion") else "") + "</div>")
    if s.get("top_search_terms"):
        out.append("<h2>成果が出た検索語</h2><table><tr><th>検索語</th><th class=n>CV</th><th class=n>クリック</th><th class=n>費用</th></tr>")
        for t in s["top_search_terms"]:
            out.append(f"<tr><td>{e(t['search_term'])}</td><td class=n>{_n(t['conversions'])}</td><td class=n>{_n(t['clicks'])}</td><td class=n>{_n(t['cost'])}</td></tr>")
        out.append("</table>")
    if s.get("wasted_search_terms"):
        out.append("<h2>成果ゼロの検索語（除外候補）</h2><table><tr><th>検索語</th><th class=n>費用</th><th class=n>クリック</th><th>キャンペーン</th></tr>")
        for t in s["wasted_search_terms"]:
            out.append(f"<tr><td>{e(t['search_term'])}</td><td class=n>{_n(t['cost'])}</td><td class=n>{_n(t['clicks'])}</td><td>{e(t['campaign_name'])}</td></tr>")
        out.append("</table>")
    if s.get("hours"):
        mx = max((h["cost"] for h in s["hours"]), default=0) or 1
        out.append("<h2>時間帯別（費用）</h2><table>")
        for h in s["hours"]:
            w = int(h["cost"] / mx * 100)
            out.append(f"<tr><td style='width:3em'>{h['hour']}時</td><td><div style='background:#4f8ad6;height:10px;width:{w}%'></div></td><td class=n style='width:7em'>{_n(h['cost'])}{u}</td><td class=n style='width:5em'>CV {_n(h['conversions'])}</td></tr>")
        out.append("</table>")
    if s.get("geo"):
        out.append("<h2>地域別（費用の多い順）</h2><p style='color:var(--muted)'>市区町村まで分かった分だけの集計です。</p><table><tr><th>地域</th><th class=n>費用</th><th class=n>クリック</th><th class=n>CV</th></tr>")
        for g in s["geo"][:15]:
            out.append(f"<tr><td>{e(g['geo_name'])}</td><td class=n>{_n(g['cost'])}</td><td class=n>{_n(g['clicks'])}</td><td class=n>{_n(g['conversions'])}</td></tr>")
        out.append("</table>")
    out.append("<footer>Kurage 広告AI分析 kadsai。数字は Google 広告の実績から規則で集計したものです。AI の要点は、この数字を言い換えたものです。</footer></main></body></html>")
    return "\n".join(out)


def write_all(cfg, date_str, summary, findings, ai_text=None):
    d = ensure_dir(os.path.join(cfg.outputs, "reports", date_str))
    paths = {}
    with open(os.path.join(d, "report.md"), "w", encoding="utf-8") as f:
        f.write(markdown(cfg, summary, findings, ai_text))
    with open(os.path.join(d, "report.html"), "w", encoding="utf-8") as f:
        f.write(html_report(cfg, summary, findings, ai_text))
    with open(os.path.join(d, "short.txt"), "w", encoding="utf-8") as f:
        f.write(short_text(cfg, summary, findings, ai_text))
    for k in ("report.md", "report.html", "short.txt"):
        paths[k] = os.path.join(d, k)
    return paths
