import argparse
import json
import os
import sys

from . import __version__
from .config import Config, EXAMPLE
from .util import dump_json, parse_date, ymd


def _cfg(args):
    if not os.path.exists(args.config):
        sys.exit(f"設定ファイルがありません: {args.config}（kadsai init で雛形を作れます）")
    return Config(args.config)


def cmd_init(args):
    if os.path.exists(args.config) and not args.force:
        sys.exit(f"{args.config} はすでにあります（上書きは --force）")
    dump_json(args.config, EXAMPLE)
    print(f"雛形を書きました: {args.config}。customer_id と google_ads_yaml を書き換えてください。")


def cmd_fetch(args):
    from .fetch import fetch
    cfg = _cfg(args)
    res = fetch(cfg, days=args.days, date_to=parse_date(args.date), sources=args.only)
    print(json.dumps(res, ensure_ascii=False, indent=1))


def cmd_import(args):
    from .csvimport import import_csv
    cfg = _cfg(args)
    n = import_csv(cfg, args.file, args.kind, date=args.date)
    print(f"取り込み {n}行（{args.kind}）")


def _analyze(cfg, date, with_ai=False):
    from . import proposals, report
    from .rules import analyze
    summary, findings = analyze(cfg, date)
    ds = ymd(date)
    ai_text = None
    if with_ai:
        from .ai import summarize
        ai_text = summarize(cfg, report.markdown(cfg, summary, findings), force=True)
    paths = report.write_all(cfg, ds, summary, findings, ai_text)
    dump_json(os.path.join(cfg.outputs, "reports", ds, "findings.json"),
              dict(summary=summary, findings=findings, ai=ai_text))
    added, _ = proposals.collect(cfg, ds, findings)
    return summary, findings, ai_text, paths, added


def cmd_analyze(args):
    cfg = _cfg(args)
    summary, findings, ai_text, paths, added = _analyze(cfg, parse_date(args.date), with_ai=args.ai)
    print(open(paths["report.md"], encoding="utf-8").read())
    print(f"\n[出力] {paths['report.html']}\n[提案] 新規 {added}件 → kadsai proposals で確認")


def cmd_summarize(args):
    from .ai import summarize
    cfg = _cfg(args)
    ds = ymd(parse_date(args.date))
    p = os.path.join(cfg.outputs, "reports", ds, "report.md")
    if not os.path.exists(p):
        sys.exit(f"先に analyze を実行してください（{p} がありません）")
    txt = summarize(cfg, open(p, encoding="utf-8").read(), force=True)
    print(txt or "（AI は設定で無効です。ai.enabled を true に）")


def cmd_notify(args):
    from .notify import email_send, line_send
    cfg = _cfg(args)
    ds = ymd(parse_date(args.date))
    p = os.path.join(cfg.outputs, "reports", ds, "short.txt")
    if not os.path.exists(p):
        sys.exit(f"先に analyze を実行してください（{p} がありません）")
    text = open(p, encoding="utf-8").read()
    r1 = line_send(cfg, text)
    r2 = email_send(cfg, f"広告日報 {ds}", text)
    print("LINE:", r1 or "無効", "\nメール:", r2 or "無効")


def cmd_run(args):
    """毎朝これ1つ。fetch → analyze（AI は設定次第）→ notify。"""
    from .fetch import fetch
    from .notify import email_send, line_send
    cfg = _cfg(args)
    date = parse_date(args.date)
    if not args.no_fetch:
        print(json.dumps(fetch(cfg, days=args.days, date_to=date), ensure_ascii=False))
    with_ai = bool(cfg.ai.get("enabled")) and not args.no_ai
    summary, findings, ai_text, paths, added = _analyze(cfg, date, with_ai=with_ai)
    text = open(paths["short.txt"], encoding="utf-8").read()
    print(text)
    print(f"\n[出力] {paths['report.html']}  [提案] 新規 {added}件")
    if not args.no_notify:
        try:
            print("LINE:", line_send(cfg, text) or "無効")
            print("メール:", email_send(cfg, f"広告日報 {ymd(date)}", text) or "無効")
        except Exception as e:
            print("通知失敗:", e, file=sys.stderr)
            sys.exit(2)


def cmd_proposals(args):
    from . import proposals
    cfg = _cfg(args)
    items = proposals.load(cfg)
    if args.apply:
        for it in proposals.apply(cfg, set(args.apply), dry_run=args.dry_run):
            print(f"#{it['id']} {it['kind']} → {it['status']} {it.get('result', '')}")
        return
    if args.reject:
        proposals.reject(cfg, set(args.reject))
        print("却下:", args.reject)
        return
    show = [i for i in items if args.all or i["status"] == "pending"]
    if not show:
        print("承認待ちの提案はありません。")
    for it in show:
        print(f"#{it['id']} [{it['status']}] {it['kind']} {json.dumps(it['params'], ensure_ascii=False)}\n   {it['reason']}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kadsai", description="Kurage 広告AI分析。Google 広告を取得し、規則で分析して日報を出す。")
    ap.add_argument("-c", "--config", default="kadsai.json")
    ap.add_argument("--version", action="version", version=__version__)
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("init", help="設定の雛形を書く"); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_init)
    p = sp.add_parser("fetch", help="Google Ads API から取り込む")
    p.add_argument("--days", type=int, default=35); p.add_argument("--date", default="yesterday", help="終了日（既定は昨日）")
    p.add_argument("--only", nargs="*", help="campaign adgroup keyword search_term ad hour geo negative"); p.set_defaults(fn=cmd_fetch)
    p = sp.add_parser("import", help="管理画面の CSV を取り込む（API が無い口座向け）")
    p.add_argument("file"); p.add_argument("--kind", required=True, choices=["search_terms", "campaign", "keyword", "adgroup"])
    p.add_argument("--date", help="CSV に日付列が無いときの日付"); p.set_defaults(fn=cmd_import)
    p = sp.add_parser("analyze", help="規則で分析して日報を書く"); p.add_argument("--date", default="yesterday")
    p.add_argument("--ai", action="store_true", help="AI 要約も付ける（設定の ai を使う）"); p.set_defaults(fn=cmd_analyze)
    p = sp.add_parser("summarize", help="日報を AI で要約する"); p.add_argument("--date", default="yesterday"); p.set_defaults(fn=cmd_summarize)
    p = sp.add_parser("notify", help="日報を LINE・メールで送る"); p.add_argument("--date", default="yesterday"); p.set_defaults(fn=cmd_notify)
    p = sp.add_parser("run", help="毎朝の一括: fetch → analyze → notify")
    p.add_argument("--date", default="yesterday"); p.add_argument("--days", type=int, default=35)
    p.add_argument("--no-fetch", action="store_true"); p.add_argument("--no-ai", action="store_true"); p.add_argument("--no-notify", action="store_true")
    p.set_defaults(fn=cmd_run)
    p = sp.add_parser("proposals", help="設定変更の提案を見る・承認して実行する・却下する")
    p.add_argument("--all", action="store_true"); p.add_argument("--apply", nargs="*", type=int, metavar="ID")
    p.add_argument("--reject", nargs="*", type=int, metavar="ID"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_proposals)

    args = ap.parse_args(argv)
    args.fn(args)
