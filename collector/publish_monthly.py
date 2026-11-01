#!/usr/bin/env python3
"""
장기 포트 월간 공개 요약 → data/monthly_public.json (평문)

유튜브 월간 결산 영상(예약 작업)이 비밀번호 없이 읽는 파일이에요.
수익률(%)과 월·일별 손익(원)만 담고, 총자산·평가금액·원금·현금·적립 금액·수량·이사 목표는 넣지 않아요.

- collector.py 가 만든 state/state.json 을 읽기만 해요(수집기는 건드리지 않음).
- cron 에서 collector.py 바로 뒤에 붙여 15분마다 실행해도 돼요.
  내용이 바뀌었을 때만, 그리고 3시간에 한 번(또는 날짜·달이 바뀐 첫 실행)만 GitHub 에 올려요.

실행:
  python3 publish_monthly.py            # 계산 + (필요하면) 업로드
  python3 publish_monthly.py --show     # 요약을 화면에 보여 주고 바로 업로드
  python3 publish_monthly.py --no-push  # 계산만 (state/out/monthly_public.json)
"""
import argparse, hashlib, json, sys, time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collector import KST, ROOT, STATE, OUT, jload, jsave, load_env, gh_put, load_past, f  # noqa: E402

CLASS = {"QQQM": "주식", "SCHD": "주식", "MCHI": "주식", "TLT": "채권·금", "IAU": "채권·금", "BTC": "코인", "ETH": "코인"}
NAMES = {"QQQM": "나스닥100", "SCHD": "미국 배당", "TLT": "미국 장기채", "IAU": "금", "MCHI": "중국",
         "BTC": "비트코인", "ETH": "이더리움"}
PXLOG = STATE / "monthly_px.json"     # 종목별 하루 마지막 가격(원화) — 종목 수익률을 가격으로 계산하려고 따로 모아요
PUB = STATE / "monthly_pub.json"      # 마지막 업로드 기록


def r4(x):
    return None if x is None else round(x, 6)


def log_prices(state):
    """state 의 최근 가격(rz_prev)을 일자별로 모아 둠. 원화 기준(달러 종목은 그때 환율 곱함)."""
    log = jload(PXLOG, {})
    hist = state.get("history") or []
    if not hist:
        return log
    d = hist[-1]["d"]
    fx = f(hist[-1].get("fx"), 0)
    day = {}
    for s, p in (state.get("rz_prev") or {}).items():
        px = f(p.get("price"))
        if not px:
            continue
        day[s] = round(px * (fx if p.get("ccy") == "USD" else 1), 2)
    if day:
        log[d] = day
    for k in sorted(log)[:-800]:
        log.pop(k)
    jsave(PXLOG, log)
    return log


def month_of(d):
    return d[:7]


def summarize_month(m, hist, trades, cfg, pxlog, past_info):
    pts = [x for x in hist if month_of(x["d"]) == m]
    if not pts:
        return None
    before = [x for x in hist if x["d"] < pts[0]["d"]]
    base = before[-1] if before else pts[0]           # 첫 달(라이브 시작 달)은 첫 기록이 기준
    seq = ([base] if before else []) + pts

    # ── 일별 손익(원)·수익률: 전날 대비, 그날 입출금 제외
    daily = []
    for a, b in zip(seq, seq[1:]):
        pl = b["v"] - a["v"] - f(b.get("f"))
        r = b["u"] / a["u"] - 1 if a.get("u") else 0
        tday = trades.get(b["d"]) or {}
        daily.append({"d": b["d"], "pl": round(pl), "r": r4(r),
                      "buy": any(v[0] > 0 for v in tday.values())})
    if not daily:
        return {"month": m, "days": 0}

    u0, u1 = base["u"], pts[-1]["u"]
    ret = u1 / u0 - 1
    pl = sum(x["pl"] for x in daily)

    # ── 시장: S&P500(원화 = SPY×환율), S&P500(달러), 코스피
    def bench(key, krw=False):
        b0, b1 = (base.get("b") or {}).get(key), (pts[-1].get("b") or {}).get(key)
        if not b0 or not b1:
            return None
        if krw:
            return r4(b1 * pts[-1]["fx"] / (b0 * base["fx"]) - 1)
        return r4(b1 / b0 - 1)

    def bench_path(key, krw=False):
        b0 = (base.get("b") or {}).get(key)
        out = []
        for x in seq[1:]:
            b = (x.get("b") or {}).get(key)
            out.append(r4(b * (x["fx"] if krw else 1) / (b0 * (base["fx"] if krw else 1)) - 1) if b and b0 else None)
        return out

    me_path = [r4(x["u"] / u0 - 1) for x in seq[1:]]
    race = {"days": [x["d"] for x in seq[1:]], "me": me_path,
            "spx": bench_path("SPY", True), "kospi": bench_path("KOSPI")}
    mkt = {"me": r4(ret), "spx_krw": bench("SPY", True), "spx_usd": bench("SPY"), "kospi": bench("KOSPI")}
    ranked = sorted([(k, v) for k, v in (("me", mkt["me"]), ("spx", mkt["spx_krw"]), ("kospi", mkt["kospi"])) if v is not None],
                    key=lambda kv: -kv[1])
    rank = next((i + 1 for i, (k, _) in enumerate(ranked) if k == "me"), None)

    # ── 종목별 수익률(%) — 가격 기록이 월초부터 있으면 가격으로, 없으면 평가액·매매로 추정
    syms = sorted(set((pts[-1].get("w") or {})) | set((base.get("w") or {})))
    pxd = sorted(k for k in pxlog if k <= pts[-1]["d"])
    px_start = next((pxlog[k] for k in reversed(pxd) if k <= base["d"]), None)
    px_end = pxlog.get(pts[-1]["d"]) or (pxlog[pxd[-1]] if pxd else None)
    hold = []
    for s in syms:
        r, how = None, None
        if px_start and px_end and px_start.get(s) and px_end.get(s):
            r, how = px_end[s] / px_start[s] - 1, "price"
        else:
            w0, w1 = f((base.get("w") or {}).get(s)), f((pts[-1].get("w") or {}).get(s))
            days = [x["d"] for x in seq]
            span = max(1, (datetime.fromisoformat(days[-1]) - datetime.fromisoformat(days[0])).days)
            flows, wsum = 0.0, 0.0
            for d, day in trades.items():
                if not (base["d"] < d <= pts[-1]["d"]) or s not in day:
                    continue
                k = f(day[s][1])
                flows += k
                wsum += k * (datetime.fromisoformat(pts[-1]["d"]) - datetime.fromisoformat(d)).days / span
            den = w0 + wsum
            if den > 0:
                r, how = (w1 - w0 - flows) / den, "dietz"
        if r is not None and abs(r) > 0.6:
            r, how = None, "skip"     # 스테이킹 수량 입력 같은 보정이 섞이면 터무니없는 값이 나옴 → 빼요
        hold.append({"sym": s, "name": NAMES.get(s, s), "cls": CLASS.get(s, "기타"), "ret": r4(r), "how": how})
    hold.sort(key=lambda h: -(h["ret"] if h["ret"] is not None else -9))

    # ── 비중(%) — 금액 대신 비중만
    w = {s: f(v) for s, v in (pts[-1].get("w") or {}).items()}
    inv = sum(w.values()) or 1
    now_w = {s: round(v / inv * 100, 1) for s, v in w.items()}
    cls_w = {}
    for s, v in now_w.items():
        cls_w[CLASS.get(s, "기타")] = round(cls_w.get(CLASS.get(s, "기타"), 0) + v, 1)
    targets = cfg.get("targets") or {}
    tgt_cls = {}
    for s, v in targets.items():
        tgt_cls[CLASS.get(s, "기타")] = round(tgt_cls.get(CLASS.get(s, "기타"), 0) + f(v), 1)
    band = f(cfg.get("band_rel"), 0.25)
    gaps = []
    for s, t in targets.items():
        n = now_w.get(s, 0.0)
        gaps.append({"sym": s, "name": NAMES.get(s, s), "target": f(t), "now": n,
                     "out": abs(n - f(t)) > f(t) * band})

    # ── 낙폭·신고점: 라이브 전체 최고 기준가 대비
    peak = f(cfg.get("nav_peak_seed"), 0)
    for x in before:
        peak = max(peak, x["u"])
    new_highs, mdd, mdd_d = 0, 0.0, None
    for x in pts:
        if x["u"] > peak + 1e-9:
            if before or x is not pts[0]:
                new_highs += 1
            peak = x["u"]
        dd = x["u"] / peak - 1 if peak else 0
        if dd < mdd:
            mdd, mdd_d = dd, x["d"]

    best = max(daily, key=lambda x: x["pl"])
    worst = min(daily, key=lambda x: x["pl"])

    # ── 올해 누적(기준가): 과거 월말 기록이 있으면 작년 12월말부터, 없으면 라이브 시작부터
    y = m[:4]
    ytd = None
    past, long_ = past_info.get("past"), past_info.get("long")
    if past and long_:
        dec = next((p for p in past if p["m"] == f"{int(y) - 1}-12"), None)
        if dec:
            ytd = {"ret": r4(u1 * long_["scale"] / dec["u"] - 1), "from": f"{y}-01"}
            k0 = (dec.get("b") or {}).get("KOSPI")
            k1 = (pts[-1].get("b") or {}).get("KOSPI")
            if k0 and k1:
                ytd["kospi"] = r4(k1 / k0 - 1)
    if ytd is None:
        first = next((x for x in hist if x["d"][:4] == y), None)
        if first:
            ytd = {"ret": r4(u1 / first["u"] - 1), "from": first["d"]}
            k0 = (first.get("b") or {}).get("KOSPI")
            k1 = (pts[-1].get("b") or {}).get("KOSPI")
            if k0 and k1:
                ytd["kospi"] = r4(k1 / k0 - 1)

    last_day = (datetime.fromisoformat(m + "-01") + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return {
        "month": m, "start": base["d"], "end": pts[-1]["d"], "days": len(daily),
        "complete": pts[-1]["d"] >= last_day.date().isoformat() or datetime.now(KST).date() > last_day.date(),
        "ret": r4(ret), "pl": round(pl), "market": mkt, "rank": rank, "race": race,
        "daily": daily, "up_days": sum(1 for x in daily if x["pl"] > 0), "down_days": sum(1 for x in daily if x["pl"] < 0),
        "best": best, "worst": worst, "water_days": sum(1 for x in daily if x["buy"]),
        "holdings": hold, "class_w": cls_w, "class_target": tgt_cls, "gaps": gaps, "band_rel": band,
        "fx": {"start": base.get("fx"), "end": pts[-1].get("fx"),
               "chg": r4(pts[-1]["fx"] / base["fx"] - 1) if base.get("fx") else None},
        "mdd": r4(mdd), "mdd_day": mdd_d, "dd_end": r4(u1 / peak - 1) if peak else None,
        "new_highs": new_highs, "dd_limit": f(cfg.get("dd_limit"), -0.30), "ytd": ytd,
    }


def build(state, cfg):
    hist = state.get("history") or []
    trades = state.get("trades") or {}
    pxlog = log_prices(state)
    try:
        past_info = load_past(cfg, state) or {}
    except Exception as e:
        print(f"  ! 과거 기록 읽기 실패({e}) — 올해 누적은 라이브 시작부터", file=sys.stderr)
        past_info = {}
    months = sorted({month_of(x["d"]) for x in hist})[-13:]
    out = {}
    for m in months:
        s = summarize_month(m, hist, trades, cfg, pxlog, past_info)
        if s:
            out[m] = s
    return {"v": 1, "updated": datetime.now(KST).isoformat(timespec="minutes"),
            "live_start": (state.get("nav") or {}).get("base_date"),
            "note": "수익률·손익만 담은 공개 요약(총자산·원금·수량 없음)", "months": out}


def show(pub):
    for m, s in pub["months"].items():
        if not s.get("days"):
            continue
        mk = s["market"]
        pc = lambda v: "-" if v is None else f"{v * 100:+.2f}%"
        print(f"[{m}] {s['start']}~{s['end']} {s['days']}일 | 나 {pc(mk['me'])} · S&P500(원화) {pc(mk['spx_krw'])} · "
              f"코스피 {pc(mk['kospi'])} → {s['rank']}위 | 손익 {s['pl']:+,}원 | 물 준 날 {s['water_days']} | "
              f"최대낙폭 {pc(s['mdd'])} | 신고점 {s['new_highs']}번")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    load_env()
    cfg = jload(ROOT / "config.json", None) or jload(ROOT / "config.example.json", {})
    state = jload(STATE / "state.json", None)
    if not state:
        sys.exit("state/state.json 이 없어요. collector.py 를 먼저 실행하세요.")
    pub = build(state, cfg)
    OUT.mkdir(parents=True, exist_ok=True)
    jsave(OUT / "monthly_public.json", pub)
    if a.show:
        show(pub)

    body = json.dumps({k: v for k, v in pub.items() if k != "updated"}, ensure_ascii=False, sort_keys=True)
    h = hashlib.sha256(body.encode()).hexdigest()[:16]
    rec = jload(PUB, {})
    now = time.time()
    today = datetime.now(KST).date().isoformat()
    due = a.show or (h != rec.get("hash") and (now - rec.get("t", 0) >= 3 * 3600 or rec.get("day") != today))
    if a.no_push or not due:
        return
    gh_put("data/monthly_public.json", json.dumps(pub, ensure_ascii=False, indent=1).encode("utf-8"),
           f"monthly: {datetime.now(KST):%m-%d %H:%M}")
    jsave(PUB, {"hash": h, "t": now, "day": today})
    print("  ↑ 월간 요약 업로드 완료")


if __name__ == "__main__":
    main()
