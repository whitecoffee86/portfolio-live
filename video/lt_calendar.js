// 장기 포트 월간 결산 — 손익 달력(월~일 7칸). 하루씩 칸이 채워지며 손익(만원) 표시, 물 준 날은 금색 물방울, 최고·최악의 날 강조. 세로 지원
// P={title,title_v,subtitle,first_dow(0=월),days_in_month,daily:[{day,pl,r(%),buy}],best_day,worst_day,instant,pct,total_pct}
//   pct:true 면 원화 대신 일별 수익률(%)과 월 수익률(total_pct)만 표시(쇼츠용 — 쇼츠엔 원화 금액 금지)
// cues: fill(채우기 시작), best, worst
const V = vertical;
heading((V && P.title_v) || P.title || '', V ? '' : (P.subtitle || ''));
const DOW = ['월', '화', '수', '목', '금', '토', '일'];
const nd = P.days_in_month, f0 = P.first_dow, rows = Math.ceil((f0 + nd) / 7);
const GX = V ? 40 : 130, GW = V ? 900 : 1080, GY = V ? 720 : 340, GH = V ? 520 : 540;
const cw = GW / 7, ch = Math.min(V ? 96 : 100, (GH - 44) / rows);
const pctf = v => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(1) + '%';
const man = v => (v > 0 ? '+' : v < 0 ? '−' : '') + (Math.abs(v) / 1e4).toFixed(Math.abs(v) >= 1e6 ? 0 : 1) + '만';
DOW.forEach((d, i) => {
  const h = el('div', 'abs', d);
  h.style.cssText += `left:${GX + i * cw}px;width:${cw}px;top:${GY}px;text-align:center;font-size:${V ? 26 : 28}px;font-weight:800;color:${i >= 5 ? '#9fb0cf' : '#e8edf8'}`;
});
const byDay = {}; (P.daily || []).forEach(x => { byDay[x.day] = x; });
const order = (P.daily || []).map(x => x.day);
const maxAbs = Math.max(1, ...(P.daily || []).map(x => Math.abs(x.pl)));
const cells = [];
for (let d = 1; d <= nd; d++) {
  const k = f0 + d - 1, c = k % 7, r = Math.floor(k / 7);
  const x = GX + c * cw + 4, y = GY + 44 + r * ch + 4, w = cw - 8, h = ch - 8;
  const e = byDay[d];
  const box = el('div', 'abs', `<div style="position:absolute;left:10px;top:6px;font-size:${V ? 20 : 22}px;font-weight:800;color:#9fb0cf">${d}</div>` +
    `<div class="pl" style="position:absolute;left:0;right:0;bottom:${V ? 8 : 10}px;text-align:center;font-size:${V ? 24 : 28}px;font-weight:900;color:#fff"></div>` +
    (e && e.buy ? `<div class="drop" style="position:absolute;right:8px;top:6px;width:${V ? 14 : 16}px;height:${V ? 14 : 16}px;border-radius:50% 0 50% 50%;transform:rotate(-45deg);background:#e8c86a;opacity:0"></div>` : ''));
  box.style.cssText += `left:${x}px;top:${y}px;width:${w}px;height:${h}px;border-radius:14px;background:rgba(255,255,255,${e ? .05 : .02});border:2px solid rgba(255,255,255,${e ? .12 : .05});box-sizing:border-box`;
  cells.push({d, e, box, pl: box.querySelector('.pl'), drop: box.querySelector('.drop'), idx: order.indexOf(d)});
}
// 오른쪽(세로는 아래) 누적 패널
const pan = el('div', 'abs', `<div style="font-size:${V ? 26 : 32}px;font-weight:800;color:#9fb0cf">${P.pct ? '이달 수익률' : '이달 누적 손익'}</div><div class="cum" style="font-size:${V ? 60 : 68}px;font-weight:900;line-height:1.1;white-space:nowrap">0원</div>` +
  `<div class="ud" style="font-size:${V ? 26 : 30}px;font-weight:800;color:#e8edf8;margin-top:6px"></div>` +
  (V ? '' : `<div style="font-size:26px;color:#9fb0cf;margin-top:18px;line-height:1.5"><span style="color:#e8c86a">◆</span> 금색 = 그날 적립<br><span style="color:#ff6f61">■</span> 이익 <span style="color:#5aa0ff">■</span> 손실</div>`));
pan.style.cssText += V ? `left:40px;width:900px;top:${GY + 44 + rows * ch + 16}px;text-align:center` : `left:1290px;width:520px;top:380px`;
const T0 = P.instant ? .15 : at('fill', .7), per = Math.min(.16, 3.2 / Math.max(1, order.length));
const TB = at('best', T0 + order.length * per + .8), TW = at('worst', TB + 1.5);
const cumEl = pan.querySelector('.cum'), udEl = pan.querySelector('.ud');
return t => {
  appear(pan, prog(t, .05, .4), 16);
  let cum = 0, up = 0, dn = 0;
  cells.forEach(o => {
    if (!o.e) { o.box.style.opacity = clamp(prog(t, .05, .4)); return; }
    const q = prog(t, T0 + o.idx * per, .3);
    o.box.style.opacity = clamp(.35 + q);
    if (q > 0) {
      const a = .25 + .6 * Math.min(1, Math.abs(o.e.pl) / maxAbs);
      o.box.style.background = o.e.pl >= 0 ? `rgba(255,111,97,${a * q})` : `rgba(90,160,255,${a * q})`;
      o.pl.textContent = P.pct ? pctf(o.e.r) : man(o.e.pl);
      o.box.style.transform = `scale(${q < 1 ? .85 + .15 * back(q, 2) : 1})`;
      if (o.drop) o.drop.style.opacity = q;
      if (q >= 1) { cum += o.e.pl; if (o.e.pl > 0) up++; else if (o.e.pl < 0) dn++; }
      else { cum += o.e.pl * q; }
    } else o.pl.textContent = '';
    const hl = (o.d === P.best_day && t >= TB) ? '#e8c86a' : (o.d === P.worst_day && t >= TW) ? '#e8edf8' : null;
    if (hl) { o.box.style.border = `4px solid ${hl}`; o.box.style.boxShadow = `0 0 ${14 + 16 * pulse(t, 2)}px ${hl}`; o.box.style.zIndex = 3; }
  });
  if (P.pct) { const fin = order.length ? prog(t, T0, order.length * per + .3) : 1; const v = (P.total_pct || 0) * eo(fin); cumEl.textContent = pctf(v).replace('%', '') .replace(/(\.\d)$/, '$1') + '%'; cumEl.textContent = (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(2) + '%'; cumEl.style.color = (P.total_pct || 0) >= 0 ? '#ff6f61' : '#5aa0ff'; }
  else { cumEl.textContent = (cum > 0 ? '+' : cum < 0 ? '−' : '') + fmtNum(Math.abs(Math.round(cum)), 0) + '원'; cumEl.style.color = cum >= 0 ? '#ff6f61' : '#5aa0ff'; }
  udEl.textContent = `오른 날 ${up} · 내린 날 ${dn}`;
};
