#!/usr/bin/env python3
"""
WhiteCoffee 장기 포트 라이브 수집기
- 토스증권 Open API(미국 ETF) + 업비트 Open API(BTC/ETH) 잔고를 읽어
- NAV(좌수법) 기준가, 환율 분해, 낙폭을 계산하고
- 비밀번호로 암호화한 JSON 한 개(data/portfolio.enc.json)를 GitHub에 올립니다.

API 키는 이 서버(.env)에만 있고, 대시보드에는 암호화된 결과만 올라갑니다.

실행:
  python3 collector.py            # 실제 수집 + GitHub 푸시
  python3 collector.py --no-push  # 수집만 (로컬 data/ 에 저장)
  python3 collector.py --mock     # API 없이 샘플 데이터로 테스트
  python3 collector.py --check    # 키·IP 연결만 점검
"""
import argparse, base64, hashlib, json, os, sys, time, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import warnings
warnings.filterwarnings("ignore", message=".*HMAC key.*")  # 업비트 키 길이 안내 문구 숨김

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
OUT = STATE / "out"   # 로컬 사본(저장소 폴더를 건드리지 않아 git pull 충돌이 없음)
TOSS = "https://openapi.tossinvest.com"
UPBIT = "https://api.upbit.com"


# ───────────────────────── 공통 ─────────────────────────
def load_env():
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def jload(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def jsave(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(p)


def f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def now_kst():
    return datetime.now(KST)


# ───────────────────────── 토스증권 ─────────────────────────
class Toss:
    def __init__(self, cid, secret):
        self.cid, self.secret = cid, secret
        self.tok_path = STATE / "toss_token.json"
        self.s = requests.Session()
        self.seq = None

    def token(self, force=False):
        # client 당 유효 토큰은 1개 — 재발급하면 이전 토큰이 즉시 무효가 되므로 캐시해서 재사용
        t = jload(self.tok_path, {})
        if not force and t.get("access_token") and t.get("exp", 0) > time.time() + 300:
            return t["access_token"]
        r = self.s.post(f"{TOSS}/oauth2/token", timeout=15, data={
            "grant_type": "client_credentials", "client_id": self.cid, "client_secret": self.secret})
        if r.status_code == 403:
            raise RuntimeError("토스: 허용되지 않은 IP예요. 토스증권 WTS > 설정 > Open API > 허용 IP 관리에 이 서버 IP를 등록하세요.")
        r.raise_for_status()
        j = r.json()
        jsave(self.tok_path, {"access_token": j["access_token"], "exp": time.time() + int(j.get("expires_in", 86400))})
        os.chmod(self.tok_path, 0o600)
        return j["access_token"]

    def get(self, path, params=None, account=False, _retry=True):
        h = {"Authorization": f"Bearer {self.token()}"}
        if account:
            h["X-Tossinvest-Account"] = str(self.account_seq())
        r = self.s.get(f"{TOSS}{path}", params=params, headers=h, timeout=15)
        if r.status_code == 401 and _retry:
            self.token(force=True)
            return self.get(path, params, account, _retry=False)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", "2")))
            return self.get(path, params, account, _retry=False)
        r.raise_for_status()
        return r.json()["result"]

    def account_seq(self):
        if self.seq is None:
            fixed = os.environ.get("TOSS_ACCOUNT_SEQ")
            if fixed:
                self.seq = int(fixed)
            else:
                accts = self.get("/api/v1/accounts")
                if not accts:
                    raise RuntimeError("토스: 종합매매 계좌가 없어요.")
                self.seq = accts[0]["accountSeq"]
        return self.seq

    def fx(self):
        r = self.get("/api/v1/exchange-rate", {"baseCurrency": "USD", "quoteCurrency": "KRW"})
        return {"rate": f(r.get("rate")), "mid": f(r.get("midRate")), "bp": f(r.get("basisPoint")),
                "dir": r.get("rateChangeType")}

    def holdings(self):
        return self.get("/api/v1/holdings", account=True)

    def fx_history(self, weeks=104):
        """과거 환율(주 1회, 최근 2년) — 환율 온도계용. 처음 한 번만 채움"""
        out, now = [], now_kst()
        for i in range(weeks, 0, -1):
            t = (now - timedelta(days=7 * i)).replace(hour=15, minute=30, second=0, microsecond=0)
            try:
                r = self.get("/api/v1/exchange-rate", {"baseCurrency": "USD", "quoteCurrency": "KRW",
                                                      "dateTime": t.isoformat()})
                out.append({"d": t.date().isoformat(), "r": round(f(r.get("rate")), 2)})
            except Exception:
                pass
            time.sleep(0.4)  # MARKET_INFO 초당 3회 제한
        return [x for x in out if x["r"]]

    def bench(self):
        """비교용 시장 지표: KOSPI(지수), SPY(S&P500 추종 ETF, 달러)"""
        out = {}
        try:
            for x in self.get("/api/v1/market-indicators/prices", {"symbols": "KOSPI"}):
                out[x["symbol"]] = f(x.get("lastPrice"))
        except Exception as e:
            print(f"  ! KOSPI 조회 실패({e})", file=sys.stderr)
        try:
            for x in self.get("/api/v1/prices", {"symbols": "SPY"}):
                out["SPY"] = f(x.get("lastPrice"))
        except Exception as e:
            print(f"  ! SPY 조회 실패({e})", file=sys.stderr)
        return {k: v for k, v in out.items() if v}

    def cash(self):
        out = {}
        for c in ("KRW", "USD"):
            try:
                out[c] = f(self.get("/api/v1/buying-power", {"currency": c}, account=True).get("cashBuyingPower"))
            except Exception as e:  # 키 권한에 '주문 정보'가 없으면 실패할 수 있음
                print(f"  ! 토스 {c} 현금 조회 실패({e}) → config의 cash_override 사용", file=sys.stderr)
                out[c] = None
        return out


# ───────────────────────── 업비트 ─────────────────────────
class Upbit:
    def __init__(self, ak, sk):
        self.ak, self.sk = ak, sk

    def _auth(self):
        import jwt
        tok = jwt.encode({"access_key": self.ak, "nonce": str(uuid.uuid4())}, self.sk, algorithm="HS512")
        return {"Authorization": f"Bearer {tok}"}

    def accounts(self):
        r = requests.get(f"{UPBIT}/v1/accounts", headers=self._auth(), timeout=15)
        if r.status_code == 401 and "no_authorization_ip" in r.text:
            raise RuntimeError("업비트: 등록되지 않은 IP예요. 업비트 > MY > Open API 관리에서 이 서버 IP로 키를 다시 발급하세요.")
        r.raise_for_status()
        return r.json()

    @staticmethod
    def ticker(markets):
        if not markets:
            return {}
        # 상장폐지·원화마켓 없는 코인이 하나라도 섞이면 업비트가 404를 주므로 먼저 걸러냄
        try:
            live = {m["market"] for m in requests.get(f"{UPBIT}/v1/market/all", timeout=15).json()}
            markets = [m for m in markets if m in live]
        except Exception:
            pass
        if not markets:
            return {}
        r = requests.get(f"{UPBIT}/v1/ticker", params={"markets": ",".join(markets)}, timeout=15)
        r.raise_for_status()
        return {x["market"]: x for x in r.json()}


# ───────────────────────── 수집 ─────────────────────────
def collect_live(cfg, state):
    toss = Toss(os.environ["TOSS_CLIENT_ID"], os.environ["TOSS_CLIENT_SECRET"])
    up = Upbit(os.environ["UPBIT_ACCESS_KEY"], os.environ["UPBIT_SECRET_KEY"])

    fx = toss.fx()
    if not state.get("fx_hist_done"):
        print("  · 환율 기록 2년치 처음 채우는 중(약 1분, 한 번만)…")
        state["fx_hist"] = toss.fx_history()
        state["fx_hist_done"] = True
    H = toss.holdings()
    cash = toss.cash()
    rows = []
    for it in H.get("items", []):
        rows.append({
            "sym": it["symbol"], "name": it.get("name", it["symbol"]), "broker": "toss",
            "ccy": it.get("currency", "USD"), "qty": f(it["quantity"]),
            "price": f(it["lastPrice"]), "avg": f(it["averagePurchasePrice"]),
            "day_rate": f((it.get("dailyProfitLoss") or {}).get("rate")),
            "day_pl": f((it.get("dailyProfitLoss") or {}).get("amount")),
        })

    accts = up.accounts()
    upbit_krw = 0.0
    coins = {}
    for a in accts:
        bal = f(a["balance"]) + f(a.get("locked"))
        if a["currency"] == "KRW":
            upbit_krw = bal
        elif a.get("unit_currency") == "KRW" and bal > 0:
            coins[a["currency"]] = [bal, f(a.get("avg_buy_price"))]
    # 스테이킹 중인 코인은 업비트 잔고 API에 안 잡혀서 config.json의 staked 수량을 더함
    staked = {k: (v if isinstance(v, dict) else {"qty": v}) for k, v in (cfg.get("staked") or {}).items()}
    for c in staked:
        coins.setdefault(c, [0.0, 0.0])
    tick = Upbit.ticker([f"KRW-{c}" for c in coins])
    for c, (bal, avg) in coins.items():
        t = tick.get(f"KRW-{c}")
        if not t:
            continue  # 원화마켓 상장폐지/에어드랍 잔여물 등
        price = f(t["trade_price"])
        sq = f(staked.get(c, {}).get("qty"))
        sa = f(staked.get(c, {}).get("avg")) or avg or price   # 평단 모르면 현재가로(손익 0으로 시작)
        q = bal + sq
        if q * price < cfg.get("dust_krw", 5000):
            continue
        avg_all = (bal * (avg or price) + sq * sa) / q if q else avg
        rows.append({"sym": c, "name": c, "broker": "upbit", "ccy": "KRW", "qty": q, "price": price,
                     "avg": avg_all, "stk": sq, "day_rate": f(t.get("signed_change_rate")),
                     "day_pl": q * f(t.get("signed_change_price"))})

    ov = cfg.get("cash_override") or {}
    return {
        "fx": fx, "rows": rows, "bench": toss.bench(),
        "cash": {
            "toss_krw": cash["KRW"] if cash["KRW"] is not None else f(ov.get("toss_krw")),
            "toss_usd": cash["USD"] if cash["USD"] is not None else f(ov.get("toss_usd")),
            "upbit_krw": upbit_krw,
        },
    }


def collect_mock(cfg, state):
    """API 없이 화면 테스트용 샘플. 실행할 때마다 조금씩 출렁이게."""
    import random
    rnd = random.Random()
    fx0 = state.get("mock_fx", 1392.0) * (1 + rnd.uniform(-0.002, 0.002))
    state["mock_fx"] = fx0
    base = state.get("mock_px") or {"QQQM": 248.3, "SCHD": 27.9, "TLT": 86.4, "IAU": 71.2, "MCHI": 61.8,
                                     "BTC": 158_400_000, "ETH": 5_910_000}
    px = {k: v * (1 + rnd.gauss(0, 0.004 if k not in ("BTC", "ETH") else 0.01)) for k, v in base.items()}
    state["mock_px"] = px
    qty = {"QQQM": 172, "SCHD": 690, "TLT": 165, "IAU": 128, "MCHI": 60, "BTC": 0.165, "ETH": 0.42}
    avg = {"QQQM": 201.5, "SCHD": 26.1, "TLT": 93.2, "IAU": 52.4, "MCHI": 47.9, "BTC": 121_000_000, "ETH": 6_400_000}
    names = {"QQQM": "Invesco NASDAQ 100 ETF", "SCHD": "Schwab US Dividend Equity ETF",
             "TLT": "iShares 20+ Year Treasury Bond ETF", "IAU": "iShares Gold Trust", "MCHI": "iShares MSCI China ETF"}
    rows = []
    for s in ("QQQM", "SCHD", "TLT", "IAU", "MCHI"):
        dr = px[s] / base[s] - 1
        rows.append({"sym": s, "name": names[s], "broker": "toss", "ccy": "USD", "qty": qty[s], "price": px[s],
                     "avg": avg[s], "day_rate": dr, "day_pl": qty[s] * (px[s] - base[s])})
    for s in ("BTC", "ETH"):
        dr = px[s] / base[s] - 1
        rows.append({"sym": s, "name": s, "broker": "upbit", "ccy": "KRW", "qty": qty[s], "price": px[s],
                     "avg": avg[s], "day_rate": dr, "day_pl": qty[s] * (px[s] - base[s])})
    state["mock_b"] = {k: v * (1 + rnd.gauss(0, .006)) for k, v in (state.get("mock_b") or {"KOSPI": 3420.0, "SPY": 668.0}).items()}
    return {"fx": {"rate": fx0, "mid": fx0, "bp": 0, "dir": "FLAT"}, "rows": rows, "bench": state["mock_b"],
            "cash": {"toss_krw": 412_000, "toss_usd": 318.4, "upbit_krw": 96_500}}


# ───────────────────────── 계산 ─────────────────────────
def compute(raw, cfg, state, ts):
    fx = raw["fx"]["rate"]
    seeds = {k: v for k, v in (cfg.get("krw_cost_seed") or {}).items() if f(v) > 0}
    lots = state.setdefault("lots", {})  # sym -> {qty, cost_usd, cost_krw, seeded}

    holds = []
    for r in raw["rows"]:
        s = r["sym"]
        usd = r["ccy"] == "USD"
        price_krw = r["price"] * (fx if usd else 1)
        value_krw = r["qty"] * price_krw
        cost_ccy = r["qty"] * r["avg"]

        if usd:
            # 원화 원금 추적: 처음엔 config 시드(토스 앱 '원화 기준' 원금), 이후 추가매수는 그날 환율로 누적
            L = lots.get(s)
            if L is None:
                if s in seeds:
                    L = {"qty": r["qty"], "cost_usd": cost_ccy, "cost_krw": f(seeds[s]), "seeded": True}
                else:
                    L = {"qty": r["qty"], "cost_usd": cost_ccy, "cost_krw": cost_ccy * fx, "seeded": False}
            else:
                if s in seeds and not L.get("seeded"):  # 나중에 시드를 채워 넣은 경우
                    L.update(cost_krw=f(seeds[s]), seeded=True)
                dq = r["qty"] - L["qty"]
                if dq > 1e-9:  # 추가 매수
                    L["cost_krw"] += max(cost_ccy - L["cost_usd"], 0) * fx
                elif dq < -1e-9 and L["qty"] > 0:  # 일부 매도 → 비례 차감
                    L["cost_krw"] *= r["qty"] / L["qty"]
                L["qty"], L["cost_usd"] = r["qty"], cost_ccy
            lots[s] = L
            cost_krw = L["cost_krw"]
            buy_fx = cost_krw / cost_ccy if cost_ccy else fx
            value_usd = r["qty"] * r["price"]
            price_eff = (value_usd - cost_ccy) * buy_fx
            fx_eff = value_usd * (fx - buy_fx)
            day_pl_krw = r["day_pl"] * fx
            seeded = L.get("seeded", False)
        else:
            cost_krw = cost_ccy
            buy_fx = None
            price_eff = value_krw - cost_krw
            fx_eff = 0.0
            day_pl_krw = r["day_pl"]
            seeded = True

        holds.append({
            "sym": s, "name": r["name"], "broker": r["broker"], "ccy": r["ccy"],
            "qty": round(r["qty"], 8), "price": r["price"], "avg": r["avg"],
            "price_krw": round(price_krw, 2), "value_krw": round(value_krw),
            "cost_krw": round(cost_krw), "pl_krw": round(value_krw - cost_krw),
            "pl_rate": (value_krw / cost_krw - 1) if cost_krw else 0,
            "pl_rate_local": (r["price"] / r["avg"] - 1) if r["avg"] else 0,
            "price_eff": round(price_eff), "fx_eff": round(fx_eff),
            "buy_fx": round(buy_fx, 2) if buy_fx else None, "fx_seeded": seeded,
            "day_rate": r["day_rate"], "day_pl_krw": round(day_pl_krw),
            "stk": r.get("stk", 0),
        })
    # 사라진 종목(전량 매도) 정리
    for s in list(lots):
        if s not in {h["sym"] for h in holds}:
            lots.pop(s)

    c = raw["cash"]
    cash_krw = f(c["toss_krw"]) + f(c["toss_usd"]) * fx + f(c["upbit_krw"])
    invest = sum(h["value_krw"] for h in holds)
    total = invest + cash_krw

    # ── NAV(좌수법): 입금·추가매수는 좌수 증가, 시장 움직임만 기준가에 반영
    prev = state.get("nav_prev")
    nav = state.setdefault("nav", {})
    flow = 0.0
    if not prev or not nav.get("units"):
        nav.update(units=total / 1000.0, base_date=ts.date().isoformat())
        unit = 1000.0
    else:
        mkt = 0.0
        px_now = {h["sym"]: h["price_krw"] for h in holds}
        for s, (q, p) in prev["px"].items():
            if s in px_now:
                mkt += q * (px_now[s] - p)
        mkt += prev.get("usd_cash", 0) * (fx - prev.get("fx", fx))
        flow = (total - prev["total"]) - mkt
        if abs(flow) < cfg.get("flow_threshold_krw", 3000):
            flow = 0.0  # 수수료·반올림 수준은 성과로 처리
        pre_unit = (total - flow) / nav["units"] if nav["units"] else 1000.0
        if flow:
            nav["units"] += flow / pre_unit
        unit = total / nav["units"] if nav["units"] else 1000.0
    state["nav_prev"] = {"total": total, "fx": fx, "usd_cash": f(c["toss_usd"]),
                         "px": {h["sym"]: [h["qty"], h["price_krw"]] for h in holds}}

    if flow:
        fl = state.setdefault("flows", [])
        fl.append({"t": ts.isoformat(timespec="minutes"), "a": round(flow)})
        state["flows"] = fl[-400:]

    # 히스토리 (KST 일자별 마지막 값 = 그날 종가)
    hist = state.setdefault("history", [])
    d = ts.date().isoformat()
    pt = {"d": d, "v": round(total), "u": round(unit, 4), "fx": round(fx, 2),
          "w": {h["sym"]: h["value_krw"] for h in holds}, "cash": round(cash_krw)}
    if raw.get("bench"):
        pt["b"] = {k: round(v, 2) for k, v in raw["bench"].items()}
    if hist and hist[-1]["d"] == d:
        f_prev = hist[-1].get("f", 0)
        pt["f"] = f_prev + round(flow)
        hist[-1] = pt
    else:
        pt["f"] = round(flow)
        hist.append(pt)

    # ── 매수·매도 기록: 종목 수량이 늘면 그날 매수로 기록 (농부 레벨·매수 영수증용)
    px_krw = {h["sym"]: h["price_krw"] for h in holds}
    curq = {h["sym"]: round(h["qty"] - (h.get("stk") or 0), 8) for h in holds}  # 스테이킹분은 매수 기록에서 제외
    curs = {h["sym"]: float(h.get("stk") or 0) for h in holds if h.get("stk")}
    prevq, prevs = state.get("qty"), state.get("stk_q", {})
    trades = state.setdefault("trades", {})
    if prevq is not None:
        for sym in set(curq) | set(prevq):
            dq = curq.get(sym, 0) - prevq.get(sym, 0)
            ds = curs.get(sym, 0) - prevs.get(sym, 0)
            if dq < 0 and ds > 0:            # 현물 → 스테이킹으로 옮긴 건 매도가 아님
                dq += min(ds, -dq)
            if abs(dq) > 1e-9:
                day = trades.setdefault(d, {})
                q0, k0 = day.get(sym, [0, 0])
                day[sym] = [round(q0 + dq, 8), round(k0 + dq * px_krw.get(sym, 0))]
    state["qty"], state["stk_q"] = curq, curs
    for k in sorted(trades)[:-400]:
        trades.pop(k)

    # ── 원금 기준선(적립 vs 시장 분해용): 시작 시점의 매입원가+현금, 이후 입출금은 flows로 더함
    if "principal0" not in nav:
        nav["principal0"] = round(sum(h["cost_krw"] for h in holds) + cash_krw - sum(x["a"] for x in state.get("flows", [])))

    intr = state.setdefault("intraday", [])
    intr.append({"t": ts.isoformat(timespec="minutes"), "v": round(total), "u": round(unit, 4)})
    cutoff = (ts - timedelta(hours=48)).isoformat()
    state["intraday"] = [x for x in intr if x["t"] >= cutoff[:16]]

    units_hist = [x["u"] for x in hist] + [round(unit, 4)]
    peak = max(units_hist + [f(cfg.get("nav_peak_seed"), 0)])
    peak_d = next((x["d"] for x in hist if x["u"] == peak), d)

    return {
        "asof": ts.isoformat(timespec="seconds"),
        "fx": raw["fx"],
        "holdings": sorted(holds, key=lambda h: -h["value_krw"]),
        "cash": {"toss_krw": round(f(c["toss_krw"])), "toss_usd": round(f(c["toss_usd"]), 2),
                 "upbit_krw": round(f(c["upbit_krw"])), "total_krw": round(cash_krw)},
        "total_krw": round(total), "invest_krw": round(invest),
        "cost_krw": round(sum(h["cost_krw"] for h in holds)),
        "day_pl_krw": round(sum(h["day_pl_krw"] for h in holds)),
        "nav": {"unit": round(unit, 4), "units": nav["units"], "base_date": nav.get("base_date"),
                "peak": round(peak, 4), "peak_date": peak_d, "dd": unit / peak - 1 if peak else 0,
                "principal0": nav.get("principal0")},
        "trades": {k: trades[k] for k in sorted(trades)[-180:]},
        "fx_hist": state.get("fx_hist", []),
        "flows": state.get("flows", [])[-400:],
        "history": hist,
        "intraday": state["intraday"],
        "config": {k: cfg.get(k) for k in ("targets", "band_rel", "goal", "monthly_dca", "dd_limit",
                                           "under_weight_mult", "scenarios", "daily_dca", "dca_days")},
    }


# ───────────────────────── 암호화 & 업로드 ─────────────────────────
def encrypt(obj, passphrase, iters=210_000):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv = os.urandom(16), os.urandom(12)
    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iters, 32)
    ct = AESGCM(key).encrypt(iv, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), None)
    b = lambda x: base64.b64encode(x).decode()
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": iters, "salt": b(salt), "iv": b(iv), "ct": b(ct),
            "updated": obj["asof"]}


def gh_put(path, content_bytes, message):
    repo, tok = os.environ["GH_REPO"], os.environ["GH_TOKEN"]
    branch = os.environ.get("GH_BRANCH", "main")
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    r = requests.get(url, headers=h, params={"ref": branch}, timeout=20)
    sha = r.json().get("sha") if r.status_code == 200 else None
    body = {"message": message, "branch": branch, "content": base64.b64encode(content_bytes).decode()}
    if sha:
        body["sha"] = sha
    r = requests.put(url, headers=h, json=body, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"GitHub 업로드 실패 {r.status_code}: {r.text[:200]}")


# ───────────────────────── 메인 ─────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--plain", action="store_true", help="암호화 안 한 JSON도 로컬에 저장(디버그)")
    a = ap.parse_args()

    load_env()
    cfg = jload(ROOT / "config.json", None) or jload(ROOT / "config.example.json", {})
    STATE.mkdir(exist_ok=True)
    state_path = STATE / ("state.mock.json" if a.mock else "state.json")
    state = jload(state_path, {})
    ts = now_kst()

    if a.check:
        ip = requests.get("https://api.ipify.org", timeout=10).text
        print(f"이 서버 공인 IP: {ip}  ← 토스·업비트에 이 IP를 등록하세요")
        t = Toss(os.environ["TOSS_CLIENT_ID"], os.environ["TOSS_CLIENT_SECRET"])
        print("토스 환율:", t.fx()["rate"], "| 계좌 seq:", t.account_seq(),
              "| 보유종목:", len(t.holdings().get("items", [])))
        print("업비트 자산:", [x["currency"] for x in Upbit(os.environ["UPBIT_ACCESS_KEY"], os.environ["UPBIT_SECRET_KEY"]).accounts()])
        print("✅ 연결 OK")
        return

    raw = collect_mock(cfg, state) if a.mock else collect_live(cfg, state)
    snap = compute(raw, cfg, state, ts)
    jsave(state_path, state)
    os.chmod(state_path, 0o600)

    pw = os.environ.get("DASH_PASSPHRASE") or ("demo" if a.mock else None)
    if not pw:
        sys.exit("DASH_PASSPHRASE 가 .env에 없어요.")
    enc = encrypt(snap, pw)
    OUT.mkdir(exist_ok=True)
    name = "portfolio.mock.enc.json" if a.mock else "portfolio.enc.json"
    jsave(OUT / name, enc)
    if a.plain:
        jsave(OUT / name.replace(".enc", ".plain"), snap)

    print(f"[{ts:%m-%d %H:%M}] 총 {snap['total_krw']:,}원 | 기준가 {snap['nav']['unit']:.2f} "
          f"| 낙폭 {snap['nav']['dd']*100:.1f}% | 환율 {snap['fx']['rate']:.1f}")

    if not a.no_push and not a.mock:
        gh_put("data/portfolio.enc.json", json.dumps(enc).encode(), f"data: {ts:%m-%d %H:%M}")
        print("  ↑ GitHub 업로드 완료")


if __name__ == "__main__":
    main()
