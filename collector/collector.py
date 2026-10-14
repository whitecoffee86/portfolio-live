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

과거 월별 기록: collector/history.csv (서버에만 둠) → 대시보드가 2025-04부터 이어서 보여줌
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


# ───────────────────────── 스테이킹 자동 추적 ─────────────────────────
def track_staking(cfg, state, coins, upbit_krw, tick):
    """스테이킹 수량을 state 에서 따라가요. config.json 의 staked 에 적힌 코인만 대상.

    1) 현물이 줄었는데 업비트 원화가 (판 만큼) 안 늘었다 → 현물을 스테이킹으로 옮긴 것
       → 줄어든 수량을 스테이킹에 더함. 총자산이 그대로라 입출금으로 안 잡힘.
    2) config 의 staked 수량을 사용자가 바꿨다 → 앱의 '총 스테이킹 수량'으로 맞춘 것
       → 그 수량을 기준으로 삼고, 늘어난 만큼(=보상)은 입금이 아니라 수익으로 처리.

    반환: (staked {코인: {"qty","avg"}}, 보정액 원)  보정액은 compute 가 입출금 감지에서 빼요.
    """
    cfg_st = {k: (v if isinstance(v, dict) else {"qty": v}) for k, v in (cfg.get("staked") or {}).items()}
    trk = state.setdefault("stk_track", {})
    prev = state.get("upbit_prev") or {}
    p_spot, p_krw = prev.get("spot") or {}, prev.get("krw")
    thr = cfg.get("flow_threshold_krw", 3000)
    log = state.setdefault("stk_log", [])
    stamp = now_kst().isoformat(timespec="minutes")
    adj = 0.0

    for c, cv in cfg_st.items():
        cq, ca = f(cv.get("qty")), f(cv.get("avg"))
        price = f((tick.get(f"KRW-{c}") or {}).get("trade_price"))
        bal, spot_avg = coins.get(c, [0.0, 0.0])
        T = trk.get(c)
        if T is None:  # 처음: config 값을 그대로 기준으로
            T = {"qty": cq, "avg": ca, "cfg": cq, "cfg_avg": ca}
            trk[c] = T

        # ① 현물 → 스테이킹 이동 감지
        moved = 0.0
        if c in p_spot and p_krw is not None and price:
            d = f(p_spot[c]) - bal
            if d > 0 and d * price >= thr:
                got = upbit_krw - f(p_krw)          # 팔았다면 원화가 거의 d×가격만큼 늘어야 함
                if got < 0.5 * d * price:
                    moved = d
                    a_in = spot_avg or f(prev.get("avg", {}).get(c)) or price
                    q0, a0 = T["qty"], (T.get("avg") or a_in)
                    T["avg"] = (q0 * a0 + d * a_in) / (q0 + d)
                    T["qty"] = q0 + d
                    log.append({"t": stamp, "c": c, "k": "move", "q": round(d, 8)})
                    print(f"  🔒 {c} {d:.6f}개 현물 → 스테이킹 이동 감지 (입출금 아님)")

        # ② config 수량을 바꿨으면 그 값으로 맞춤, 차이는 보상(수익)
        if abs(cq - f(T.get("cfg"))) > 1e-9:
            reward = cq - T["qty"]   # ①에서 이미 더한 이동분은 빠짐
            if price:
                adj += reward * price
            T.update(qty=cq, cfg=cq)
            log.append({"t": stamp, "c": c, "k": "sync", "q": round(reward, 8)})
            print(f"  🎁 {c} 스테이킹 수량 맞춤 {cq:.6f}개 (차이 {reward:+.6f}개 → 수익으로 처리)")
        if ca and abs(ca - f(T.get("cfg_avg"))) > 1e-9:
            T.update(avg=ca, cfg_avg=ca)

    state["stk_log"] = log[-100:]
    state["upbit_prev"] = {"krw": upbit_krw, "spot": {c: v[0] for c, v in coins.items()},
                           "avg": {c: v[1] for c, v in coins.items()}}
    return {c: {"qty": T["qty"], "avg": T.get("avg")} for c, T in trk.items() if c in cfg_st}, adj


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
    # 스테이킹 중인 코인은 업비트 잔고 API에 안 잡힘 → track_staking 이 수량을 따라감
    #   (config.json 의 staked 에 적힌 코인만 대상)
    for c in (cfg.get("staked") or {}):
        coins.setdefault(c, [0.0, 0.0])
    tick = Upbit.ticker([f"KRW-{c}" for c in coins])
    staked, stk_adj = track_staking(cfg, state, coins, upbit_krw, tick)
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
        "fx": fx, "rows": rows, "bench": toss.bench(), "stk_adj": stk_adj,
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


# ───────────────────────── 실현손익 ─────────────────────────
def rebuy_qty(q, a0, a1, p):
    """수량은 그대로인데 평단만 a0→a1로 바뀜 = 15분 안에 k주 팔고 k주 다시 산 것.
    이동평균이면 a1 = ((q-k)·a0 + k·p) / q → k = q·(a1-a0)/(p-a0). p(재매수가)는 지금 가격으로 봐요."""
    if not q or not a0 or abs(a1 / a0 - 1) <= 0.001:
        return 0.0
    if abs(p - a0) / a0 < 0.003:
        return q            # 지금 가격이 평단과 거의 같으면 알 수 없음 → 전량으로 봄
    return max(0.0, min(q, q * (a1 - a0) / (p - a0)))


def track_realized(raw, cfg, state, ts):
    """15분 전 스냅샷(rz_prev)과 비교해 매도를 찾아 실현손익을 state["realized"]에 쌓아요.
    compute 가 lots(원화 원금)를 고치기 전에 불러요. 반환: 이번 창에서 사고판 금액(원) — 수수료 보정용.
    - 일부 매도: 원화 원금을 수량 비례로 덜어냄(이동평균) → 실현 = 판 수량 × 지금 가격 × 지금 환율 − 덜어낸 원금
    - 전량 매도: 종목이 사라지면 직전 시세(최대 15분 전)로 추정
    - 15분 안에 전량 팔고 같은 수량 재매수: 수량은 같고 평단만 바뀜 → 전량 매도로 봄
    - 코인은 업비트 원화가 실제로 늘었을 때만 매도로 봐요(스테이킹 이동 오인 방지)"""
    prev = state.get("rz_prev")
    fx = raw["fx"]["rate"]
    rows = {r["sym"]: r for r in raw["rows"]}
    rz = state.setdefault("realized", [])
    traded = 0.0
    if prev:
        d_upkrw = f(raw["cash"].get("upbit_krw")) - f(state.get("rz_upkrw"), f(raw["cash"].get("upbit_krw")))
        for s, p in prev.items():
            r, usd = rows.get(s), p.get("ccy") == "USD"
            q0, q1 = f(p.get("qty")), (f(r["qty"]) if r else 0.0)
            px_now = (f(r["price"]) if r else f(p.get("price")))
            k = fx if usd else 1.0
            if q1 > q0 + 1e-9:
                traded += (q1 - q0) * px_now * k
            sold, how = 0.0, ""
            if q1 < q0 - 1e-9:
                sold, how = q0 - q1, ("전량" if not r else "일부")
            elif r and abs(q1 - q0) <= 1e-9:
                sold = rebuy_qty(q0, f(p.get("avg")), f(r["avg"]), px_now)
                how = "재매수"
            if sold <= 1e-9 or q0 <= 0:
                continue
            proceeds = sold * px_now * k
            if not usd and how != "재매수" and d_upkrw < 0.5 * proceeds:
                continue   # 원화가 안 늘었으면 매도가 아님(스테이킹 이동·출고 등)
            cost = f(p.get("cost_krw")) * sold / q0
            traded += proceeds * (2 if how == "재매수" else 1)
            rz.append({"t": ts.isoformat(timespec="minutes"), "sym": s, "q": round(sold, 8),
                       "px": round(px_now, 4 if usd else 0), "fx": round(fx, 2) if usd else None,
                       "a": round(proceeds), "c": round(cost), "pl": round(proceeds - cost),
                       "pool": "us" if usd else "coin", "how": how})
            print(f"  🧾 {s} {sold:g}{'주' if usd else '개'} 매도 감지({how}) → 실현 {proceeds - cost:+,.0f}원")
    state["realized"] = rz[-600:]
    state["rz_upkrw"] = f(raw["cash"].get("upbit_krw"))
    return traded


def realized_summary(state, cfg, ts):
    """연도별 합계(해외주식 / 코인) + 기록 시작 전 실현분(config realized_seed)"""
    by = {}
    for x in state.get("realized", []):
        y = x["t"][:4]
        b = by.setdefault(y, {"us": 0, "coin": 0, "n": 0})
        b[x.get("pool", "us")] += x["pl"]; b["n"] += 1
    seed = {str(k): f(v) for k, v in (cfg.get("realized_seed") or {}).items()}
    return {"years": by, "seed": seed, "since": state.setdefault("rz_since", ts.isoformat(timespec="minutes"))}


# ───────────────────────── 계산 ─────────────────────────
def compute(raw, cfg, state, ts):
    fx = raw["fx"]["rate"]
    seeds = {k: v for k, v in (cfg.get("krw_cost_seed") or {}).items() if f(v) > 0}
    lots = state.setdefault("lots", {})  # sym -> {qty, cost_usd, cost_krw, seeded}
    traded = track_realized(raw, cfg, state, ts)
    seed_used = set(state.setdefault("seed_used", []))   # 한 번 다 팔았던 종목은 시드(옛 원화 원금)를 다시 쓰지 않음

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
                if s in seeds and s not in seed_used:
                    L = {"qty": r["qty"], "cost_usd": cost_ccy, "cost_krw": f(seeds[s]), "seeded": True}
                else:
                    L = {"qty": r["qty"], "cost_usd": cost_ccy, "cost_krw": cost_ccy * fx, "seeded": s in seed_used}
            else:
                if s in seeds and not L.get("seeded") and s not in seed_used:  # 나중에 시드를 채워 넣은 경우
                    L.update(cost_krw=f(seeds[s]), seeded=True)
                dq = r["qty"] - L["qty"]
                if dq > 1e-9:  # 추가 매수
                    L["cost_krw"] += max(cost_ccy - L["cost_usd"], 0) * fx
                elif dq < -1e-9 and L["qty"] > 0:  # 일부 매도 → 비례 차감
                    L["cost_krw"] *= r["qty"] / L["qty"]
                elif L["qty"] > 0 and (k := rebuy_qty(L["qty"], L["cost_usd"] / L["qty"], r["avg"], r["price"])) > 0:
                    # 15분 안에 k주 팔고 k주 재매수 → 판 만큼 옛 원금을 덜고 새로 산 만큼 지금 환율로 더함
                    L["cost_krw"] = L["cost_krw"] * (1 - k / L["qty"]) + k * r["price"] * fx
                    if k >= L["qty"] - 1e-9:
                        L["seeded"] = True
                        seed_used.add(s)
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
            seed_used.add(s)
    state["seed_used"] = sorted(seed_used)
    state["rz_prev"] = {h["sym"]: {"qty": h["qty"], "avg": h["avg"], "price": h["price"], "ccy": h["ccy"],
                                   "cost_krw": h["cost_krw"]} for h in holds}

    c = raw["cash"]
    cash_krw = f(c["toss_krw"]) + f(c["toss_usd"]) * fx + f(c["upbit_krw"])
    invest = sum(h["value_krw"] for h in holds)
    total = invest + cash_krw

    # ── NAV(좌수법): 입금·추가매수는 좌수 증가, 시장 움직임만 기준가에 반영
    prev = state.get("nav_prev")
    nav = state.setdefault("nav", {})
    flow, div = 0.0, 0.0
    krw_cash = f(c["toss_krw"]) + f(c["upbit_krw"])
    if not prev or not nav.get("units"):
        nav.update(units=total / 1000.0, base_date=ts.date().isoformat(), start_total=round(total))
        unit = 1000.0
    else:
        mkt = 0.0
        px_now = {h["sym"]: h["price_krw"] for h in holds}
        for s, (q, p) in prev["px"].items():
            if s in px_now:
                mkt += q * (px_now[s] - p)
        mkt += prev.get("usd_cash", 0) * (fx - prev.get("fx", fx))
        mkt += f(raw.get("stk_adj"))   # 스테이킹 보상(수량 맞춤 차이)은 입금이 아니라 수익
        flow = (total - prev["total"]) - mkt
        if abs(flow) < cfg.get("flow_threshold_krw", 3000):
            flow = 0.0  # 수수료·반올림 수준은 성과로 처리
        elif flow < 0 and traded > 0 and -flow <= traded * 0.004 + cfg.get("flow_threshold_krw", 3000):
            flow = 0.0  # 사고판 창의 수수료·시세 차이는 출금이 아니라 비용(성과)으로
        # 배당: 원화 입금 없이 달러 예수금만 늘었으면 입금이 아니라 배당(=수익)으로 봄 → 기준가에 반영
        if flow > 0 and prev.get("krw_cash") is not None:
            d_krw = krw_cash - prev["krw_cash"]
            d_usd = (f(c["toss_usd"]) - prev.get("usd_cash", 0)) * fx
            if d_krw <= 1000 and d_usd >= 0.8 * flow:
                div, flow = flow, 0.0
        pre_unit = (total - flow) / nav["units"] if nav["units"] else 1000.0
        if flow:
            nav["units"] += flow / pre_unit
        unit = total / nav["units"] if nav["units"] else 1000.0
    state["nav_prev"] = {"total": total, "fx": fx, "usd_cash": f(c["toss_usd"]), "krw_cash": krw_cash,
                         "px": {h["sym"]: [h["qty"], h["price_krw"]] for h in holds}}

    if div:
        dv = state.setdefault("divs", [])
        dv.append({"t": ts.isoformat(timespec="minutes"), "a": round(div), "usd": round(div / fx, 2)})
        state["divs"] = dv[-500:]
        print(f"  💵 배당 감지 {round(div):,}원 (${div / fx:,.2f})")

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
    td = (ts - timedelta(hours=9)).date().isoformat()  # 매수 하루 = 09:00 ~ 다음날 09:00 (밤사이 미국장 체결분 포함)
    if prevq is not None:
        for sym in set(curq) | set(prevq):
            dq = curq.get(sym, 0) - prevq.get(sym, 0)
            ds = curs.get(sym, 0) - prevs.get(sym, 0)
            if dq < 0 and ds > 0:            # 현물 → 스테이킹으로 옮긴 건 매도가 아님
                dq += min(ds, -dq)
            if abs(dq) > 1e-9:
                day = trades.setdefault(td, {})
                q0, k0 = day.get(sym, [0, 0])
                day[sym] = [round(q0 + dq, 8), round(k0 + dq * px_krw.get(sym, 0))]
    state["qty"], state["stk_q"] = curq, curs
    for k in sorted(trades)[:-400]:
        trades.pop(k)

    # ── 원금 기준선(적립 vs 시장 분해용): 시작 시점의 매입원가+현금, 이후 입출금은 flows로 더함
    if "principal0" not in nav:
        nav["principal0"] = round(sum(h["cost_krw"] for h in holds) + cash_krw - sum(x["a"] for x in state.get("flows", [])))

    intr = state.setdefault("intraday", [])
    intr.append({"t": ts.isoformat(timespec="minutes"), "v": round(total), "u": round(unit, 4), "fx": round(fx, 2),
                 "p": {h["sym"]: (round(h["price"], 2) if h["ccy"] == "USD" else round(h["price"])) for h in holds}})  # 시세 보드 48시간 그래프용
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
        "realized": state.get("realized", [])[-300:],
        "realized_sum": realized_summary(state, cfg, ts),
        "fx_hist": state.get("fx_hist", []),
        "flows": state.get("flows", [])[-400:],
        "divs": state.get("divs", []) + [x for x in (cfg.get("dividends_extra") or []) if isinstance(x, dict) and x.get("t")],
        "history": hist,
        "intraday": state["intraday"],
        "config": {k: cfg.get(k) for k in ("targets", "band_rel", "goal", "monthly_dca", "dd_limit",
                                           "under_weight_mult", "scenarios", "daily_dca", "dca_days",
                                           "div_yield", "div_months", "div_tax")},
        **load_past(cfg, state),
    }


# ───────────────────────── 과거 월별 기록 (history.csv) ─────────────────────────
def _num(x):
    x = str(x).replace(",", "").replace("원", "").strip()
    return float(x) if x not in ("", "-") else 0.0


def load_past(cfg, state):
    """라이브 수집 전 월말 기록을 읽어 같은 기준가(좌수법)로 이어 붙임.
    history.csv 는 서버에만 두고(깃허브에 안 올라감) 결과는 다른 데이터와 같이 암호화돼요.
    열: 월(YYYY-MM), 기말자산, 입출금, 코스피, S&P500  — 첫 달 입출금 = 시작 원금"""
    p = ROOT / cfg.get("history_file", "history.csv")
    if not p.exists():
        return {}
    import csv
    rows = []
    for r in csv.reader(p.read_text(encoding="utf-8-sig").splitlines()):
        if not r or not r[0].strip()[:4].isdigit():
            continue  # 머리글·빈 줄 건너뜀
        m = r[0].strip().replace(".", "-").replace("/", "-")[:7]
        rows.append((m, _num(r[1]), _num(r[2]), _num(r[3]) if len(r) > 3 else 0, _num(r[4]) if len(r) > 4 else 0))
    rows.sort()
    if len(rows) < 2:
        return {}
    import calendar
    past, units, nav, cum = [], 0.0, 1000.0, 0.0
    for i, (m, v, fl, k, sp) in enumerate(rows):
        if i == 0:
            units, nav = v / 1000.0, 1000.0
        else:
            units += fl / nav          # 그달 입출금은 전월말 기준가로 좌수 매입 (원래 표와 같은 방식)
            nav = v / units
        cum += fl
        y, mo = int(m[:4]), int(m[5:7])
        b = {k2: v2 for k2, v2 in (("KOSPI", k), ("SPX", sp)) if v2}
        past.append({"m": m, "d": f"{m}-{calendar.monthrange(y, mo)[1]:02d}", "v": round(v), "f": round(fl),
                     "p": round(cum), "u": round(nav, 4), "b": b})

    # 연결점: 라이브 첫 총액과 마지막 월말 사이(며칠) 움직임을 기준가에 반영
    navst = state.get("nav", {})
    t0 = navst.get("start_total")
    if not t0:
        base = navst.get("base_date")
        first = next((x for x in state.get("intraday", []) if x["t"][:10] == base), None)
        h0 = (state.get("history") or [None])[0]
        t0 = first["v"] if first else (h0["v"] * 1000.0 / h0["u"] if h0 and h0.get("u") else None)
        if t0:
            navst["start_total"] = round(t0)
    if not t0:
        return {}
    gap_flow = f(cfg.get("history_gap_flow"), 0.0)   # 마지막 월말 ~ 라이브 시작 사이에 넣은 돈(선택)
    last = past[-1]
    g = (t0 - gap_flow) / last["v"] if last["v"] else 1.0
    if abs(g - 1) > 0.05:
        print(f"  ! 과거 기록 연결 확인: 마지막 월말 {last['v']:,} → 라이브 시작 {round(t0):,} ({(g-1)*100:+.1f}%). "
              f"그 사이 입출금이 있었다면 config.json 의 history_gap_flow 에 넣어주세요.", file=sys.stderr)
    return {"past": past, "long": {"scale": last["u"] * g / 1000.0, "start": past[0]["d"],
                                   "gap": round(g - 1, 5), "gap_flow": round(gap_flow),
                                   "principal": last["p"] + round(gap_flow), "live_start_total": round(t0)}}


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
          f"| 낙폭 {snap['nav']['dd']*100:.1f}% | 환율 {snap['fx']['rate']:.1f}"
          + (f" | 과거 {len(snap['past'])}개월 연결(누적 기준가 {snap['nav']['unit']*snap['long']['scale']:.1f})" if snap.get('past') else ""))

    if not a.no_push and not a.mock:
        gh_put("data/portfolio.enc.json", json.dumps(enc).encode(), f"data: {ts:%m-%d %H:%M}")
        print("  ↑ GitHub 업로드 완료")


if __name__ == "__main__":
    main()
