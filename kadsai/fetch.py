"""Google Ads API から日別の実績を取り込む。読むだけ。書き換えは proposals.py だけが行う。"""
import datetime as dt
import json
import warnings

from . import db
from .util import micros, ymd, today_jst

warnings.filterwarnings("ignore", category=FutureWarning)


def client(cfg):
    from google.ads.googleads.client import GoogleAdsClient
    c = GoogleAdsClient.load_from_storage(cfg.google_ads_yaml)
    if cfg.login_customer_id:
        c.login_customer_id = cfg.login_customer_id
    return c


def _search(ga, cid, query):
    return ga.search(customer_id=cid, query=query)


def _m(r):
    m = r.metrics
    return dict(impressions=m.impressions, clicks=m.clicks, cost=micros(m.cost_micros),
                conversions=float(m.conversions))


def fetch(cfg, days=35, date_to=None, sources=None):
    c = client(cfg)
    ga = c.get_service("GoogleAdsService")
    cid = cfg.customer_id
    date_to = date_to or (today_jst() - dt.timedelta(days=1))
    date_from = date_to - dt.timedelta(days=days - 1)
    rng = f"segments.date BETWEEN '{ymd(date_from)}' AND '{ymd(date_to)}'"
    con = db.connect(cfg.db)
    counts = {}
    want = set(sources or ["campaign", "adgroup", "keyword", "search_term", "ad", "hour", "geo", "negative"])

    if "campaign" in want:
        rows = []
        for r in _search(ga, cid, f"""
            SELECT segments.date, campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type,
                   campaign_budget.amount_micros,
                   metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.conversions_value,
                   metrics.search_impression_share, metrics.search_budget_lost_impression_share,
                   metrics.search_rank_lost_impression_share
            FROM campaign WHERE {rng} AND campaign.status != 'REMOVED'"""):
            d = dict(date=r.segments.date, customer_id=cid, campaign_id=r.campaign.id, campaign_name=r.campaign.name,
                     status=r.campaign.status.name, channel=r.campaign.advertising_channel_type.name,
                     budget=micros(r.campaign_budget.amount_micros), conv_value=float(r.metrics.conversions_value),
                     search_is=r.metrics.search_impression_share or 0.0,
                     lost_is_budget=r.metrics.search_budget_lost_impression_share or 0.0,
                     lost_is_rank=r.metrics.search_rank_lost_impression_share or 0.0)
            d.update(_m(r))
            rows.append(d)
        counts["campaign"] = db.upsert(con, "daily_campaign", rows)

    if "adgroup" in want:
        rows = []
        for r in _search(ga, cid, f"""
            SELECT segments.date, campaign.id, ad_group.id, ad_group.name, ad_group.status,
                   metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
            FROM ad_group WHERE {rng} AND ad_group.status != 'REMOVED'"""):
            d = dict(date=r.segments.date, customer_id=cid, campaign_id=r.campaign.id, adgroup_id=r.ad_group.id,
                     adgroup_name=r.ad_group.name, status=r.ad_group.status.name)
            d.update(_m(r))
            rows.append(d)
        counts["adgroup"] = db.upsert(con, "daily_adgroup", rows)

    if "keyword" in want:
        rows = []
        for r in _search(ga, cid, f"""
            SELECT segments.date, campaign.id, ad_group.id, ad_group_criterion.criterion_id,
                   ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, ad_group_criterion.status,
                   ad_group_criterion.quality_info.quality_score,
                   ad_group_criterion.position_estimates.first_page_cpc_micros,
                   ad_group_criterion.position_estimates.top_of_page_cpc_micros,
                   ad_group_criterion.effective_cpc_bid_micros,
                   metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
            FROM keyword_view WHERE {rng} AND ad_group_criterion.status != 'REMOVED'"""):
            k = r.ad_group_criterion
            d = dict(date=r.segments.date, customer_id=cid, campaign_id=r.campaign.id, adgroup_id=r.ad_group.id,
                     criterion_id=k.criterion_id, keyword=k.keyword.text, match_type=k.keyword.match_type.name,
                     status=k.status.name, quality_score=k.quality_info.quality_score or None,
                     first_page_cpc=micros(k.position_estimates.first_page_cpc_micros),
                     top_page_cpc=micros(k.position_estimates.top_of_page_cpc_micros),
                     cpc_bid=micros(k.effective_cpc_bid_micros))
            d.update(_m(r))
            rows.append(d)
        counts["keyword"] = db.upsert(con, "daily_keyword", rows)

    if "search_term" in want:
        rows = []
        for r in _search(ga, cid, f"""
            SELECT segments.date, campaign.id, ad_group.id, search_term_view.search_term, search_term_view.status,
                   metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
            FROM search_term_view WHERE {rng}"""):
            d = dict(date=r.segments.date, customer_id=cid, campaign_id=r.campaign.id, adgroup_id=r.ad_group.id,
                     search_term=r.search_term_view.search_term, status=r.search_term_view.status.name)
            d.update(_m(r))
            rows.append(d)
        counts["search_term"] = db.upsert(con, "daily_search_term", rows)

    if "ad" in want:
        rows = []
        for r in _search(ga, cid, f"""
            SELECT segments.date, campaign.id, ad_group.id, ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.status,
                   ad_group_ad.ad.final_urls, ad_group_ad.ad.responsive_search_ad.headlines,
                   metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
            FROM ad_group_ad WHERE {rng} AND ad_group_ad.status != 'REMOVED'"""):
            ad = r.ad_group_ad.ad
            heads = [h.text for h in ad.responsive_search_ad.headlines][:3]
            d = dict(date=r.segments.date, customer_id=cid, campaign_id=r.campaign.id, adgroup_id=r.ad_group.id,
                     ad_id=ad.id, ad_type=ad.type_.name, status=r.ad_group_ad.status.name,
                     headline=" / ".join(heads), final_url=(list(ad.final_urls) or [""])[0])
            d.update(_m(r))
            rows.append(d)
        counts["ad"] = db.upsert(con, "daily_ad", rows)

    if "hour" in want:
        rows = []
        for r in _search(ga, cid, f"""
            SELECT segments.date, segments.hour, campaign.id,
                   metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
            FROM campaign WHERE {rng} AND campaign.status != 'REMOVED'"""):
            d = dict(date=r.segments.date, customer_id=cid, campaign_id=r.campaign.id, hour=r.segments.hour)
            d.update(_m(r))
            rows.append(d)
        counts["hour"] = db.upsert(con, "daily_hour", rows)

    if "geo" in want:
        rows, ids = [], set()
        try:
            for r in _search(ga, cid, f"""
                SELECT segments.date, campaign.id, segments.geo_target_city,
                       metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
                FROM geographic_view WHERE {rng}"""):
                gid = int((r.segments.geo_target_city or "/0").rsplit("/", 1)[-1] or 0)
                ids.add(gid)
                d = dict(date=r.segments.date, customer_id=cid, campaign_id=r.campaign.id, geo_id=gid, geo_name="")
                d.update(_m(r))
                rows.append(d)
            names = _geo_names(ga, cid, ids)
            for d in rows:
                d["geo_name"] = names.get(d["geo_id"], str(d["geo_id"]))
            counts["geo"] = db.upsert(con, "daily_geo", rows)
        except Exception as e:  # 地域レポートは口座の種類で取れないことがある
            counts["geo"] = f"skip: {str(e)[:120]}"

    if "negative" in want:
        con.execute("DELETE FROM negative_keyword WHERE customer_id=?", (cid,))
        rows = []
        for r in _search(ga, cid, """
            SELECT campaign.id, campaign_criterion.criterion_id, campaign_criterion.keyword.text,
                   campaign_criterion.keyword.match_type
            FROM campaign_criterion WHERE campaign_criterion.negative = TRUE AND campaign_criterion.type = 'KEYWORD'
              AND campaign.status != 'REMOVED'"""):
            rows.append(dict(customer_id=cid, level="campaign", campaign_id=r.campaign.id, adgroup_id=0,
                             criterion_id=r.campaign_criterion.criterion_id, keyword=r.campaign_criterion.keyword.text,
                             match_type=r.campaign_criterion.keyword.match_type.name, list_name=""))
        for r in _search(ga, cid, """
            SELECT campaign.id, ad_group.id, ad_group_criterion.criterion_id, ad_group_criterion.keyword.text,
                   ad_group_criterion.keyword.match_type
            FROM ad_group_criterion WHERE ad_group_criterion.negative = TRUE AND ad_group_criterion.type = 'KEYWORD'
              AND ad_group_criterion.status != 'REMOVED'"""):
            rows.append(dict(customer_id=cid, level="adgroup", campaign_id=r.campaign.id, adgroup_id=r.ad_group.id,
                             criterion_id=r.ad_group_criterion.criterion_id, keyword=r.ad_group_criterion.keyword.text,
                             match_type=r.ad_group_criterion.keyword.match_type.name, list_name=""))
        try:
            sets = {}
            for r in _search(ga, cid, """
                SELECT campaign.id, campaign_shared_set.shared_set, shared_set.name, shared_set.id
                FROM campaign_shared_set WHERE shared_set.type = 'NEGATIVE_KEYWORDS' AND campaign_shared_set.status='ENABLED'"""):
                sets.setdefault(r.shared_set.id, (r.shared_set.name, []))[1].append(r.campaign.id)
            for sid, (name, camps) in sets.items():
                for r in _search(ga, cid, f"""
                    SELECT shared_criterion.criterion_id, shared_criterion.keyword.text, shared_criterion.keyword.match_type
                    FROM shared_criterion WHERE shared_set.id = {sid} AND shared_criterion.type = 'KEYWORD'"""):
                    for camp in camps:
                        rows.append(dict(customer_id=cid, level="list", campaign_id=camp, adgroup_id=0,
                                         criterion_id=r.shared_criterion.criterion_id, keyword=r.shared_criterion.keyword.text,
                                         match_type=r.shared_criterion.keyword.match_type.name, list_name=name))
        except Exception as e:
            counts["negative_list"] = f"skip: {str(e)[:120]}"
        counts["negative"] = db.upsert(con, "negative_keyword", rows)

    con.execute("INSERT INTO fetch_log VALUES (?,?,?,?,?,?)",
                (dt.datetime.now().isoformat(timespec="seconds"), cid, ymd(date_from), ymd(date_to), "api",
                 json.dumps(counts, ensure_ascii=False)))
    con.commit()
    con.close()
    return dict(date_from=ymd(date_from), date_to=ymd(date_to), counts=counts)


def _geo_names(ga, cid, ids):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    out = {}
    for i in range(0, len(ids), 500):
        chunk = ",".join(str(x) for x in ids[i:i + 500])
        for r in _search(ga, cid, f"""
            SELECT geo_target_constant.id, geo_target_constant.name, geo_target_constant.canonical_name
            FROM geo_target_constant WHERE geo_target_constant.id IN ({chunk})"""):
            out[r.geo_target_constant.id] = r.geo_target_constant.name
    return out
