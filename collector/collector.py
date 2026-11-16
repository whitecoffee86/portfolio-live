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


# ── 서버 config.json 원격 변경 (한 번씩만 적용)
# 서버는 15분마다 이 저장소 코드를 받아 오니까, 여기에 적으면 다음 실행 때 config.json 이 바뀌어요.
# 적용한 id 는 config.json 의 _patches_applied 에 남아서 다시 덮어쓰지 않아요(그 뒤 손으로 바꾼 값 유지).
# ⚠️ 공개 저장소예요. 비중·적립 금액은 공개해도 된다고 함(2026-11-13). 원금·잔고·배당·키는 절대 넣지 않기.
CONFIG_PATCHES = [
    {"id": "2026-11-13-targets",   # 정수 목표 비중 + MCHI 정리 (QQQM 40·SCHD 20·TLT 10·IAU 15·BTC 13·ETH 2)
     "set": {"targets": {"QQQM": 40, "SCHD": 20, "TLT": 10, "IAU": 15, "MCHI": 0, "BTC": 13, "ETH": 2}}},
    {"id": "2026-11-13-dca-stage1",  # 적립 1단계: 한 달 105만 → 대시보드가 ⚖️ 규칙·실제 주기로 나눔(종목별 고정 금액 daily_dca는 끔)
     "set": {"monthly_dca": 1050000, "daily_dca": None}},
]


# ── 서버 기록(state.json) 한 번만 다시 계산 (id 로 한 번씩, 실패하면 3번까지 다음 실행에 재시도)
# 공개 저장소라 수량·금액은 적지 않고 '언제부터'만 적어요. 서버가 그 직전에 올렸던 암호화 기록(git 이력)을 풀어
# 그 시점부터 지금 규칙(usd_window)으로 다시 돌려 실현손익·원화 원금·수수료 출금 오인을 고쳐요.
STATE_REPAIRS = [
    {"id": "2026-11-17-rebuy-split", "since": "2026-11-13T09:00:00+09:00",
     "why": "15분 안에 팔고 다른 수량으로 다시 산 리밸런싱(QQQM 등)이 '1주 매도'로 잡힘"},
]


def apply_config_patches(path):
    cfg = jload(path, None)
    if not isinstance(cfg, dict):
        return   # config.json 이 없으면(예시 파일로 도는 중) 건드리지 않음
    done = list(cfg.get("_patches_applied") or [])
    todo = [p for p in CONFIG_PATCHES if p["id"] not in done]
    if not todo:
        return
    for p in todo:
        cfg.update(p["set"])
        done.append(p["id"])
        print(f"  ⚙️ config.json 변경 적용: {p['id']} → {', '.join(p['set'])}")
    cfg["_patches_applied"] = done
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
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

    def orders(self, status, frm):
        out, cur = [], None
        for _ in range(20):
            p = {"status": status, "from": frm}
            if status == "CLOSED":
                p["limit"] = 100
                if cur:
                    p["cursor"] = cur
            r = self.get("/api/v1/orders", p, account=True) or {}
            out += r.get("orders") or []
            if status != "CLOSED" or not r.get("hasNext") or not r.get("nextCursor"):
                break
            cur = r["nextCursor"]
        return out

    def fills(self, days=4):
        """최근 며칠 주문의 체결 누계 {orderId: {...}} — 앱에서 낸 주문도 들어와요(지정가·시장가·LOC).
        15분 창마다 '새로 체결된 양'만 골라 판 수량·산 수량을 정확히 나누는 데 써요."""
        frm = (now_kst() - timedelta(days=days)).date().isoformat()
        out = {}
        for st in ("CLOSED", "OPEN"):
            for o in self.orders(st, frm):
                ex = o.get("execution") or {}
                q = f(ex.get("filledQuantity"))
                if q <= 0 or o.get("side") not in ("BUY", "SELL") or not o.get("orderId"):
                    continue
                px = f(ex.get("averageFilledPrice"))
                out[o["orderId"]] = {"sym": o.get("symbol"), "side": o["side"], "q": q,
                                     "amt": f(ex.get("filledAmount")) or q * px,
                                     "fee": f(ex.get("commission")) + f(ex.get("tax")),
                                     "ccy": o.get("currency") or "USD",
                                     "at": ex.get("filledAt") or o.get("orderedAt")}
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
    # 체결 내역은 보유보다 '먼저' 받아요. 그 사이 체결이 끼면 이번 창은 평단 추정, 다음 창에선 '보유 그대로'라 무시 → 두 번 안 셈
    days = 4
    for rp in STATE_REPAIRS:   # 아직 안 끝난 기록 보정이 있으면 그 시작일까지 넉넉히
        if not ((state.get("_repairs") or {}).get(rp["id"]) or {}).get("ok"):
            days = max(days, (now_kst() - datetime.fromisoformat(rp["since"])).days + 2)
    try:
        fills = toss.fills(days)
    except Exception as e:
        fills = None
        print(f"  ! 토스 체결 내역 조회 실패({e}) → 수량·평단 변화로 추정", file=sys.stderr)
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
        "fx": fx, "rows": rows, "bench": toss.bench(), "stk_adj": stk_adj, "fills": fills,
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
def _fill_add(agg, sym, side, q, amt, fee):
    a = agg.setdefault(sym, {"sq": 0.0, "samt": 0.0, "sfee": 0.0, "bq": 0.0, "bamt": 0.0, "bfee": 0.0})
    k = "s" if side == "SELL" else "b"
    a[k + "q"] += q
    a[k + "amt"] += amt
    a[k + "fee"] += fee


def fill_deltas(raw, state):
    """이번 창(직전 실행 이후)에 새로 체결된 양 {종목: 합계}. 체결 내역을 못 받았거나 처음이면 None(→ 평단 추정)."""
    F = raw.get("fills")
    if F is None:
        return None
    seen = state.get("fills_seen")
    state["fills_seen"] = {oid: [x["q"], x["amt"], x["fee"]] for oid, x in F.items()}
    if seen is None:
        return None   # 처음엔 기준만 잡음(이미 반영된 옛 체결을 이번 창 것으로 착각하지 않게)
    agg = {}
    for oid, x in F.items():
        q0, a0, e0 = (seen.get(oid) or [0.0, 0.0, 0.0])
        if x["q"] - q0 > 1e-9:
            _fill_add(agg, x["sym"], x["side"], x["q"] - q0, x["amt"] - a0, x["fee"] - e0)
    return agg


def window_fills(F, t0, t1):
    """보정용: 체결 시각이 (t0, t1] 안인 주문 합계 {종목: 합계}"""
    agg = {}
    for x in (F or {}).values():
        try:
            at = datetime.fromisoformat(str(x["at"]).replace("Z", "+00:00"))
        except Exception:
            continue
        if t0 < at <= t1:
            _fill_add(agg, x["sym"], x["side"], x["q"], x["amt"], x["fee"])
    return agg


def usd_window(q0, a0, q1, a1, px, fl=None, fx=1.0):
    """15분 창 하나의 변화(q0주@a0 → q1주@a1)를 '판 수량'과 '산 수량'으로 나눠요.
    ① 토스 체결 내역(fl)이 수량 변화와 맞으면 그대로 써요 → 판 가격·수수료까지 정확.
    ② 없거나 안 맞으면 평단으로 풀어요. 토스 평단은 이동평균이라 팔 땐 그대로, 살 땐 섞여요:
         a1·q1 = (q0−s)·a0 + b·p,  b = q1−q0+s   →   s = (a1·q1 − a0·q0 − (q1−q0)·p) / (p − a0)
       p(산 가격)는 지금 가격으로 대신해서 생기는 작은 오차는 '거의 전부(98%↑)'면 전부로 맞춰요.
       예) 417주 전량 팔고 416주 재매수 → 순변화는 −1주지만 평단이 새 가격으로 바뀌어서 417주 매도로 잡혀요.
    반환: {sold, bought, spx(판 가격 $), sfee·bfee($), src}"""
    out = {"sold": 0.0, "bought": 0.0, "spx": px, "sfee": 0.0, "bfee": 0.0, "src": "추정"}
    dq = q1 - q0
    if fl and (fl["sq"] > 0 or fl["bq"] > 0):
        resid = q1 - (q0 - fl["sq"] + fl["bq"])          # 체결 내역으로 설명 안 되는 수량(주식 모으기가 목록에 없으면 +)
        tol = max(0.02, 0.002 * max(q0, q1))
        stale = fl["sq"] > 0 and abs(dq) <= 1e-9 and (a0 <= 0 or abs(a1 / a0 - 1) <= 1e-7)   # 체결은 됐는데 보유가 아직 그대로
        if not stale and fl["sq"] <= q0 + tol and (abs(resid) <= tol or (fl["sq"] <= 0 and resid > 0)):
            s = min(fl["sq"], q0)
            spx = fl["samt"] / fl["sq"] if fl["sq"] > 0 else px
            ref = spx if fl["sq"] > 0 else (fl["bamt"] / fl["bq"] if fl["bq"] > 0 else px)
            krw = px > 0 and abs(ref / px - 1) > 0.15        # 원화로 찍힌 주문이면 달러로 못 씀 → 지금 가격
            out.update(sold=s, bought=q1 - q0 + s, spx=px if krw else spx, src="체결",
                       sfee=fl["sfee"] / (fx if krw else 1), bfee=fl["bfee"] / (fx if krw else 1))
            return out
    if q0 <= 1e-12:
        out["bought"] = max(q1, 0.0)
        return out
    if q1 <= 1e-12:
        out["sold"] = q0
        return out
    if a0 <= 0 or abs(a1 / a0 - 1) <= 0.001:          # 평단 그대로 → 줄었으면 판 것, 늘었으면 산 것
        s = max(-dq, 0.0)
    elif dq > 1e-9 and px > 0 and abs((a1 * q1 - a0 * q0) / dq / px - 1) <= 0.03:
        s = 0.0                                        # 늘어난 만큼을 지금 가격 근처에서 산 것 → 그냥 매수
    elif abs(px - a0) / a0 < 0.003:
        s = q0                                         # 지금 가격이 평단과 거의 같으면 판 수량을 알 수 없음 → 전량으로
    else:
        s = (a1 * q1 - a0 * q0 - dq * px) / (px - a0)
        s = min(max(s, max(-dq, 0.0)), q0)
        if s >= 0.98 * q0:
            s = q0
    out.update(sold=s, bought=q1 - q0 + s)
    return out


def rz_entry(t, sym, mv, q0, cost0, fx, usd=True):
    """실현손익 한 줄. 원금은 이동평균(판 수량 비례), 수수료를 알면 뺌"""
    sold = mv["sold"]
    k = fx if usd else 1.0
    proceeds = sold * mv["spx"] * k
    cost = cost0 * sold / q0 if q0 else 0.0
    fee = mv.get("sfee", 0.0) * k
    how = "재매수" if mv.get("bought", 0) > 1e-6 else ("전량" if sold >= q0 - 1e-9 else "일부")
    e = {"t": t, "sym": sym, "q": round(sold, 8), "px": round(mv["spx"], 4 if usd else 0),
         "fx": round(fx, 2) if usd else None, "a": round(proceeds), "c": round(cost),
         "pl": round(proceeds - cost - fee), "pool": "us" if usd else "coin", "how": how}
    if fee:
        e["fee"] = round(fee)
    if mv.get("src") == "체결":
        e["src"] = "체결"
    return e


def lot_apply(L, sold, qty, avg, fx):
    """원화 원금(lot) 갱신: 판 수량만큼 옛 원금을 비례로 덜고, 달러 매입금이 늘어난 만큼(=새로 산 몫)은 지금 환율로 더해요.
    반환: 옛 수량을 전부 팔았는지(→ 시드 원금은 다시 안 씀)"""
    q0 = L.get("qty") or 0.0
    keep = max(q0 - sold, 0.0)
    if q0 > 0:
        old_usd = L["cost_usd"] * keep / q0
        L["cost_krw"] *= keep / q0
    else:
        old_usd, L["cost_krw"] = 0.0, 0.0
    add = qty * avg - old_usd
    if add > 1e-6:
        L["cost_krw"] += add * fx
    L["qty"], L["cost_usd"] = qty, qty * avg
    return q0 > 0 and sold >= q0 - 1e-9


def track_realized(raw, cfg, state, ts):
    """15분 전 스냅샷(rz_prev)과 비교해 매도를 찾아 실현손익을 state["realized"]에 쌓아요.
    compute 가 lots(원화 원금)를 고치기 전에 불러요.
    반환: (사고판 금액 원, 아는 수수료 원, {미국 종목: 판 수량}) — 앞의 둘은 수수료를 출금으로 착각하지 않게,
          판 수량은 compute 의 원화 원금 갱신에 그대로 씀
    - 미국 ETF: usd_window 로 판 수량·산 수량을 나눔(토스 체결 내역 우선, 없으면 평단 역산)
      → 15분 안에 팔고 '다른 수량'으로 다시 사도(리밸런싱) 순변화가 아니라 실제 판 수량으로 잡혀요
    - 전량 매도: 종목이 사라지면 직전 시세(최대 15분 전)로 추정
    - 코인은 업비트 원화가 실제로 늘었을 때만 매도로 봐요(스테이킹 이동 오인 방지)"""
    prev = state.get("rz_prev")
    fx = raw["fx"]["rate"]
    rows = {r["sym"]: r for r in raw["rows"]}
    rz = state.setdefault("realized", [])
    fl = fill_deltas(raw, state)
    traded, fees, moves = 0.0, 0.0, {}
    if prev:
        d_upkrw = f(raw["cash"].get("upbit_krw")) - f(state.get("rz_upkrw"), f(raw["cash"].get("upbit_krw")))
        for s, p in prev.items():
            r, usd = rows.get(s), p.get("ccy") == "USD"
            q0, q1 = f(p.get("qty")), (f(r["qty"]) if r else 0.0)
            px_now = (f(r["price"]) if r else f(p.get("price")))
            k = fx if usd else 1.0
            t = ts.isoformat(timespec="minutes")
            if usd:
                mv = usd_window(q0, f(p.get("avg")), q1, f(r["avg"]) if r else 0.0, px_now,
                                (fl or {}).get(s) if fl is not None else None, fx)
                moves[s] = mv["sold"]
                traded += (mv["sold"] * mv["spx"] + max(mv["bought"], 0.0) * px_now) * fx
                fees += (mv["sfee"] + mv["bfee"]) * fx
                if mv["sold"] > 1e-9 and q0 > 0:
                    e = rz_entry(t, s, mv, q0, f(p.get("cost_krw")), fx)
                    rz.append(e)
                    print(f"  🧾 {s} {mv['sold']:g}주 매도 감지({e['how']}·{mv['src']}"
                          + (f", 재매수 {mv['bought']:g}주" if e["how"] == "재매수" else "") + f") → 실현 {e['pl']:+,}원")
                continue
            if q1 > q0 + 1e-9:
                traded += (q1 - q0) * px_now * k
            sold, how = 0.0, ""
            if q1 < q0 - 1e-9:
                sold, how = q0 - q1, ("전량" if not r else "일부")
            elif r and abs(q1 - q0) <= 1e-9:   # 같은 수량인데 평단만 바뀜 = 팔고 같은 수량 재매수
                sold, how = usd_window(q0, f(p.get("avg")), q1, f(r["avg"]), px_now)["sold"], "재매수"
            if sold <= 1e-9 or q0 <= 0:
                continue
            proceeds = sold * px_now * k
            if how != "재매수" and d_upkrw < 0.5 * proceeds:
                continue   # 원화가 안 늘었으면 매도가 아님(스테이킹 이동·출고 등)
            traded += proceeds * (2 if how == "재매수" else 1)
            e = rz_entry(t, s, {"sold": sold, "spx": px_now, "bought": 0.0}, q0, f(p.get("cost_krw")), fx, usd=False)
            e["how"] = how
            rz.append(e)
            print(f"  🧾 {s} {sold:g}개 매도 감지({how}) → 실현 {e['pl']:+,}원")
    state["realized"] = rz[-600:]
    state["rz_upkrw"] = f(raw["cash"].get("upbit_krw"))
    return traded, fees, moves


def realized_summary(state, cfg, ts):
    """연도별 합계(해외주식 / 코인) + 기록 시작 전 실현분(config realized_seed)"""
    by = {}
    for x in state.get("realized", []):
        y = x["t"][:4]
        b = by.setdefault(y, {"us": 0, "coin": 0, "n": 0})
        b[x.get("pool", "us")] += x["pl"]; b["n"] += 1
    seed = {str(k): f(v) for k, v in (cfg.get("realized_seed") or {}).items()}
    return {"years": by, "seed": seed, "since": state.setdefault("rz_since", ts.isoformat(timespec="minutes"))}


# ───────────────────────── 기록 다시 계산 (STATE_REPAIRS) ─────────────────────────
def decrypt(enc, pw):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    b = base64.b64decode
    key = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), b(enc["salt"]), int(enc.get("iter", 210_000)), 32)
    return json.loads(AESGCM(key).decrypt(b(enc["iv"]), b(enc["ct"]), None))


def _dt(x):
    return datetime.fromisoformat(str(x).replace("Z", "+00:00"))


def past_snaps(since, pw, repo=None):
    """서버 git 이력의 암호화 기록 중 since 직전 1개 + 그 뒤 전부를 풀어 시간순으로.
    다시 계산에 필요한 것(시각·환율·총액·미국 종목 수량/평단/가격/원화 원금)만 남겨 메모리를 아껴요."""
    import subprocess
    repo, path = (repo or ROOT.parent), "data/portfolio.enc.json"
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True,
                                    timeout=120, check=True).stdout
    shas = git("log", "--format=%H", f"--since={(since - timedelta(hours=3)).isoformat()}", "--", path).split()[::-1]
    snaps = {}
    for h in shas:
        try:
            p = decrypt(json.loads(git("show", f"{h}:{path}")), pw)
        except Exception:
            continue   # 비밀번호가 바뀌기 전 기록 등
        snaps[p["asof"]] = {"asof": p["asof"], "fx": f(p["fx"]["rate"]), "total": f(p.get("total_krw")),
                            "h": {x["sym"]: {k: x.get(k) for k in ("qty", "avg", "price", "cost_krw", "fx_seeded")}
                                  for x in p.get("holdings", []) if x.get("ccy") == "USD"}}
    out = sorted(snaps.values(), key=lambda x: _dt(x["asof"]))
    before = [x for x in out if _dt(x["asof"]) < since]
    if not before:
        raise RuntimeError("시작 시각 직전 기록을 git 이력에서 못 찾았어요")
    return [before[-1]] + [x for x in out if _dt(x["asof"]) >= since]


def repair_replay(rp, cfg, state, raw, snaps=None):
    """since 직전 기록의 원화 원금에서 출발해, 그 뒤 15분 기록들을 차례로 usd_window 로 다시 나눠요.
    바꾸는 것: 그 구간 미국 종목 실현손익(realized) · 원화 원금(lots, rz_prev) · 수수료가 출금으로 잡힌 건(flows + 기준가)"""
    since = _dt(rp["since"])
    if snaps is None:
        pw = os.environ.get("DASH_PASSPHRASE")
        if not pw:
            raise RuntimeError("DASH_PASSPHRASE 가 없어요")
        snaps = past_snaps(since, pw)
    if len(snaps) < 2:
        raise RuntimeError("다시 계산할 기록이 없어요")
    base, last = snaps[0], snaps[-1]
    rzp = state.get("rz_prev") or {}
    for s_, h in last["h"].items():   # 마지막 기록 = 직전 실행이어야 이어 붙일 수 있음(업로드 실패 직후면 다음에)
        if abs(f((rzp.get(s_) or {}).get("qty")) - f(h["qty"])) > 1e-6:
            raise RuntimeError(f"마지막 기록과 서버 상태가 달라요({s_}) → 다음 실행 때 다시")
    F = raw.get("fills")
    thr = cfg.get("flow_threshold_krw", 3000)
    seed_used = set(state.get("seed_used", []))
    lots = {s_: {"qty": f(h["qty"]), "cost_usd": f(h["qty"]) * f(h["avg"]), "cost_krw": f(h["cost_krw"]),
                 "seeded": bool(h.get("fx_seeded", True))} for s_, h in base["h"].items()}
    new_rz, drop, logs = [], [], []
    flows = state.get("flows", [])
    for prev, cur in zip(snaps, snaps[1:]):
        t0, t1, fx = _dt(prev["asof"]), _dt(cur["asof"]), cur["fx"]
        wf = window_fills(F, t0, t1) if F else None
        traded = fees = 0.0
        for s_ in sorted(set(prev["h"]) | set(cur["h"])):
            p, c = prev["h"].get(s_), cur["h"].get(s_)
            q0, q1 = (f(p["qty"]) if p else 0.0), (f(c["qty"]) if c else 0.0)
            a0, a1 = (f(p["avg"]) if p else 0.0), (f(c["avg"]) if c else 0.0)
            px = f((c or p)["price"])
            mv = usd_window(q0, a0, q1, a1, px, (wf or {}).get(s_) if wf is not None else None, fx)
            traded += (mv["sold"] * mv["spx"] + max(mv["bought"], 0.0) * px) * fx
            fees += (mv["sfee"] + mv["bfee"]) * fx
            L = lots.setdefault(s_, {"qty": 0.0, "cost_usd": 0.0, "cost_krw": 0.0, "seeded": s_ in seed_used})
            if mv["sold"] > 1e-9 and q0 > 0:
                e = rz_entry(t1.isoformat(timespec="minutes"), s_, mv, q0, L["cost_krw"], fx)
                new_rz.append(e)
                logs.append(f"{e['t'][5:16]} {s_} {e['q']:g}주 {e['how']}({mv['src']}) 실현 {e['pl']:+,}")
            if c is None:
                lots.pop(s_, None)
                seed_used.add(s_)
                continue
            if lot_apply(L, mv["sold"], q1, a1, fx):
                L["seeded"] = True
                seed_used.add(s_)
        tm = t1.replace(second=0, microsecond=0)
        for x in flows:
            if (traded > 0 and f(x.get("a")) < 0 and _dt(x["t"]) == tm
                    and -f(x["a"]) <= traded * 0.004 + fees + thr):
                drop.append((x, cur["total"]))

    # ── 반영 (여기까지 오면 한꺼번에)
    old = state.get("realized", [])
    keep = [x for x in old if not ((x.get("pool") or "us") == "us" and _dt(x["t"]) > _dt(base["asof"]))]
    n_old = len(old) - len(keep)
    state["realized"] = sorted(keep + new_rz, key=lambda x: _dt(x["t"]))[-600:]
    st_lots = state.setdefault("lots", {})
    for s_ in list(st_lots):
        if s_ not in last["h"]:
            st_lots.pop(s_)
    for s_ in last["h"]:
        st_lots[s_] = lots[s_]
        if s_ in rzp:
            rzp[s_]["cost_krw"] = round(lots[s_]["cost_krw"])
    state["seed_used"] = sorted(seed_used)
    nav = state.get("nav") or {}
    for x, T in sorted(drop, key=lambda z: _dt(z[0]["t"])):
        a, tx = -f(x["a"]), _dt(x["t"])
        r = T / (T + a) if T > 0 else 1.0       # 수수료를 출금 대신 비용으로: 좌수는 그대로, 그 뒤 기준가만 수수료만큼 낮아짐
        if nav.get("units"):
            nav["units"] /= r
        for it in state.get("intraday", []):
            if _dt(it["t"]) >= tx:
                it["u"] = round(it["u"] * r, 4)
        d = tx.astimezone(KST).date().isoformat()
        for hh in state.get("history", []):
            if hh["d"] >= d:
                hh["u"] = round(hh["u"] * r, 4)
            if hh["d"] == d:
                hh["f"] = hh.get("f", 0) + round(a)
        flows.remove(x)
        logs.append(f"{x['t'][5:16]} 출금 {round(-a):,}원 → 사고판 수수료(비용)로")
    for l in logs:
        print("     ·", l)
    return (f"{len(snaps) - 1}개 창 다시 계산 · 미국 실현 {n_old}건 → {len(new_rz)}건"
            f" · 수수료 출금 오인 {len(drop)}건 정리")


def run_repairs(cfg, state, raw, ts):
    done = state.setdefault("_repairs", {})
    for rp in STATE_REPAIRS:
        st = done.get(rp["id"]) or {}
        if st.get("ok") or st.get("tries", 0) >= 3:
            continue
        try:
            msg = repair_replay(rp, cfg, state, raw)
            done[rp["id"]] = {"ok": True, "at": ts.isoformat(timespec="minutes"), "msg": msg}
            print(f"  🛠️ 기록 다시 계산 {rp['id']}: {msg}")
        except Exception as e:
            st["tries"] = st.get("tries", 0) + 1
            st["err"] = str(e)[:200]
            done[rp["id"]] = st
            print(f"  ⚠️ 기록 다시 계산 {rp['id']} 실패({st['tries']}/3): {e}", file=sys.stderr)


# ───────────────────────── 계산 ─────────────────────────
def compute(raw, cfg, state, ts):
    fx = raw["fx"]["rate"]
    seeds = {k: v for k, v in (cfg.get("krw_cost_seed") or {}).items() if f(v) > 0}
    lots = state.setdefault("lots", {})  # sym -> {qty, cost_usd, cost_krw, seeded}
    traded, fees, moves = track_realized(raw, cfg, state, ts)
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
                sold = moves.get(s)   # track_realized 가 나눈 판 수량(직전 스냅샷이 없을 때만 여기서 평단으로 추정)
                if sold is None:
                    sold = usd_window(L["qty"], L["cost_usd"] / L["qty"] if L["qty"] else 0.0,
                                      r["qty"], r["avg"], r["price"])["sold"]
                if lot_apply(L, sold, r["qty"], r["avg"], fx):   # 옛 수량을 다 팔았음 → 원금이 전부 새 환율 기준
                    L["seeded"] = True
                    seed_used.add(s)
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
        elif flow < 0 and traded > 0 and -flow <= traded * 0.004 + fees + cfg.get("flow_threshold_krw", 3000):
            flow = 0.0  # 사고판 창의 수수료·시세 차이는 출금이 아니라 비용(성과)으로(사고판 금액 = 판 쪽 + 산 쪽 둘 다)
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
    # 매수한 날 요약(영구 보관): {날짜: [ETF 매수 원화, 코인 매수 원화]} — trades는 400일만 남기지만
    # 업적 진열장(월간 출석·최장 연속 적립)과 농부 레벨은 처음부터의 기록이 필요해서 따로 남김
    try:
        blog = state.setdefault("buy_log", {})
        for bday, bt in trades.items():
            b_etf = sum(max(v[1], 0) for s_, v in bt.items() if s_ not in ("BTC", "ETH") and v[0] > 0)
            b_coin = sum(max(v[1], 0) for s_, v in bt.items() if s_ in ("BTC", "ETH") and v[0] > 0)
            if b_etf or b_coin:
                blog[bday] = [round(b_etf), round(b_coin)]
            else:
                blog.pop(bday, None)
    except Exception as ex:  # 요약이 깨져도 수집은 계속
        print("⚠️ buy_log 건너뜀:", ex)
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
        "buy_log": state.get("buy_log", {}),
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
    if not a.mock:
        try:
            apply_config_patches(ROOT / "config.json")
        except Exception as e:   # 변경 실패해도 수집은 그대로
            print(f"  ⚠️ config 변경 건너뜀: {e}", file=sys.stderr)
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
    if not a.mock:
        run_repairs(cfg, state, raw, ts)   # 실패해도 수집은 그대로(상태는 다 계산한 뒤에만 바꿈)
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
