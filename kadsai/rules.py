"""規則による分析。数字はすべてここで確定する。AI はこの結果を言い換えるだけ。"""
import datetime as dt
from collections import defaultdict

from . import db
from .util import ngrams, ymd

SEV = {"high": 3, "mid": 2, "low": 1}


def _f(kind, severity, title, detail, numbers=None, entity=None, suggestion=None, proposal=None):
    return dict(kind=kind, severity=severity, title=title, detail=detail, numbers=numbers or {},
                entity=entity or {}, suggestion=suggestion or "", proposal=proposal)


def _sum(rows, key):
    return sum((r[key] or 0) for r in rows)


def _period(con, cid, table, d_from, d_to, extra_where="", params=()):
    return db.q(con, f"SELECT * FROM {table} WHERE customer_id=? AND date BETWEEN ? AND ? {extra_where}",
                (cid, ymd(d_from), ymd(d_to)) + tuple(params))


def campaign_names(con, cid):
    rows = db.q(con, "SELECT campaign_id, campaign_name FROM daily_campaign WHERE customer_id=? GROUP BY campaign_id", (cid,))
    return {r["campaign_id"]: r["campaign_name"] for r in rows}


def adgroup_names(con, cid):
    rows = db.q(con, "SELECT adgroup_id, adgroup_name FROM daily_adgroup WHERE customer_id=? GROUP BY adgroup_id", (cid,))
    return {r["adgroup_id"]: r["adgroup_name"] for r in rows}


def analyze(cfg, date):
    """date=分析対象日（ふつうは昨日）。戻り値は summary と findings。"""
    R = cfg.rules
    con = db.connect(cfg.db)
    cid = cfg.customer_id
    cn = campaign_names(con, cid)
    an = adgroup_names(con, cid)
    findings = []

    # ---- 0. 期間の要約（直近N日 vs その前N日）
    n = R["report_days"]
    cur = _period(con, cid, "daily_campaign", date - dt.timedelta(days=n - 1), date)
    prev = _period(con, cid, "daily_campaign", date - dt.timedelta(days=2 * n - 1), date - dt.timedelta(days=n))
    day = _period(con, cid, "daily_campaign", date, date)
    summary = dict(date=ymd(date), days=n,
                   day=_agg(day), current=_agg(cur), previous=_agg(prev),
                   campaigns=[])
    by_c = defaultdict(list)
    for r in cur:
        by_c[r["campaign_id"]].append(r)
    for c_id, rows in sorted(by_c.items(), key=lambda kv: -_sum(kv[1], "cost")):
        a = _agg(rows)
        a.update(campaign_id=c_id, campaign_name=cn.get(c_id, str(c_id)), status=rows[-1]["status"],
                 channel=rows[-1]["channel"], budget=rows[-1]["budget"])
        summary["campaigns"].append(a)

    # ---- 1. 異常検知：対象日を、過去N週の同じ曜日の平均と比べる
    weeks = R["anomaly_weeks"]
    same_days = [date - dt.timedelta(days=7 * k) for k in range(1, weeks + 1)]
    hist = {}
    for d in same_days:
        for r in _period(con, cid, "daily_campaign", d, d):
            hist.setdefault(r["campaign_id"], []).append(r)
    for c_id, rows in by_c.items():
        today = [r for r in day if r["campaign_id"] == c_id]
        if not today or c_id not in hist:
            continue
        t = today[0]
        h = hist[c_id]
        for key, label, minimum in (("cost", "費用", R["anomaly_min_cost"]), ("clicks", "クリック", R["anomaly_min_clicks"]),
                                    ("conversions", "コンバージョン", 1)):
            avg = _sum(h, key) / len(h)
            v = t[key] or 0
            if max(v, avg) < minimum:
                continue
            if avg == 0:
                ratio = None
            else:
                ratio = v / avg - 1
            if ratio is None or abs(ratio) >= R["anomaly_threshold"]:
                direction = "増" if (ratio is None or ratio > 0) else "減"
                sev = "high" if key == "conversions" or (ratio is not None and abs(ratio) >= 1) else "mid"
                findings.append(_f(
                    "anomaly", sev,
                    f"{cn.get(c_id, c_id)}：{label}が同じ曜日の平均より{direction}",
                    f"{ymd(date)}（{_wd(date)}）の{label}は{_n(v)}。過去{len(h)}週の同じ曜日の平均は{_n(avg)}。",
                    numbers=dict(metric=key, value=v, average=avg, ratio=ratio, weeks=len(h)),
                    entity=dict(campaign_id=c_id, campaign_name=cn.get(c_id, str(c_id))),
                    suggestion="急な増減は、入札・予算・広告の変更か、検索の需要の変化が原因です。変更履歴と検索語を見てください。"))

    # ---- 2. 検索語：成果ゼロで費用を食った語（除外候補）
    sd = R["search_term_days"]
    st = _period(con, cid, "daily_search_term", date - dt.timedelta(days=sd - 1), date)
    by_t = defaultdict(list)
    for r in st:
        by_t[(r["campaign_id"], r["search_term"])].append(r)
    negs = db.q(con, "SELECT * FROM negative_keyword WHERE customer_id=?", (cid,))
    neg_set = {(r["campaign_id"], r["keyword"].lower()) for r in negs} | {(0, r["keyword"].lower()) for r in negs}
    wasted = []
    for (c_id, term), rows in by_t.items():
        cost, clicks, conv = _sum(rows, "cost"), _sum(rows, "clicks"), _sum(rows, "conversions")
        if conv == 0 and (cost >= R["wasted_min_cost"] or clicks >= R["wasted_min_clicks"]):
            if (c_id, term.lower()) in neg_set:
                continue
            wasted.append(dict(campaign_id=c_id, campaign_name=cn.get(c_id, str(c_id)), search_term=term,
                               cost=cost, clicks=clicks, impressions=_sum(rows, "impressions")))
    wasted.sort(key=lambda x: -x["cost"])
    for w in wasted[:20]:
        findings.append(_f(
            "wasted_search_term", "mid" if w["cost"] < R["wasted_min_cost"] * 3 else "high",
            f"成果ゼロの検索語「{w['search_term']}」",
            f"直近{sd}日で費用{_n(w['cost'])}{cfg.currency}・クリック{w['clicks']}回・コンバージョン0（{w['campaign_name']}）。",
            numbers=dict(cost=w["cost"], clicks=w["clicks"], impressions=w["impressions"], days=sd),
            entity=dict(campaign_id=w["campaign_id"], campaign_name=w["campaign_name"], search_term=w["search_term"]),
            suggestion="自社の仕事と関係ない語なら、キャンペーンの除外キーワード（完全一致）に追加します。",
            proposal=dict(kind="add_negative_keyword", campaign_id=w["campaign_id"], keyword=w["search_term"],
                          match_type="EXACT")))

    # ---- 3. 語（n-gram）単位：成果ゼロのまま費用を使っている語
    gram = defaultdict(lambda: dict(cost=0.0, clicks=0, conversions=0.0, terms=set()))
    for (c_id, term), rows in by_t.items():
        cost, clicks, conv = _sum(rows, "cost"), _sum(rows, "clicks"), _sum(rows, "conversions")
        for g in set(ngrams(term)):
            gram[g]["cost"] += cost
            gram[g]["clicks"] += clicks
            gram[g]["conversions"] += conv
            gram[g]["terms"].add(term)
    bad = [(g, v) for g, v in gram.items()
           if v["conversions"] == 0 and v["cost"] >= R["ngram_min_cost"] and len(v["terms"]) >= 2]
    bad.sort(key=lambda kv: -kv[1]["cost"])
    for g, v in bad[:10]:
        findings.append(_f(
            "wasted_ngram", "mid",
            f"語「{g}」を含む検索はすべて成果ゼロ",
            f"直近{sd}日、「{g}」を含む検索語{len(v['terms'])}種類で費用{_n(v['cost'])}{cfg.currency}・クリック{v['clicks']}回・コンバージョン0。",
            numbers=dict(cost=v["cost"], clicks=v["clicks"], terms=sorted(v["terms"])[:10]),
            entity=dict(ngram=g),
            suggestion="この語そのものを除外キーワード（部分一致）にすると、同じ種類の検索をまとめて止められます。"))

    # ---- 4. 成果が出ている検索語（上位）
    good = []
    for (c_id, term), rows in by_t.items():
        conv = _sum(rows, "conversions")
        if conv > 0:
            good.append(dict(campaign_id=c_id, search_term=term, conversions=conv, cost=_sum(rows, "cost"),
                             clicks=_sum(rows, "clicks")))
    good.sort(key=lambda x: (-x["conversions"], x["cost"]))
    summary["top_search_terms"] = good[:10]
    summary["wasted_search_terms"] = wasted[:20]

    # ---- 5. 除外キーワードの衝突：除外が、入札しているキーワードを止めていないか
    kws = db.q(con, """SELECT campaign_id, adgroup_id, keyword, match_type FROM daily_keyword
                       WHERE customer_id=? AND status='ENABLED' GROUP BY campaign_id, adgroup_id, keyword""", (cid,))
    for ng in negs:
        nt = ng["keyword"].lower().strip()
        for k in kws:
            if ng["campaign_id"] and ng["campaign_id"] != k["campaign_id"]:
                continue
            if ng["level"] == "adgroup" and ng["adgroup_id"] != k["adgroup_id"]:
                continue
            kt = k["keyword"].lower()
            hit = (nt == kt) if ng["match_type"] == "EXACT" else (nt in kt)
            if hit:
                findings.append(_f(
                    "negative_conflict", "high",
                    f"除外キーワード「{ng['keyword']}」が入札キーワード「{k['keyword']}」を止めている",
                    f"{ng['level']}レベルの除外（{ng['match_type']}）が、{cn.get(k['campaign_id'], k['campaign_id'])} の入札キーワードに当たります。",
                    entity=dict(campaign_id=k["campaign_id"], negative=ng["keyword"], keyword=k["keyword"]),
                    suggestion="どちらかが間違いです。除外を外すか、キーワードを止めてください。"))

    # ---- 6. インプレッションシェア（検索キャンペーン）
    isd = R["is_days"]
    isrows = _period(con, cid, "daily_campaign", date - dt.timedelta(days=isd - 1), date, "AND channel='SEARCH'")
    by_c2 = defaultdict(list)
    for r in isrows:
        by_c2[r["campaign_id"]].append(r)
    for c_id, rows in by_c2.items():
        imps = _sum(rows, "impressions")
        if imps == 0:
            continue
        lb = sum((r["lost_is_budget"] or 0) * (r["impressions"] or 0) for r in rows) / imps
        lr = sum((r["lost_is_rank"] or 0) * (r["impressions"] or 0) for r in rows) / imps
        share = sum((r["search_is"] or 0) * (r["impressions"] or 0) for r in rows) / imps
        if lb >= R["is_lost_budget"]:
            findings.append(_f(
                "lost_is_budget", "mid",
                f"{cn.get(c_id, c_id)}：予算不足で表示を逃している",
                f"直近{isd}日のインプレッションシェア{_p(share)}。予算が理由で逃した割合{_p(lb)}。",
                numbers=dict(share=share, lost_budget=lb, lost_rank=lr),
                entity=dict(campaign_id=c_id, campaign_name=cn.get(c_id, str(c_id))),
                suggestion="成果が出ているキャンペーンなら日予算を上げる価値があります。出ていなければ上げません。",
                proposal=dict(kind="raise_budget", campaign_id=c_id, ratio=1.2)))
        if lr >= R["is_lost_rank"]:
            findings.append(_f(
                "lost_is_rank", "low",
                f"{cn.get(c_id, c_id)}：順位が低くて表示を逃している",
                f"直近{isd}日のインプレッションシェア{_p(share)}。順位が理由で逃した割合{_p(lr)}。",
                numbers=dict(share=share, lost_budget=lb, lost_rank=lr),
                entity=dict(campaign_id=c_id, campaign_name=cn.get(c_id, str(c_id))),
                suggestion="入札を上げるより先に、広告文とリンク先の品質（品質スコア）を見てください。"))

    # ---- 7. キーワード：1ページ目に届かない入札・低い品質スコア・表示ゼロ
    kd = _period(con, cid, "daily_keyword", date - dt.timedelta(days=isd - 1), date, "AND status='ENABLED'")
    by_k = defaultdict(list)
    for r in kd:
        by_k[(r["adgroup_id"], r["criterion_id"])].append(r)
    below, lowq = [], []
    for key, rows in by_k.items():
        last = rows[-1]
        if last["first_page_cpc"] and last["cpc_bid"] and last["cpc_bid"] < last["first_page_cpc"] and _sum(rows, "impressions") < 10:
            below.append(last)
        if last["quality_score"] and last["quality_score"] <= R["quality_score_low"] and _sum(rows, "impressions") >= 10:
            lowq.append((last, _sum(rows, "cost")))
    for k in below[:10]:
        findings.append(_f(
            "bid_below_first_page", "low",
            f"キーワード「{k['keyword']}」の入札が1ページ目の目安に届いていない",
            f"入札{_n(k['cpc_bid'])}{cfg.currency}に対し、1ページ目の目安{_n(k['first_page_cpc'])}{cfg.currency}。直近{isd}日の表示はほぼゼロ。",
            numbers=dict(cpc_bid=k["cpc_bid"], first_page_cpc=k["first_page_cpc"]),
            entity=dict(adgroup_id=k["adgroup_id"], adgroup_name=an.get(k["adgroup_id"], ""), keyword=k["keyword"]),
            suggestion="必要な語なら入札を目安まで上げる、不要なら止める。"))
    for k, cost in sorted(lowq, key=lambda x: -x[1])[:10]:
        findings.append(_f(
            "low_quality_score", "mid",
            f"キーワード「{k['keyword']}」の品質スコアが{k['quality_score']}",
            f"品質スコア{k['quality_score']}（10点満点）。直近{isd}日の費用{_n(cost)}{cfg.currency}。",
            numbers=dict(quality_score=k["quality_score"], cost=cost),
            entity=dict(adgroup_id=k["adgroup_id"], adgroup_name=an.get(k["adgroup_id"], ""), keyword=k["keyword"]),
            suggestion="広告文にこの語を入れる、リンク先のページにこの語の内容を書く、の2つで上がります。"))

    # ---- 8. 広告文：同じ広告グループ内で成績が離れている
    ad = _period(con, cid, "daily_ad", date - dt.timedelta(days=sd - 1), date, "AND status='ENABLED'")
    by_g = defaultdict(lambda: defaultdict(list))
    for r in ad:
        by_g[r["adgroup_id"]][r["ad_id"]].append(r)
    for g_id, ads in by_g.items():
        stats = []
        for ad_id, rows in ads.items():
            imps = _sum(rows, "impressions")
            if imps >= R["ad_min_impressions"]:
                stats.append(dict(ad_id=ad_id, headline=rows[-1]["headline"], impressions=imps,
                                  clicks=_sum(rows, "clicks"), ctr=_sum(rows, "clicks") / imps,
                                  conversions=_sum(rows, "conversions")))
        if len(stats) >= 2:
            stats.sort(key=lambda x: -x["ctr"])
            best, worst = stats[0], stats[-1]
            if best["ctr"] > 0 and worst["ctr"] < best["ctr"] * 0.5:
                findings.append(_f(
                    "weak_ad", "low",
                    f"{an.get(g_id, g_id)}：クリック率が半分以下の広告がある",
                    f"いちばん良い広告「{best['headline']}」のクリック率{_p(best['ctr'])}に対し、「{worst['headline']}」は{_p(worst['ctr'])}（直近{sd}日・表示{worst['impressions']}回）。",
                    numbers=dict(best=best, worst=worst),
                    entity=dict(adgroup_id=g_id, adgroup_name=an.get(g_id, ""), ad_id=worst["ad_id"]),
                    suggestion="弱い広告を止めるか、良い広告の見出しに寄せて書き直します。"))

    # ---- 9. 時間帯：費用は使うが成果が出ない時間帯
    hd = R["hour_days"]
    hr = _period(con, cid, "daily_hour", date - dt.timedelta(days=hd - 1), date)
    by_h = defaultdict(lambda: dict(cost=0.0, clicks=0, conversions=0.0, impressions=0))
    for r in hr:
        h = by_h[r["hour"]]
        for k in ("cost", "clicks", "conversions", "impressions"):
            h[k] += r[k] or 0
    total_cost = sum(v["cost"] for v in by_h.values()) or 1
    total_conv = sum(v["conversions"] for v in by_h.values())
    summary["hours"] = [dict(hour=h, **v) for h, v in sorted(by_h.items())]
    if total_conv > 0:
        for h, v in sorted(by_h.items()):
            if v["conversions"] == 0 and v["cost"] / total_cost >= 0.08:
                findings.append(_f(
                    "dead_hour", "low",
                    f"{h}時台は費用の{_p(v['cost'] / total_cost)}を使って成果ゼロ",
                    f"直近{hd}日、{h}時台の費用{_n(v['cost'])}{cfg.currency}・クリック{v['clicks']}回・コンバージョン0。",
                    numbers=dict(hour=h, cost=v["cost"], share=v["cost"] / total_cost),
                    entity=dict(hour=h),
                    suggestion="電話が取れない時間帯なら、広告スケジュールでその時間を外すか入札を下げます。"))

    # ---- 10. 地域
    geo = _period(con, cid, "daily_geo", date - dt.timedelta(days=sd - 1), date)
    by_geo = defaultdict(lambda: dict(cost=0.0, clicks=0, conversions=0.0, impressions=0, geo_name=""))
    for r in geo:
        g = by_geo[r["geo_id"]]
        g["geo_name"] = r["geo_name"]
        for k in ("cost", "clicks", "conversions", "impressions"):
            g[k] += r[k] or 0
    summary["geo"] = sorted((dict(geo_id=k, **v) for k, v in by_geo.items()), key=lambda x: -x["cost"])[:15]

    findings.sort(key=lambda f: -SEV[f["severity"]])
    con.close()
    return summary, findings


def _agg(rows):
    imps, clicks, cost, conv = _sum(rows, "impressions"), _sum(rows, "clicks"), _sum(rows, "cost"), _sum(rows, "conversions")
    return dict(impressions=imps, clicks=clicks, cost=cost, conversions=conv,
                conv_value=_sum(rows, "conv_value") if rows and "conv_value" in rows[0] else 0,
                ctr=(clicks / imps) if imps else 0, cpc=(cost / clicks) if clicks else 0,
                cpa=(cost / conv) if conv else None, cvr=(conv / clicks) if clicks else 0)


def _n(v):
    v = float(v)
    if v == int(v):
        return f"{v:,.0f}"
    return f"{v:,.2f}" if abs(v) < 1 else f"{v:,.1f}"


def _p(v):
    return f"{v * 100:.1f}%"


def _wd(d):
    return "月火水木金土日"[d.weekday()]
