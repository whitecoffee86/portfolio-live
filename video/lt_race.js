// 장기 포트 월간 결산 — 숫자 훅 / 3자 레이스 + 시상대. 세로(쇼츠) 지원
// mode "hook": P={title,title_v,subtitle,items:[{label,sub,value,kind:'pct'|'won'|'rank',color}],instant,banner}  cues: i1,i2,i3,banner
//   instant:true 면 0.1초부터 칸마다 0에서 1.6초 롤업(쇼츠 첫 장면). 아니면 칸 틀(?)을 먼저 깔고 큐에서 롤업(롱폼)
// mode "race": P={title,title_v,subtitle,days:[라벨],lanes:[{name,color,vals:[소수]}],podium:[lane 순서],instant}  cues: start, podium
const V = vertical;
const pct = (v, d = 2) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(d) + '%';
const FMT = (s, v) => s.kind === 'won' ? (v > 0 ? '+' : v < 0 ? '−' : '') + fmtNum(Math.abs(Math.round(v)), 0) + '원' : s.kind === 'rank' ? Math.max(1, Math.round(v)) + '위' : pct(v);
const COLR = v => v > 0 ? '#ff6f61' : v < 0 ? '#5aa0ff' : '#e8edf8';

if ((P.mode || 'hook') === 'hook') {
  heading((V && P.title_v) || P.title || '', V ? '' : (P.subtitle || ''));
  const S = P.items, n = S.length;
  const pos = i => V ? {x: 70, y: 700 + i * 205, w: 840, h: 185} : {x: 150 + i * 560, y: 340, w: 500, h: 380};
const big = s => s.kind === 'won' ? (V ? 64 : 76) : (V ? 80 : 128);
  const boxes = S.map((s, i) => {
    const p = pos(i);
    const d = el('div', 'abs', `<div style="font-size:${V ? 34 : 42}px;font-weight:900;color:#fff">${s.label}</div>` +
      `<div class="v" style="font-size:${big(s)}px;font-weight:900;line-height:1.05;margin-top:${V ? 0 : 14}px;color:${s.color || COLR(s.value)}">?</div>` +
      `<div style="font-size:${V ? 26 : 30}px;color:#9fb0cf;font-weight:700;margin-top:${V ? 0 : 10}px">${s.sub || ''}</div>`);
    d.style.cssText += `left:${p.x}px;top:${p.y}px;width:${p.w}px;height:${p.h}px;box-sizing:border-box;border-radius:28px;` +
      `background:rgba(255,255,255,.06);border:3px solid ${i === 0 ? '#e8c86a' : 'rgba(255,255,255,.22)'};display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center`;
    return d;
  });
  const ban = el('div', 'abs', P.banner || ''); if (!P.banner) ban.style.display = 'none';
  ban.style.cssText += V ? `left:70px;width:840px;top:1310px;text-align:center;font-size:38px;font-weight:900;color:#0c1630;background:#e8c86a;border-radius:18px;padding:12px 0`
    : `left:200px;width:1520px;top:790px;text-align:center;font-size:46px;font-weight:900;color:#0c1630;background:#e8c86a;border-radius:18px;padding:14px 0`;
  const TS = S.map((_, i) => P.instant ? .1 + i * .25 : at('i' + (i + 1), .6 + i * 1.5)), TB = at('banner', TS[n - 1] + 1.8);
  return t => {
    boxes.forEach((b, i) => {
      const s = S[i];
      const qa = P.instant ? prog(t, TS[i], .45) : prog(t, .05 + i * .1, .35);
      const qn = eo(prog(t, TS[i], 1.6));
      b.style.opacity = clamp(qa * 3);
      const sc = P.instant && qa < 1 ? .4 + .6 * back(qa, 2.2) : 1;
      b.style.transform = `translateY(${(P.instant ? 6 * Math.sin(t * 2.2 + i * 1.3) : 0) + (P.instant ? 0 : (1 - eo(qa)) * 24)}px) scale(${sc})`;
      const v = b.querySelector('.v');
      v.textContent = t >= TS[i] ? FMT(s, s.kind === 'rank' ? s.value + (3 - s.value) * (1 - qn) : s.value * qn) : '?';
      v.style.color = t >= TS[i] ? (s.color || COLR(s.value)) : 'rgba(232,200,106,.45)';
      if (t >= TS[i] + 1.6 && i === 0) b.style.boxShadow = `0 0 ${16 + 20 * pulse(t, 1.8)}px #e8c86a88`;
    });
    appear(ban, prog(t, TB, .45), 24, .9);
  };
}

// ───── race
heading((V && P.title_v) || P.title || '', V ? '' : (P.subtitle || ''));
const L = P.lanes, N = P.days.length;
const all = L.flatMap(l => l.vals.filter(v => v != null)).concat([0]);
let lo = Math.min(...all), hi = Math.max(...all);
const pad = Math.max(.004, (hi - lo) * .12); lo -= pad; hi += pad;
const X0 = V ? 90 : 170, X1 = V ? 640 : 1000, Y0 = V ? 710 : 340, Y1 = V ? 1090 : 860;
const xs = i => X0 + (X1 - X0) * (N <= 1 ? 1 : i / (N - 1));
const ys = v => Y1 - (Y1 - Y0) * (v - lo) / (hi - lo);
const zero = ys(0);
let grid = `<line x1="${X0}" x2="${X1}" y1="${zero}" y2="${zero}" stroke="rgba(255,255,255,.35)" stroke-width="3" stroke-dasharray="10 8"/>`;
grid += `<text x="${X0 - 14}" y="${zero + 9}" fill="#9fb0cf" font-size="${V ? 24 : 26}" font-weight="700" text-anchor="end">0%</text>`;
const paths = L.map(l => {
  let d = '', first = true;
  l.vals.forEach((v, i) => { if (v == null) return; d += (first ? 'M' : 'L') + xs(i).toFixed(1) + ' ' + ys(v).toFixed(1) + ' '; first = false; });
  return `<path class="ln" d="${d}" fill="none" stroke="${l.color}" stroke-width="${V ? 7 : 8}" stroke-linejoin="round" stroke-linecap="round"/>`;
}).join('');
const sv = svg(grid + paths);
const lns = [...sv.querySelectorAll('.ln')];
lns.forEach(p => { p.style.strokeDasharray = p.getTotalLength(); p.style.strokeDashoffset = p.getTotalLength(); });
const dl = el('div', 'abs', P.days[0] || ''); dl.style.cssText += `left:${X0 - 60}px;top:${Y1 + 14}px;width:120px;text-align:center;font-size:24px;color:#9fb0cf;font-weight:700`;
const dr = el('div', 'abs', ''); dr.style.cssText += `top:${Y1 + 14}px;width:160px;text-align:center;font-size:24px;color:#9fb0cf;font-weight:700`;
const tags = L.map(l => {
  const d = el('div', 'abs', ''); d.style.cssText += `font-size:${V ? 28 : 32}px;font-weight:900;color:${l.color};white-space:nowrap;padding:4px 12px;border-radius:12px;background:rgba(12,22,48,.82)`;
  const dot = el('div', 'abs', ''); dot.style.cssText += `width:${V ? 22 : 26}px;height:${V ? 22 : 26}px;border-radius:50%;background:${l.color};box-shadow:0 0 14px ${l.color}`;
  return {d, dot};
});
// 시상대
const ord = P.podium || L.map((_, i) => i).sort((a, b) => (L[b].vals[N - 1] ?? -9) - (L[a].vals[N - 1] ?? -9));
const PX = V ? 110 : 1330, PW = V ? 240 : 140, PG = V ? 30 : 16, PB = V ? 1380 : 860;
const hts = V ? [170, 120, 80] : [260, 180, 120];
const place = [1, 0, 2]; // 2위-1위-3위 배치
const pods = ord.map((li, r) => {
  const slot = place.indexOf(r), x = PX + slot * (PW + PG), h = hts[r];
  const b = el('div', 'abs', `<div style="font-size:${V ? 54 : 60}px;font-weight:900;color:#0c1630">${r + 1}</div>`);
  b.style.cssText += `left:${x}px;width:${PW}px;top:${PB - h}px;height:${h}px;border-radius:14px 14px 0 0;background:${r === 0 ? '#e8c86a' : r === 1 ? '#c9d3e6' : '#c58b5a'};display:flex;align-items:flex-start;justify-content:center;padding-top:8px;box-sizing:border-box;transform-origin:bottom`;
  const nm = el('div', 'abs', `<div style="font-size:${V ? 30 : 30}px;font-weight:900;color:${L[li].color}">${L[li].name}</div><div style="font-size:${V ? 30 : 30}px;font-weight:900;color:#fff">${pct(L[li].vals[N - 1] ?? 0)}</div>`);
  nm.style.cssText += `left:${x - 30}px;width:${PW + 60}px;top:${PB - h - (V ? 86 : 88)}px;text-align:center;line-height:1.2`;
  return {b, nm, r};
});
const TS0 = P.instant ? .15 : at('start', .6), TP = at('podium', TS0 + 4.2), RUN = Math.max(2.4, Math.min(4.2, TP - TS0 - .2));
return t => {
  const q = eio(prog(t, TS0, RUN));
  const fi = q * (N - 1), k = Math.floor(fi), fr = fi - k;
  lns.forEach(p => { p.style.strokeDashoffset = p.getTotalLength() * (1 - q); });
  L.forEach((l, i) => {
    const a = l.vals[Math.min(k, N - 1)] ?? 0, b = l.vals[Math.min(k + 1, N - 1)] ?? a, v = lerp(a, b, fr);
    const x = lerp(xs(Math.min(k, N - 1)), xs(Math.min(k + 1, N - 1)), fr), y = ys(v);
    const o = tags[i], vis = clamp(prog(t, .05, .3));
    o.dot.style.opacity = vis; o.dot.style.left = (x - (V ? 11 : 13)) + 'px'; o.dot.style.top = (y - (V ? 11 : 13)) + 'px';
    o.d.style.opacity = vis; o.d.textContent = `${l.name} ${pct(v)}`;
    o.d.style.left = (x + 20) + 'px'; o.d.style.top = (y - 24 + (i - 1) * (V ? 6 : 0)) + 'px';
  });
  // 겹치는 꼬리표는 세로로 벌려 줌
  const ts = tags.map((o, i) => ({o, y: parseFloat(o.d.style.top)})).sort((a, b) => a.y - b.y);
  for (let j = 1; j < ts.length; j++) if (ts[j].y - ts[j - 1].y < (V ? 46 : 50)) { ts[j].y = ts[j - 1].y + (V ? 46 : 50); ts[j].o.d.style.top = ts[j].y + 'px'; }
  dl.style.opacity = clamp(prog(t, .05, .3));
  dr.textContent = P.days[Math.min(N - 1, Math.round(fi))] || ''; dr.style.left = (lerp(X0, X1, q) - 80) + 'px'; dr.style.opacity = q > .05 ? 1 : 0;
  pods.forEach(o => {
    const qq = prog(t, TP + (2 - o.r) * .35, .5);
    o.b.style.opacity = clamp(qq * 3); o.b.style.transform = `scaleY(${back(qq, 1.6)})`;
    appear(o.nm, prog(t, TP + (2 - o.r) * .35 + .3, .4), 20);
    if (o.r === 0 && qq >= 1) o.b.style.boxShadow = `0 0 ${18 + 20 * pulse(t, 2)}px #e8c86aaa`;
  });
};
