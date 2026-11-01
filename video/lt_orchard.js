// 장기 포트 월간 결산 — 종목 과수원 / 막대 / 결론. 세로 지원
// mode "orchard": P={title,title_v,subtitle,items:[{name,sym,ret(소수)}],instant}  cues: grow(첫 나무), best, worst
//   나무마다 수익률만큼 잎이 커지고(이익=초록+금색 열매, 손실=작게+갈색 잎이 떨어짐) 아래에 이름·% 표시
// mode "bars": P={title,title_v,subtitle,unit,dec,lo,hi,rows:[{name,sub,value,target,color,txt}],marker:{value,label},badge:{text,color},instant}
//   cues: r1..rN, mk, badge  (음수는 0선 왼쪽으로 자람, target 이 있으면 금색 눈금으로 목표 표시)
// mode "outro": P={lines:[{text,color}]}  cues: l1, l2
const V = vertical, mode = P.mode || 'orchard';
const pct = (v, d = 2) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(d) + '%';

if (mode === 'outro') {
  const COLS = {white: '#fff', gold: '#e8c86a', red: '#ff6f61', green: '#5fd69a'};
  const rr = rnd(31), CY = V ? 860 : 470;
  const coins = [...Array(V ? 12 : 16)].map(() => {
    const c = el('div', 'abs', '$'); const s = 28 + rr() * 24;
    c.style.cssText += `width:${s}px;height:${s}px;border-radius:50%;background:radial-gradient(circle at 35% 30%,#fff3c0,#e8c86a 50%,#a8801e);color:#3a2a06;font-size:${s * .55}px;font-weight:900;display:flex;align-items:center;justify-content:center;opacity:.8`;
    return {c, x: (V ? 60 : 120) + rr() * (V ? 840 : 1680), d: rr() * 4, sp: 150 + rr() * 120};
  });
  const LS = (P.lines || []).map((l, i) => {
    const d = el('div', 'abs', l.text);
    const size = l.size || (V ? (i === 0 ? 72 : 96) : (i === 0 ? 84 : 110));
    d.style.cssText += `left:${V ? 40 : 140}px;width:${V ? 880 : 1240}px;top:${CY - (V ? 150 : 170) + i * (V ? 124 : 140)}px;text-align:center;font-size:${size}px;font-weight:900;color:${COLS[l.color] || l.color || '#fff'};line-height:1.15`;
    return d;
  });
  const TL = LS.map((_, i) => at('l' + (i + 1), .2 + i * 1.2));
  return t => {
    coins.forEach(o => { const y = -80 + ((t + o.d) * o.sp) % ((V ? 1500 : 1100)); o.c.style.left = o.x + 'px'; o.c.style.top = y + 'px'; o.c.style.transform = `rotate(${t * 90 + o.d * 50}deg)`; });
    LS.forEach((d, i) => { const q = prog(t, TL[i], .5); d.style.opacity = clamp(q * 2); d.style.transform = `scale(${q < 1 ? .6 + .4 * back(q, 2) : 1})`; });
  };
}

heading((V && P.title_v) || P.title || '', V ? '' : (P.subtitle || ''));

if (mode === 'bars') {
  const R = P.rows, n = R.length, unit = P.unit ?? '%', dec = P.dec ?? 1;
  const vals = R.flatMap(r => [r.value, r.target ?? r.value]).concat([0, P.marker ? P.marker.value : 0]);
  const lo = P.lo ?? Math.min(0, ...vals), hi = P.hi ?? Math.max(0, ...vals) * 1.08;
  const X0 = V ? 90 : 560, X1 = V ? 880 : 1560, Y0 = V ? 780 : 360;
  const GAP = V ? Math.min(150, 520 / n) : Math.min(120, 480 / n), BH = V ? 50 : (n > 5 ? 44 : 58);
  const bx = v => X0 + (X1 - X0) * (v - lo) / (hi - lo), z = bx(0);
  svg(`<line x1="${z}" x2="${z}" y1="${Y0 - 24}" y2="${Y0 + GAP * (n - 1) + BH + (V ? 44 : 24)}" stroke="rgba(255,255,255,.35)" stroke-width="3"/>`);
  const rows = R.map((r, i) => {
    const y = Y0 + i * GAP + (V ? 38 : 0);
    const nm = el('div', 'abs', `<span style="font-size:${V ? 32 : 40}px;font-weight:900;color:#fff">${r.name}</span>${r.sub ? `<span style="font-size:${V ? 24 : 26}px;color:#9fb0cf;margin-left:12px;${V ? '' : 'display:block;margin-left:0'}">${r.sub}</span>` : ''}`);
    nm.style.cssText += V ? `left:${X0}px;top:${y - 46}px;width:860px` : `left:80px;top:${y - 2}px;width:450px;text-align:right`;
    const bar = el('div', 'abs', ''); bar.style.cssText += `top:${y}px;height:${BH}px;width:0;border-radius:10px;background:${r.color || (r.value < 0 ? '#5aa0ff' : '#e8c86a')}`;
    const val = el('div', 'abs', ''); val.style.cssText += `top:${y + 2}px;font-size:${V ? 38 : 44}px;font-weight:900;color:${r.color || (r.value < 0 ? '#5aa0ff' : '#e8c86a')};white-space:nowrap`;
    let tg = null;
    if (r.target != null) { tg = el('div', 'abs', ''); tg.style.cssText += `left:${bx(r.target) - 3}px;top:${y - 10}px;width:6px;height:${BH + 20}px;background:#fff;border-radius:3px;box-shadow:0 0 8px #fff`; }
    return {r, nm, bar, val, tg, y};
  });
  let mk = null, mkl = null;
  if (P.marker) {
    const x = bx(P.marker.value);
    mk = el('div', 'abs', ''); mk.style.cssText += `left:${x - 2}px;top:${Y0 - 30}px;width:0;height:${GAP * (n - 1) + BH + 60 + (V ? 38 : 0)}px;border-left:5px dashed #ff6f61`;
    mkl = el('div', 'abs', P.marker.label); mkl.style.cssText += `left:${Math.max(V ? 20 : 60, x - 180)}px;width:360px;text-align:center;top:${Y0 + GAP * (n - 1) + BH + (V ? 86 : 44)}px;font-size:${V ? 28 : 32}px;font-weight:900;color:#ff6f61`;
  }
  let bd = null;
  if (P.badge) { bd = el('div', 'abs', P.badge.text); bd.style.cssText += V ? `left:70px;width:840px;top:1300px;text-align:center;font-size:34px;font-weight:900;color:#0c1630;background:${P.badge.color || '#e8c86a'};border-radius:16px;padding:10px 0` : `left:460px;width:1200px;top:${Math.min(860, Y0 + GAP * n + 60)}px;text-align:center;font-size:40px;font-weight:900;color:#0c1630;background:${P.badge.color || '#e8c86a'};border-radius:16px;padding:10px 0`; }
  const TR = R.map((_, i) => at('r' + (i + 1), (P.instant ? .15 : .7) + i * (P.instant ? .25 : 1))), TM = at('mk', TR[n - 1] + 1), TBd = at('badge', TR[n - 1] + 1.6);
  return t => {
    rows.forEach((o, i) => {
      appear(o.nm, prog(t, .05 + i * .08, .35), 14);
      if (o.tg) o.tg.style.opacity = clamp(prog(t, .1 + i * .08, .35));
      const q = eo(prog(t, TR[i], 1.0)), v = o.r.value * q, xe = bx(v);
      o.bar.style.left = Math.min(z, xe) + 'px'; o.bar.style.width = Math.abs(xe - z) + 'px';
      o.val.textContent = q > 0 ? (q >= 1 && o.r.txt ? o.r.txt : (v > 0 && unit === '%' && P.sign ? '+' : v < 0 ? '−' : '') + fmtNum(Math.abs(v), dec) + unit) : '';
      const w = o.val.offsetWidth || 160;
      o.val.style.left = (o.r.value >= 0 ? xe + 14 : Math.max(10, xe - 14 - w)) + 'px';
    });
    if (mk) { const q = prog(t, TM, .5); mk.style.opacity = q; mkl.style.opacity = q; }
    if (bd) appear(bd, prog(t, TBd, .45), 20, .85);
  };
}

// ───── orchard
const I = P.items, n = I.length;
const perRow = V ? 4 : n, nr = Math.ceil(n / perRow);
const slot = i => {
  const r = Math.floor(i / perRow), c = i % perRow, cnt = Math.min(perRow, n - r * perRow);
  const W = V ? 880 : 1640, x0 = V ? 50 : 140;
  return {x: x0 + W * (c + .5) / cnt, g: V ? 980 + r * 310 : 800};
};
const maxAbs = Math.max(.01, ...I.map(it => Math.abs(it.ret ?? 0)));
const ground = svg(`${[...Array(nr)].map((_, r) => { const g = slot(r * perRow).g; return `<rect x="${V ? 30 : 100}" y="${g}" width="${V ? 900 : 1720}" height="10" rx="5" fill="rgba(120,90,50,.7)"/>`; }).join('')}`);
const trees = I.map((it, i) => {
  const s = slot(i), r = it.ret ?? 0, pos = r >= 0, k = Math.abs(r) / maxAbs;
  const sc = V ? .7 : 1;
  const crown = (pos ? 70 + 60 * k : 70 - 30 * k) * sc, trunkH = 80 * sc;
  const tr = el('div', 'abs', ''); tr.style.cssText += `left:${s.x - 9 * sc}px;top:${s.g - trunkH}px;width:${18 * sc}px;height:${trunkH}px;background:#8a5a32;border-radius:6px;transform-origin:bottom`;
  const cr = el('div', 'abs', ''); cr.style.cssText += `left:${s.x - crown}px;top:${s.g - trunkH - crown * 1.6}px;width:${crown * 2}px;height:${crown * 2}px;border-radius:50%;background:radial-gradient(circle at 40% 35%,${pos ? '#7ee0a6,#2f9e63' : '#c9a36a,#7a5a2e'});transform-origin:50% 100%`;
  const fr = [], rr = rnd(11 + i * 7);
  const nf = it.ret == null ? 0 : pos ? Math.max(1, Math.round(1 + 5 * k)) : Math.max(1, Math.round(1 + 3 * k));
  for (let j = 0; j < nf; j++) {
    const f = el('div', 'abs', ''), a = rr() * Math.PI * 2, d = rr() * crown * .7, sz = (pos ? 22 : 18) * sc;
    f.style.cssText += `width:${sz}px;height:${sz}px;border-radius:${pos ? '50%' : '50% 0 50% 0'};background:${pos ? '#e8c86a' : '#a8743a'};box-shadow:${pos ? '0 0 10px #e8c86a' : 'none'}`;
    fr.push({f, x: s.x + Math.cos(a) * d - sz / 2, y: s.g - trunkH - crown * .6 + Math.sin(a) * d * .8 - sz / 2, fall: !pos, dl: rr()});
  }
  const lab = el('div', 'abs', `<div style="font-size:${V ? 26 : 30}px;font-weight:900;color:#fff">${it.name}</div><div class="p" style="font-size:${V ? 34 : 40}px;font-weight:900;color:${it.ret == null ? '#9fb0cf' : pos ? '#ff6f61' : '#5aa0ff'}"></div>`);
  lab.style.cssText += `left:${s.x - 110}px;width:220px;top:${s.g + 16}px;text-align:center;line-height:1.15`;
  return {it, s, tr, cr, fr, lab, crown, trunkH};
});
const TG = I.map((_, i) => P.instant ? .1 + i * .18 : at('grow', .6) + i * .45);
const TB = at('best', TG[n - 1] + 1), TW = at('worst', TB + 1.5);
return t => {
  trees.forEach((o, i) => {
    o.lab.style.opacity = clamp(prog(t, .05, .3));
    o.tr.style.opacity = clamp(prog(t, .05, .3));
    const q = prog(t, TG[i], .9);
    o.cr.style.opacity = clamp(.25 + q);
    o.cr.style.transform = `scale(${.3 + .7 * (q < 1 ? back(q, 1.8) : 1) + (q >= 1 ? .02 * Math.sin(t * 2 + i) : 0)})`;
    o.lab.querySelector('.p').textContent = o.it.ret == null ? '—' : q > 0 ? pct(o.it.ret * eo(q)) : '';
    o.fr.forEach(f => {
      const qf = prog(t, TG[i] + .6 + f.dl * .4, .4);
      f.f.style.opacity = qf > 0 ? (f.fall ? clamp(1.6 - prog(t, TG[i] + 1, 2.2)) : 1) : 0;
      const dy = f.fall ? 140 * eio(prog(t, TG[i] + 1, 2.2)) : 0;
      f.f.style.left = (f.x + (f.fall ? 20 * Math.sin(t * 3 + f.dl * 6) : 0)) + 'px'; f.f.style.top = (f.y + dy) + 'px';
      f.f.style.transform = `scale(${qf < 1 ? back(qf, 2) : 1})`;
    });
    const isB = i === 0 && t >= TB, isW = i === n - 1 && t >= TW && n > 1;
    o.cr.style.boxShadow = isB ? `0 0 ${20 + 20 * pulse(t, 2)}px #e8c86a` : isW ? `0 0 ${16 + 14 * pulse(t, 2)}px #5aa0ff` : 'none';
  });
};
