"""設定変更は承認制。分析が出した提案を保存し、人が承認したものだけ API で実行する。"""
import datetime as dt
import os
import warnings

from .util import dump_json, ensure_dir, load_json

warnings.filterwarnings("ignore", category=FutureWarning)


def _path(cfg):
    return os.path.join(ensure_dir(os.path.join(cfg.outputs, "proposals")), "proposals.json")


def load(cfg):
    p = _path(cfg)
    return load_json(p) if os.path.exists(p) else []


def save(cfg, items):
    dump_json(_path(cfg), items)


def collect(cfg, date_str, findings):
    """findings の proposal を提案一覧に追加（同じ内容は増やさない）。"""
    items = load(cfg)
    existing = {(i["kind"], _key(i)) for i in items if i["status"] in ("pending", "applied")}
    added = 0
    nid = max([i["id"] for i in items], default=0) + 1
    for f in findings:
        pr = f.get("proposal")
        if not pr:
            continue
        if (pr["kind"], _key(pr)) in existing:
            continue
        items.append(dict(id=nid, date=date_str, status="pending", kind=pr["kind"], params=pr,
                          reason=f["title"] + "。" + f["detail"], created=dt.datetime.now().isoformat(timespec="seconds")))
        existing.add((pr["kind"], _key(pr)))
        nid += 1
        added += 1
    save(cfg, items)
    return added, items


def _key(p):
    params = p.get("params", p)
    return (str(params.get("campaign_id", "")), str(params.get("keyword", "")), str(params.get("adgroup_id", "")),
            str(params.get("criterion_id", "")))


def apply(cfg, ids, dry_run=False):
    from .fetch import client
    items = load(cfg)
    c = client(cfg)
    cid = cfg.customer_id
    done = []
    for it in items:
        if it["id"] not in ids or it["status"] != "pending":
            continue
        p = it["params"]
        try:
            if dry_run:
                it["result"] = "dry-run"
            elif it["kind"] == "add_negative_keyword":
                it["result"] = _add_negative(c, cid, p["campaign_id"], p["keyword"], p.get("match_type", "EXACT"))
            elif it["kind"] == "raise_budget":
                it["result"] = _raise_budget(c, cid, p["campaign_id"], float(p.get("ratio", 1.2)), p.get("amount"))
            elif it["kind"] == "pause_keyword":
                it["result"] = _pause_keyword(c, cid, p["adgroup_id"], p["criterion_id"])
            elif it["kind"] == "pause_campaign":
                it["result"] = _pause_campaign(c, cid, p["campaign_id"])
            else:
                raise ValueError(f"unknown kind {it['kind']}")
            it["status"] = "applied" if not dry_run else "pending"
            it["applied"] = dt.datetime.now().isoformat(timespec="seconds")
        except Exception as e:
            it["status"] = "failed"
            it["result"] = str(e)[:500]
        done.append(it)
    save(cfg, items)
    return done


def reject(cfg, ids):
    items = load(cfg)
    for it in items:
        if it["id"] in ids and it["status"] == "pending":
            it["status"] = "rejected"
    save(cfg, items)


def _add_negative(c, cid, campaign_id, keyword, match_type):
    svc = c.get_service("CampaignCriterionService")
    op = c.get_type("CampaignCriterionOperation")
    cr = op.create
    cr.campaign = c.get_service("CampaignService").campaign_path(cid, campaign_id)
    cr.negative = True
    cr.keyword.text = keyword
    cr.keyword.match_type = c.enums.KeywordMatchTypeEnum[match_type]
    res = svc.mutate_campaign_criteria(customer_id=cid, operations=[op])
    return res.results[0].resource_name


def _raise_budget(c, cid, campaign_id, ratio, amount=None):
    ga = c.get_service("GoogleAdsService")
    row = next(ga.search(customer_id=cid, query=f"SELECT campaign_budget.resource_name, campaign_budget.amount_micros FROM campaign WHERE campaign.id = {campaign_id}"))
    cur = row.campaign_budget.amount_micros
    new = int(amount * 1_000_000) if amount else int(cur * ratio)
    svc = c.get_service("CampaignBudgetService")
    op = c.get_type("CampaignBudgetOperation")
    b = op.update
    b.resource_name = row.campaign_budget.resource_name
    b.amount_micros = new
    c.copy_from(op.update_mask, c.get_type("FieldMask")(paths=["amount_micros"]))
    svc.mutate_campaign_budgets(customer_id=cid, operations=[op])
    return f"budget {cur / 1e6:.0f} -> {new / 1e6:.0f}"


def _pause_keyword(c, cid, adgroup_id, criterion_id):
    svc = c.get_service("AdGroupCriterionService")
    op = c.get_type("AdGroupCriterionOperation")
    k = op.update
    k.resource_name = svc.ad_group_criterion_path(cid, adgroup_id, criterion_id)
    k.status = c.enums.AdGroupCriterionStatusEnum.PAUSED
    c.copy_from(op.update_mask, c.get_type("FieldMask")(paths=["status"]))
    svc.mutate_ad_group_criteria(customer_id=cid, operations=[op])
    return "paused"


def _pause_campaign(c, cid, campaign_id):
    svc = c.get_service("CampaignService")
    op = c.get_type("CampaignOperation")
    k = op.update
    k.resource_name = svc.campaign_path(cid, campaign_id)
    k.status = c.enums.CampaignStatusEnum.PAUSED
    c.copy_from(op.update_mask, c.get_type("FieldMask")(paths=["status"]))
    svc.mutate_campaigns(customer_id=cid, operations=[op])
    return "paused"
