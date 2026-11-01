#!/usr/bin/env python3
"""
장기 포트 월간 결산 영상 스토리보드 만들기 (whitecoffee-video 엔진용)

  python3 build_lt_story.py data/monthly_public.json YYYY-MM <작업폴더>

결과
  <작업폴더>/month.json               이 달 숫자(영상에 쓰는 값은 전부 여기서)
  <작업폴더>/storyboard.json          가로 롱폼(16장면)
  <작업폴더>/shorts/storyboard.json   세로 쇼츠(5장면, 전부 custom)
  lt_calendar.js · lt_race.js · lt_orchard.js 를 두 폴더에 복사
데이터가 없거나 5일 미만이면 마지막 줄에 "EMPTY: 사유" 를 출력하고 끝나요.

금액 공개 규칙: 원 단위는 월 손익·일별 손익만. 총자산·평가금액·원금·현금·적립 금액·수량·목표 금액은
요약 파일에 애초에 없어요. 쇼츠·썸네일·제목에는 원화 금액을 넣지 않아요.
"""
import json, shutil, sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = ["lt_calendar.js", "lt_race.js", "lt_orchard.js"]
BLOG_HOME = "https://ideas07576.tistory.com"
C_ME, C_SPX, C_KOS = "#e8c86a", "#5fd69a", "#c9a0ff"


def empty(msg):
    print(f"EMPTY: {msg}")
    sys.exit(0)


def pc(x, d=2, sign=True):
    if x is None:
        return "-"
    v = x * 100
    s = ("+" if v > 0 else "−" if v < 0 else "") if sign else ("−" if v < 0 else "")
    return f"{s}{abs(v):.{d}f}%"


def pcs(x, d=2):
    """말할 때: 부호 없이 크기만"""
    return f"{abs(x * 100):.{d}f}%"


def updown(x):
    return "올랐어요" if x > 0 else "내렸어요" if x < 0 else "제자리였어요"


def won(v):
    """대사용: 만원 단위 반올림"""
    a = abs(v)
    if a < 10000:
        return f"{a:,}원"
    return f"{round(a / 10000):,}만원"


def md(d):
    y, m, dd = d.split("-")
    return f"{int(m)}월 {int(dd)}일"


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    src, ym, work = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    try:
        pub = json.loads(Path(src).read_text(encoding="utf-8"))
    except Exception as e:
        empty(f"요약 파일을 못 읽음({e})")
    m = (pub.get("months") or {}).get(ym)
    if not m or not m.get("days"):
        empty(f"{ym} 데이터 없음")
    if m["days"] < 5:
        empty(f"{ym} 기록이 {m['days']}일뿐(5일 미만)")

    M = int(ym[5:])
    mk = m["market"]
    ret, pl = m["ret"], m["pl"]
    spx, kos = mk.get("spx_krw"), mk.get("kospi")
    rank = m.get("rank") or 0
    nb = sum(1 for v in (spx, kos) if v is not None) + 1
    win = pl >= 0
    hold = [h for h in m["holdings"] if h.get("ret") is not None]
    best_h, worst_h = (hold[0], hold[-1]) if hold else (None, None)
    best, worst = m["best"], m["worst"]
    gaps_out = [g for g in m["gaps"] if g["out"]]
    slug = f"longterm-monthly-{ym}"
    work.mkdir(parents=True, exist_ok=True)
    (work / "shorts").mkdir(exist_ok=True)
    for s in SCRIPTS:
        for d in (work, work / "shorts"):
            shutil.copy(HERE / s, d / s)

    # ── 순위 문장
    if nb < 3:
        rank_txt, rank_q = f"{nb}명 중 {rank}위", "시장이랑 비교하면요?"
    else:
        rank_txt = f"세 명 중 {rank}위"
        rank_q = {1: "시장을 이긴 거예요?", 2: "그럼 중간이에요?", 3: "꼴찌예요?"}.get(rank, "몇 등이에요?")
    first_dow = date.fromisoformat(ym + "-01").weekday()
    import calendar
    dim = calendar.monthrange(int(ym[:4]), M)[1]
    start_note = f"{md(m['start'])}부터 " if m["start"][:7] == ym and m["start"][8:] != "01" and int(m["start"][8:]) > 2 else ""

    month = dict(m, slug=slug, M=M, rank_txt=rank_txt, live_start=pub.get("live_start"))
    (work / "month.json").write_text(json.dumps(month, ensure_ascii=False, indent=1), encoding="utf-8")

    P100 = lambda a: [None if v is None else round(v * 100, 3) for v in a]
    race_lanes = [{"name": "나", "color": C_ME, "vals": P100(m["race"]["me"])}]
    if spx is not None:
        race_lanes.append({"name": "S&P500", "color": C_SPX, "vals": P100(m["race"]["spx"])})
    if kos is not None:
        race_lanes.append({"name": "코스피", "color": C_KOS, "vals": P100(m["race"]["kospi"])})
    days_lbl = [f"{int(d[5:7])}/{int(d[8:])}" for d in m["race"]["days"]]
    cal_daily = [{"day": int(x["d"][8:]), "pl": x["pl"], "r": round((x["r"] or 0) * 100, 2), "buy": x["buy"]} for x in m["daily"] if x["d"][:7] == ym]
    cal = {"mode": "calendar", "title": f"{M}월 손익 달력", "title_v": f"{M}월 손익 달력", "subtitle": "하루 손익(만원) · 금색 = 적립한 날",
           "first_dow": first_dow, "days_in_month": dim, "daily": cal_daily,
           "best_day": int(best["d"][8:]) if best["d"][:7] == ym else None,
           "worst_day": int(worst["d"][8:]) if worst["d"][:7] == ym else None}
    orch_items = [{"name": h["name"], "sym": h["sym"], "ret": round(h["ret"] * 100, 3)} for h in hold]

    # ── 대사 조각
    lead_vs = ""
    if kos is not None and spx is not None:
        if rank == 1:
            lead_vs = f"같은 달 S&P500은 {pcs(spx)}, 코스피는 {pcs(kos)} {updown(kos)}. 둘 다 이겼어요."
        elif rank == 3:
            lead_vs = f"그런데 같은 달 코스피는 {pcs(kos)}, S&P500은 {pcs(spx)} {updown(spx)}. 셋 중 꼴찌예요."
        else:
            hi_name = "코스피" if (kos or -9) > (spx or -9) else "S&P500"
            lead_vs = f"그런데 {hi_name}{'는' if hi_name == '코스피' else '은'} {pcs(max(kos, spx))} {updown(max(kos, spx))}. 셋 중 두 번째예요."
    s_hook_lines = [
        {"who": "A", "text": f"{M}월 한 달, 제 장기 포트는 {pcs(ret)} {updown(ret)}. 손익으로는 {'플러스' if win else '마이너스'} {won(pl)}이에요."},
        {"who": "Q", "text": rank_q},
        {"who": "A", "text": lead_vs or f"비교할 시장 데이터가 이번 달은 부족했어요."},
    ]
    hook = {"type": "custom", "chapter": f"{M}월 결과",
            "lines": s_hook_lines,
            "props": {"script_file": "lt_race.js", "data": {"mode": "hook", "title": f"장기 포트 {M}월 결산", "subtitle": f"{start_note}매일 적립 · 수익률과 손익만 공개",
                      "items": [{"label": "이달 수익률", "sub": "기준가(좌수) 기준", "value": ret * 100},
                                {"label": "이달 손익", "sub": "입출금 제외", "value": pl, "kind": "won"},
                                {"label": "시장과 비교", "sub": "S&P500 · 코스피 중", "value": rank, "kind": "rank", "color": "#e8c86a"}]},
                      "cues": {"i1": {"at": pcs(ret)}, "i2": {"at": won(pl).split(" ")[0]}, "i3": {"at": ("이겼어요." if rank == 1 else "꼴찌예요." if rank == 3 else "두")}}},
            "sfx": [{"at": 0, "sound": "whoosh_big"}, {"at": pcs(ret), "sound": "ticks", "dur": 1.2}, {"at": ("이겼어요." if rank == 1 else "꼴찌예요." if rank == 3 else "두"), "sound": "hit", "big": False}],
            "actors": [{"id": "magnifier_phone" if win else "pose_endure_eyes_closed", "x": 1760, "y": 960, "h": 300, "enter": "rise", "at": 0.3, "anim": "idle"}]}
    if not lead_vs:
        hook["props"]["cues"]["i3"] = {"at": "부족했어요."}
        hook["sfx"][-1]["at"] = "부족했어요."

    scenes = [hook]
    scenes.append({"type": "clip", "asset": "yearend_report", "focus": 0.4,
                   "lines": [{"who": "A", "text": f"토스의 미국 ETF 다섯 종목과, 업비트의 비트코인·이더리움. 매일 조금씩 사 모으는 장기 포트, {M}월 결산이에요."}],
                   "props": {"lines": [{"text": f"장기 포트 {M}월", "at": 0.3, "size": 110}, {"text": "월간 결산", "at": "결산이에요.", "color": "gold", "size": 130}], "y": 520, "align": "left"}})
    cards = [{"top": "내 포트", "sub": "기준가 기준", "value": round(ret * 100, 2), "unit": "%", "decimals": 2, "at": "제", "glow": rank == 1}]
    if spx is not None:
        cards.append({"top": "S&P500", "sub": "원화 환산", "value": round(spx * 100, 2), "unit": "%", "decimals": 2, "at": "S&P500#2"})
    if kos is not None:
        cards.append({"top": "코스피", "sub": "지수", "value": round(kos * 100, 2), "unit": "%", "decimals": 2, "at": "코스피"})
    scenes.append({"type": "cards", "chapter": "시장과 비교",
                   "lines": [{"who": "Q", "text": "S&P500은 달러인데, 그대로 비교해도 돼요?"},
                             {"who": "A", "text": f"그래서 원화로 바꿔서 봤어요. 제 포트 {pc(ret)}, S&P500 {pc(spx) if spx is not None else '-'}, 코스피 {pc(kos) if kos is not None else '-'}."}],
                   "props": {"title": f"{M}월 성적표", "items": cards},
                   "actors": [{"id": "balance_scale", "x": 1760, "y": 960, "h": 300, "enter": "pop", "at": 0.3, "anim": "idle"}]})
    scenes.append({"type": "stage", "chapter": "규칙",
                   "lines": [{"who": "Q", "text": "매달 사는 시점은 어떻게 정해요?"},
                             {"who": "A", "text": "정하지 않아요. 시장이 오르든 내리든, 매일 같은 비중대로 조금씩 사요. 이 영상은 그 결과를 매달 그대로 공개하는 기록이에요."}],
                   "props": {"lines": [{"text": "타이밍 없음", "at": "정하지", "size": 110}, {"text": "매일 같은 비중", "at": "매일", "color": "gold", "size": 120}], "x0": 140, "x1": 1050, "align": "left"},
                   "actors": [{"id": "watering_dca", "x": 1450, "y": 940, "h": 600, "enter": "rise", "at": 0.2, "anim": "idle", "bubble": {"text": "오늘도 한 바가지", "at": "조금씩"}}]})
    scenes.append({"type": "bignum",
                   "lines": [{"who": "A", "text": f"그래서 {M}월에 적립이 들어간 날은, 기록된 {m['days']}일 중 {m['water_days']}일이었어요."}],
                   "props": {"title": "물 준 날", "value": m["water_days"], "unit": "일", "decimals": 0, "at": f"{m['water_days']}일이었어요.",
                             "caption": f"기록된 {m['days']}일 중", "footer": "금액은 공개하지 않아요", "footer_t": {"at": f"{m['water_days']}일이었어요."}, "color": "gold"}})
    scenes.append({"type": "custom", "chapter": "손익 달력",
                   "lines": [{"who": "A", "text": f"하루씩 칠해 볼게요. 빨간 칸은 이익, 파란 칸은 손실이에요."},
                             {"who": "A", "text": f"가장 좋았던 날은 {md(best['d'])}, {'플러스' if best['pl'] >= 0 else '마이너스'} {won(best['pl'])}. 가장 아팠던 날은 {md(worst['d'])}, {'마이너스' if worst['pl'] < 0 else '플러스'} {won(worst['pl'])}이었어요."}],
                   "props": {"script_file": "lt_calendar.js", "data": cal,
                             "cues": {"fill": {"at": "칠해"}, "best": {"at": "좋았던"}, "worst": {"at": "아팠던"}}},
                   "sfx": [{"at": 0, "sound": "whoosh"}, {"at": "칠해", "offset": 0.1, "sound": "pop", "repeat": 8, "every": 0.3}, {"at": "좋았던", "sound": "sparkle"}, {"at": "아팠던", "sound": "thud"}]})
    race_last = {"me": ret, "spx": spx, "kospi": kos}
    scenes.append({"type": "custom", "chapter": "3자 레이스",
                   "lines": [{"who": "A", "text": f"이번엔 한 달을 레이스로 다시 돌려 볼게요. 출발선은 {md(m['start'])}, 셋 다 0%에서 출발해요."},
                             {"who": "Q", "text": "누가 먼저 들어와요?"},
                             {"who": "A", "text": f"결승선 기준으로, 저는 {rank_txt}예요."}],
                   "props": {"script_file": "lt_race.js", "data": {"mode": "race", "title": f"{M}월 3자 레이스", "subtitle": "같은 날 출발 · 누적 수익률(원화)",
                                                                  "days": days_lbl, "lanes": race_lanes},
                             "cues": {"start": {"at": "돌려"}, "podium": {"at": "결승선"}}},
                   "sfx": [{"at": 0, "sound": "whoosh"}, {"at": "돌려", "sound": "riser"}, {"at": "결승선", "sound": "win"}]})
    if rank == 1:
        interp = [{"who": "A", "text": "한 달 이긴 건 운도 커요. 다음 달엔 질 수도 있어요. 그래서 저는 한 달 순위보다, 이 기록이 몇 년 쌓였을 때를 봐요."}]
        st_lines = [{"text": "한 달 1위", "at": "이긴", "size": 110}, {"text": "운도 크다", "at": "운도", "color": "gold", "size": 120}]
        act = "calculator_happy"
    elif rank == 3:
        interp = [{"who": "Q", "text": "그럼 그냥 지수 하나 사는 게 낫지 않아요?"},
                  {"who": "A", "text": "한 달만 보면 그래요. 그런데 이 포트엔 채권과 금이 들어 있어서, 시장이 크게 빠지는 달엔 덜 빠지게 짜 놨어요. 그 대가로 오르는 달엔 뒤처져요."}]
        st_lines = [{"text": "오르는 달엔 뒤처지고", "at": "오르는", "size": 96}, {"text": "빠지는 달엔 덜 빠지게", "at": "덜", "color": "gold", "size": 96}]
        act = "umbrella_shield"
    else:
        interp = [{"who": "A", "text": "중간이라는 건, 분산이 하는 일 그대로예요. 제일 센 시장만큼은 못 오르지만, 제일 약한 시장보다는 덜 흔들려요."}]
        st_lines = [{"text": "가운데 자리", "at": "중간이라는", "size": 110}, {"text": "분산이 하는 일", "at": "분산이", "color": "gold", "size": 110}]
        act = "balance_scale"
    scenes.append({"type": "stage", "lines": interp, "props": {"lines": st_lines, "x0": 140, "x1": 1050, "align": "left"},
                   "actors": [{"id": act, "x": 1450, "y": 940, "h": 580, "enter": "pop", "at": 0.2, "anim": "idle"}]})
    if hold:
        lines = [{"who": "A", "text": "종목별로 보면, 과수원 같아요. 나무마다 이번 달 수익률만큼 자랐어요."},
                 {"who": "A", "text": f"제일 많이 자란 나무는 {best_h['name']}, {pc(best_h['ret'])}. 가장 움츠린 나무는 {worst_h['name']}, {pc(worst_h['ret'])}이에요."}]
        scenes.append({"type": "custom", "chapter": "종목 과수원", "lines": lines,
                       "props": {"script_file": "lt_orchard.js", "data": {"mode": "orchard", "title": f"{M}월 종목 과수원", "subtitle": "나무 크기 = 이번 달 수익률(원화)", "items": orch_items},
                                 "cues": {"grow": {"at": "나무마다"}, "best": {"at": "제일"}, "worst": {"at": "움츠린"}}},
                       "sfx": [{"at": 0, "sound": "whoosh"}, {"at": "나무마다", "sound": "pop", "repeat": len(orch_items), "every": 0.45}, {"at": "제일", "sound": "sparkle"}, {"at": "움츠린", "sound": "down"}]})
    cw, ct = m["class_w"], m["class_target"]
    cls_rows = [{"name": k, "sub": f"목표 {ct.get(k, 0):.1f}%", "value": cw.get(k, 0), "target": ct.get(k), "color": c}
                for k, c in (("주식", "#e8c86a"), ("채권·금", "#9fd0ff"), ("코인", "#ffb36b")) if k in cw or k in ct]
    scenes.append({"type": "custom", "chapter": "비중 점검",
                   "lines": [{"who": "Q", "text": "그래서 지금 바구니는 어떻게 생겼어요?"},
                             {"who": "A", "text": "주식, 채권과 금, 코인 세 덩어리로 보면, 흰 눈금이 목표고 막대가 지금이에요."}],
                   "props": {"script_file": "lt_orchard.js", "data": {"mode": "bars", "title": "자산군 비중", "subtitle": "막대 = 지금 · 흰 눈금 = 목표", "unit": "%", "dec": 1, "lo": 0, "hi": 80, "rows": cls_rows},
                             "cues": {"r1": {"at": "주식,"}, "r2": {"at": "채권과"}, "r3": {"at": "코인"}}},
                   "sfx": [{"at": 0, "sound": "whoosh"}, {"at": "주식,", "sound": "pop"}, {"at": "채권과", "sound": "pop"}, {"at": "코인", "sound": "pop"}]})
    gap_rows = [{"name": g["name"], "value": g["now"], "target": g["target"], "txt": f"{g['now']:.1f}% · 목표 {g['target']:g}%",
                 "color": "#ff6f61" if g["out"] else "#e8c86a"} for g in sorted(m["gaps"], key=lambda g: -g["target"])]
    if gaps_out:
        names = ", ".join(g["name"] for g in gaps_out[:2])
        gap_line = f"목표에서 많이 벗어난 종목은 빨간색, {names}이에요. 이런 종목은 적립 비중으로 천천히 맞춰요."
        badge = {"text": f"밴드 밖: {names}", "color": "#ff6f61"}
    else:
        gap_line = f"일곱 종목 모두 목표의 ±{round(m['band_rel'] * 100)}% 밴드 안이에요. 그래서 이번 달은 비중을 손대지 않아요."
        badge = {"text": "전 종목 밴드 안 · 그대로 적립", "color": "#e8c86a"}
    scenes.append({"type": "custom",
                   "lines": [{"who": "A", "text": "종목 일곱 개로 쪼개 보면 이래요."}, {"who": "A", "text": gap_line}],
                   "props": {"script_file": "lt_orchard.js", "data": {"mode": "bars", "title": "목표 비중 vs 지금", "subtitle": f"목표의 ±{round(m['band_rel'] * 100)}% 밴드", "unit": "%", "dec": 1, "lo": 0,
                                                                     "hi": max(50, max(g["now"] for g in m["gaps"]) * 1.1), "rows": gap_rows, "badge": badge},
                             "cues": {"r1": {"at": "쪼개"}, "badge": {"at": "빨간색," if gaps_out else "밴드"}}},
                   "sfx": [{"at": 0, "sound": "whoosh"}, {"at": "쪼개", "sound": "pop", "repeat": len(gap_rows), "every": 0.25}, {"at": "빨간색," if gaps_out else "밴드", "sound": "check"}]})
    fx = m["fx"]
    fx_cards = [{"top": "원·달러 환율", "sub": f"{fx['start']:,.0f} → {fx['end']:,.0f}", "value": round((fx['chg'] or 0) * 100, 2), "unit": "%", "decimals": 2, "at": "환율은"},
                {"top": "이달 최대 낙폭", "sub": "최고점 대비", "value": round(m["mdd"] * 100, 2), "unit": "%", "decimals": 2, "at": "낙폭은"},
                {"top": "신고점", "sub": "기준가 최고 경신", "value": m["new_highs"], "unit": "번", "decimals": 0, "at": "신고점은"}]
    scenes.append({"type": "cards", "chapter": "숫자 세 개",
                   "lines": [{"who": "A", "text": f"숫자 세 개만 더 볼게요. 환율은 {pcs(fx['chg'] or 0)} {updown(fx['chg'] or 0)}. 달러 자산은 환율이 오르면 원화로 더 커져요."},
                             {"who": "A", "text": f"이달 최대 낙폭은 {pc(m['mdd'])}, 신고점은 {m['new_highs']}번 나왔어요."}],
                   "props": {"title": "환율 · 낙폭 · 신고점", "items": fx_cards},
                   "actors": [{"id": "currency_exchange", "x": 1780, "y": 960, "h": 280, "enter": "pop", "at": 0.3, "anim": "idle"}]})
    lim = m["dd_limit"]
    scenes.append({"type": "custom", "chapter": "버티기 선",
                   "lines": [{"who": "Q", "text": "얼마나 빠지면 불안해지세요?"},
                             {"who": "A", "text": f"제 버티기 선은 {pcs(lim, 0)}예요. 여기까지는 규칙대로 그냥 사요. 이번 달 가장 깊었던 순간은 {pc(m['mdd'])}, 선까지 한참 남았어요."}],
                   "props": {"script_file": "lt_orchard.js", "data": {"mode": "bars", "title": "버티기 선", "subtitle": "최고점 대비 낙폭", "unit": "%", "dec": 2, "lo": lim * 100 * 1.15, "hi": 2,
                                                                     "rows": [{"name": "이번 달 최저", "sub": md(m["mdd_day"]) if m.get("mdd_day") else "", "value": m["mdd"] * 100, "color": "#5aa0ff"},
                                                                              {"name": "월말 지금", "sub": "최고점 대비", "value": (m["dd_end"] or 0) * 100, "color": "#9fd0ff"}],
                                                                     "marker": {"value": lim * 100, "label": f"버티기 선 {pc(lim, 0)}"}},
                             "cues": {"mk": {"at": "버티기"}, "r1": {"at": "깊었던"}, "r2": {"at": "한참"}}},
                   "sfx": [{"at": 0, "sound": "whoosh"}, {"at": "버티기", "sound": "thud"}, {"at": "깊었던", "sound": "down"}, {"at": "한참", "sound": "check"}],
                   "actors": [{"id": "pose_endure_eyes_closed", "x": 1760, "y": 960, "h": 300, "enter": "pop", "at": 0.3, "anim": "idle"}]})
    ytd = m.get("ytd") or {}
    if ytd.get("ret") is not None:
        frm = "올해 1월부터" if str(ytd.get("from", "")).endswith("-01") else f"기록을 시작한 {md(ytd['from'])}부터"
        scenes.append({"type": "bignum", "chapter": "올해 누적",
                       "lines": [{"who": "A", "text": f"{frm} 쌓인 누적 수익률은 {pcs(ytd['ret'])}{'' if ytd['ret'] >= 0 else ' 손실'}이에요."}],
                       "props": {"title": "올해 누적 수익률", "value": round(ytd["ret"] * 100, 2), "unit": "%", "decimals": 2, "at": pcs(ytd["ret"]),
                                 "caption": f"{frm} · 기준가 기준", "footer": (f"같은 기간 코스피 {pc(ytd['kospi'])}" if ytd.get("kospi") is not None else "입출금 영향 제외"),
                                 "footer_t": {"at": pcs(ytd["ret"])}, "color": "gold"}})
    nxt = (M % 12) + 1
    plan = [{"text": "매일 같은 비중으로 적립", "at": "매일"},
            {"text": (f"{gaps_out[0]['name']} 비중 천천히 맞추기" if gaps_out else "비중 밴드 그대로 유지"), "at": "확인하고,"},
            {"text": f"버티기 선 {pc(lim, 0)} 확인", "at": "버티기"},
            {"text": f"{nxt}월 결과도 그대로 공개", "at": "공개"}]
    scenes.append({"type": "checklist", "chapter": f"{nxt}월 계획",
                   "lines": [{"who": "A", "text": f"{nxt}월 계획은 바뀌지 않아요. 매일 같은 비중으로 사고, 비중을 한 번 확인하고, 버티기 선을 지키고, 결과는 다음 달에 그대로 공개해요."}],
                   "props": {"title": f"{nxt}월 체크리스트", "items": plan},
                   "actors": [{"id": "clipboard_check", "x": 1720, "y": 960, "h": 320, "enter": "pop", "at": 0.3, "anim": "idle"}]})
    concl_q = "여러분 포트는 지난달 몇 퍼센트였나요? 댓글로 같이 비교해 봐요."
    scenes.append({"type": "title",
                   "lines": [{"who": "A", "text": f"{M}월은 {pc(ret)}, {rank_txt}. 한 달 성적은 바뀌어도, 규칙은 그대로 갑니다."},
                             {"who": "A", "text": "날짜별 기록은 설명란 첫 줄 블로그에 정리해 둘게요. " + concl_q}],
                   "props": {"asset": "fence_calm", "focus": [45, 40],
                             "lines": [{"text": "한 달 성적은 바뀌어도", "at": "한", "size": 80}, {"text": "규칙은 그대로", "at": "규칙은", "color": "gold", "size": 110}],
                             "sub": {"text": "날짜별 기록은 설명란 블로그에", "at": "날짜별"}, "sub_y": 700}})

    title = f"미국 ETF·비트코인 매일 적립 장기 포트 {M}월 결산, S&P500·코스피와 레이스 결과 {pc(ret)}"
    meta = {
        "search_query": "미국 ETF 적립식 포트폴리오 수익률",
        "titles": [title], "title_why": "검색어 '미국 ETF 적립식'을 앞에 두고 시장 비교 결과(%)를 붙임 — 원화 금액은 제목에 넣지 않음",
        "description": f"미국 ETF 적립식 포트폴리오(토스 미국 ETF 5종 + 업비트 비트코인·이더리움)를 매일 같은 비중으로 사 모은 {M}월 결과입니다. "
                       f"수익률 {pc(ret)}로 S&P500(원화)·코스피와 비교해 {rank_txt}였고, 손익 달력·종목별 수익률·비중·낙폭을 정리했습니다.",
        "blog_url": BLOG_HOME,
        "hashtags": ["미국ETF적립식", "장기투자", "포트폴리오", "QQQM", "SCHD"],
        "tags": ["미국 ETF 적립식", "장기 투자 포트폴리오", "QQQM", "SCHD", "TLT", "IAU", "비트코인 적립", "월간 결산", "토스증권", "업비트"],
        "pinned_comment": concl_q,
        "intro_chapter": f"{M}월 결과",
        "disclaimer_extra": "※ 개인 장기 포트의 월간 기록이며 수익률은 기준가(좌수법) 기준입니다. 총자산·투자 원금은 공개하지 않습니다. 과거 수익이 미래 수익을 보장하지 않으며 매수·매도 권유가 아닙니다.",
    }
    thumb_actor = "spring_bounce" if win else "pose_endure_eyes_closed"
    sb = {
        "slug": slug, "category": "장기 투자",
        "hot_words": ["S&P500", "코스피", "적립", "버티기", "레이스"],
        "music": {"style": "acoustic" if win else "piano"},
        "meta": meta,
        "thumbnail": {"top": f"미국 ETF 적립 {M}월 결산", "line1": "S&P500·코스피와", "main": f"{rank}위" if nb == 3 else pc(ret),
                      "main_size": 190, "main_top": 330, "actor": thumb_actor, "actor_x": 760, "actor_top": 40, "actor_h": 700},
        "thumbnail_alts": [
            {"top": f"미국 ETF 적립 {M}월 결산", "line1": "매일 샀더니", "main": pc(ret), "main_size": 170, "actor": "watering_dca", "actor_x": 760, "actor_top": 40, "actor_h": 700},
            {"top": f"미국 ETF 적립 {M}월 결산", "line1": "시장 이겼을까?", "main": "레이스", "main_size": 170, "actor": "thinking_question", "actor_x": 760, "actor_top": 40, "actor_h": 700},
        ],
        "scenes": scenes,
    }
    (work / "storyboard.json").write_text(json.dumps(sb, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 쇼츠 (전 장면 custom, 0프레임부터 움직임, 원화 금액 없음)
    hook_items = [{"label": "내 장기 포트", "sub": "매일 적립", "value": ret * 100, "color": C_ME}]
    if spx is not None:
        hook_items.append({"label": "S&P500", "sub": "원화 환산", "value": spx * 100, "color": C_SPX})
    if kos is not None:
        hook_items.append({"label": "코스피", "sub": "지수", "value": kos * 100, "color": C_KOS})
    win_word = {1: "제가 1등", 2: "제가 2등", 3: "제가 꼴찌"}.get(rank, "")
    sh = [
        {"type": "custom", "lines": [{"who": "A", "text": f"{M}월 한 달, 매일 사기만 한 제 포트와 S&P500, 코스피. 누가 이겼을까요?"}],
         "props": {"script_file": "lt_race.js", "data": {"mode": "hook", "title_v": f"{M}월 성적표", "instant": True, "items": hook_items}, "cues": {}},
         "sfx": [{"at": 0, "sound": "whoosh_big"}, {"at": 0.1, "sound": "ticks", "dur": 1.6}, {"at": "이겼을까요?", "sound": "pop"}]},
        {"type": "custom", "lines": [{"who": "A", "text": f"하루씩 칠하면 빨간 날 {m['up_days']}일, 파란 날 {m['down_days']}일이었어요."}],
         "props": {"script_file": "lt_calendar.js", "data": dict(cal, instant=True, pct=True, total_pct=round(ret * 100, 2), title_v=f"{M}월 수익률 달력"), "cues": {"best": {"at": "파란"}, "worst": {"at": f"{m['down_days']}일이었어요."}}},
         "sfx": [{"at": 0, "sound": "whoosh"}, {"at": 0.15, "sound": "pop", "repeat": 8, "every": 0.3}]},
        {"type": "custom", "lines": [{"who": "A", "text": f"같은 날 출발시켜 보면, 결승선에선 {win_word}예요."}],
         "props": {"script_file": "lt_race.js", "data": {"mode": "race", "title_v": f"{M}월 3자 레이스", "instant": True, "days": days_lbl, "lanes": race_lanes},
                   "cues": {"podium": {"at": "결승선에선"}}},
         "sfx": [{"at": 0, "sound": "whoosh"}, {"at": 0.15, "sound": "riser"}, {"at": "결승선에선", "sound": "win"}]},
    ]
    if hold:
        sh.append({"type": "custom", "lines": [{"who": "A", "text": f"종목 중엔 {best_h['name']}이 {pcs(best_h['ret'])}로 제일 크게 자랐어요."}],
                   "props": {"script_file": "lt_orchard.js", "data": {"mode": "orchard", "title_v": "종목 과수원", "instant": True, "items": orch_items},
                             "cues": {"best": {"at": "제일"}, "worst": {"at": "자랐어요."}}},
                   "sfx": [{"at": 0, "sound": "whoosh"}, {"at": 0.1, "sound": "pop", "repeat": len(orch_items), "every": 0.18}, {"at": "제일", "sound": "sparkle"}]})
    sh.append({"type": "custom", "lines": [{"who": "A", "text": f"{M}월은 {pc(ret)}. 순위는 바뀌어도, 매일 사는 규칙은 그대로예요."}],
               "props": {"script_file": "lt_orchard.js", "data": {"mode": "outro", "lines": [{"text": f"{M}월 {pc(ret)} · {rank}위" if nb == 3 else f"{M}월 {pc(ret)}", "color": "white"}, {"text": "규칙은 그대로", "color": "gold"}]},
                         "cues": {"l1": {"at": 0.2}, "l2": {"at": "규칙은"}}},
               "sfx": [{"at": 0, "sound": "whoosh"}, {"at": "규칙은", "sound": "sparkle"}],
               "actors": [{"id": "confident_thumbs", "x": 540, "y": 1390, "h": 300, "enter": "rise", "at": 0.3, "anim": "idle", "react": [{"anim": "shake", "at": "규칙은"}]}]})
    shorts = {
        "slug": slug + "-shorts", "format": "vertical", "category": "장기 투자",
        "music": sb["music"], "hot_words": ["S&P500", "코스피", "레이스"],
        "header": f"장기 포트 {M}월 <b>{pc(ret)}</b><br>S&P500·코스피와 레이스",
        "longform": {"title": title},
        "thumbnail": {"top": "미국 ETF 매일 적립", "line1": "S&P500·코스피와", "main": "누가 1등?", "actor": "thinking_question"},
        "meta": {"titles": [f"미국 ETF 매일 적립 포트 vs S&P500 vs 코스피, {M}월 레이스 누가 이겼을까?"],
                 "title_why": "많이 들고 있는 이름(S&P500·코스피) + 질문, 답은 영상 끝에서",
                 "description": f"미국 ETF 5종과 비트코인·이더리움을 매일 같은 비중으로 사는 장기 포트의 {M}월 결과를 S&P500(원화)·코스피와 비교했습니다.",
                 "hashtags": ["미국ETF적립식", "장기투자", "S&P500", "코스피", "shorts"],
                 "pinned_comment": "여러분 포트는 지난달 S&P500보다 잘했나요, 못했나요?",
                 "disclaimer_extra": meta["disclaimer_extra"]},
        "scenes": sh,
    }
    (work / "shorts" / "storyboard.json").write_text(json.dumps(shorts, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK: {slug} · {M}월 {pc(ret)} · 손익 {pl:+,}원 · {rank_txt} · 롱폼 {len(scenes)}장면 · 쇼츠 {len(sh)}장면 → {work}")


if __name__ == "__main__":
    main()
