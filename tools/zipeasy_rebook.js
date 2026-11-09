#!/usr/bin/env node
/*
 * zipeasy_rebook.js
 *
 * Watches a 집이지(zipeasy) move-in pre-inspection reservation and, when a slot
 * EARLIER than the currently-held slot opens up, moves the reservation to the
 * earliest such slot. Read-only by default; pass --apply to actually change.
 *
 * No personal data is stored in this file — everything is passed as arguments.
 *
 *   node tools/zipeasy_rebook.js \
 *     --pcode HE0077 --dong "120동" --hosu 1503 \
 *     --name "홍길동" --phone 010-0000-0000 [--apply]
 *
 * Exit codes: 0 = ran ok (changed or nothing to do), 2 = error.
 */

const BASE = 'https://api.zipeasy.me';

function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  if (i !== -1 && i + 1 < process.argv.length) return process.argv[i + 1];
  return def;
}
const APPLY = process.argv.includes('--apply');

const PCODE = arg('pcode');
const DONG  = arg('dong');           // e.g. "120동"
const HOSU  = arg('hosu');           // e.g. "1503"
const NAME  = arg('name');
const PHONE = arg('phone');
const TYPE  = parseInt(arg('type', '1'), 10); // 1 = 사전점검

if (!PCODE || !DONG || !HOSU || !NAME || !PHONE) {
  console.error('missing required args (--pcode --dong --hosu --name --phone)');
  process.exit(2);
}

// time-slot ordering helpers: ptype like "p1230" -> minutes since midnight
const TIME_CODES = [
  'p0900','p0930','p1000','p1030','p1100','p1130','p1200','p1230',
  'p1300','p1330','p1400','p1430','p1500','p1530','p1600','p1630'
];
const pmin = (p) => {
  const h = parseInt(p.slice(1, 3), 10), m = parseInt(p.slice(3, 5), 10);
  return h * 60 + m;
};
const hhmm = (p) => p.slice(1, 3) + ':' + p.slice(3, 5);

async function j(method, path, { body, token } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['x-access-token'] = token;
  const res = await fetch(BASE + path, {
    method, headers, body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  const txt = await res.text();
  try { data = JSON.parse(txt); } catch { data = txt; }
  return { status: res.status, data };
}

function dateList(startMs, endMs) {
  // inclusive list of YYYY-MM-DD (KST) from start date to end date
  const out = [];
  const kst = (ms) => new Date(ms + 9 * 3600e3); // shift to KST wall clock
  const s = kst(startMs), e = kst(endMs);
  const d = new Date(Date.UTC(s.getUTCFullYear(), s.getUTCMonth(), s.getUTCDate()));
  const last = new Date(Date.UTC(e.getUTCFullYear(), e.getUTCMonth(), e.getUTCDate()));
  while (d <= last) {
    out.push(d.toISOString().slice(0, 10));
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return out;
}

(async () => {
  const stamp = new Date().toLocaleString('sv-SE', { timeZone: 'Asia/Seoul' });
  const log = (...a) => console.log(`[${stamp}]`, ...a);

  // 1) login
  const login = await j('POST', '/v2/room-members/login', {
    body: { project_code: PCODE, room_code: `${DONG.replace('동','')}-${HOSU}`,
            dong: DONG, hosu: Number(HOSU), name: NAME, phone: PHONE },
  });
  if (login.status !== 200 || !login.data?.data?.access_token) {
    log('LOGIN FAILED', login.status, JSON.stringify(login.data).slice(0, 300));
    process.exit(2);
  }
  const token = login.data.data.access_token;
  const profile = login.data.data.profile;
  const roomCode = profile.room_code;

  // 2) current reservation
  const cur = await j('GET',
    `/v2/reservations?project_code=${PCODE}&room_code=${roomCode}`, { token });
  const item = cur.data?.data?.items?.[0];
  if (!item) { log('NO CURRENT RESERVATION', JSON.stringify(cur.data).slice(0,200)); process.exit(2); }
  const groupCode = item.group_code;
  const curDate = item.reservation_date;
  const curPtype = item.ptype;
  const uid = item.uid;
  log(`current: ${curDate} ${hhmm(curPtype)} (uid ${uid}, group ${groupCode})`);

  // 3) reservable dates + whether the change window is still open
  const proj = await j('GET', `/v2/projects/view/${PCODE}`, { token });
  const pv = proj.data?.data || {};
  const nowMs = Date.now();
  if (pv.check_reservation_end_date && nowMs > pv.check_reservation_end_date) {
    log('CHANGE WINDOW CLOSED (past check_reservation_end_date). No action.');
    process.exit(0);
  }
  const dates = dateList(pv.check_start_date, pv.check_end_date);

  // current (date,time) rank
  const rank = (date, ptype) => dates.indexOf(date) * 10000 + pmin(ptype);
  const curRank = rank(curDate, curPtype);

  // 4) scan every date up to & including current date for EARLIER open slots
  const openEarlier = [];
  for (const date of dates) {
    if (dates.indexOf(date) > dates.indexOf(curDate)) break; // later date, skip
    const capRes = await j('GET',
      `/v2/check-reservations?project_code=${PCODE}&reservation_date=${date}&group_code=${groupCode}`, { token });
    const cap = capRes.data?.data?.items?.[0];
    if (!cap) continue;
    const cntRes = await j('GET',
      `/v2/reservations/count?project_code=${PCODE}&group_code=${groupCode}&reservation_date=${date}&type=${TYPE}`, { token });
    const counts = {};
    for (const c of (cntRes.data?.data?.items || [])) counts[c.PTYPE] = c.total;
    for (const code of TIME_CODES) {
      const capacity = cap[code] || 0;
      if (capacity <= 0) continue;                 // slot not offered
      if (rank(date, code) >= curRank) continue;   // not earlier than current
      const booked = counts[code] || 0;
      const remain = capacity - booked;
      if (remain > 0) openEarlier.push({ date, code, remain, rank: rank(date, code) });
    }
  }

  if (openEarlier.length === 0) {
    log('no earlier slot open. (still full)');
    process.exit(0);
  }
  openEarlier.sort((a, b) => a.rank - b.rank);
  const target = openEarlier[0];
  log('EARLIER SLOT OPEN =>', `${target.date} ${hhmm(target.code)} (remain ${target.remain})`,
      openEarlier.length > 1 ? `[+${openEarlier.length-1} more]` : '');

  if (!APPLY) { log('dry-run (no --apply): not changing.'); process.exit(0); }

  // 5) change reservation to the earliest open earlier slot
  const upd = await j('PUT', `/v2/reservations/${uid}`, {
    token,
    body: { project_code: PCODE, reservation_date: target.date, ptype: target.code,
            name: NAME, phone: PHONE, room_code: roomCode, register: NAME },
  });
  // verify
  const after = await j('GET',
    `/v2/reservations?project_code=${PCODE}&room_code=${roomCode}`, { token });
  const a = after.data?.data?.items?.[0];
  if (upd.status === 200 && a && a.reservation_date === target.date && a.ptype === target.code) {
    log('CHANGED ✅ ->', `${a.reservation_date} ${hhmm(a.ptype)}`);
    console.log('RESULT=CHANGED', `${a.reservation_date} ${hhmm(a.ptype)}`);
    process.exit(0);
  } else {
    log('CHANGE REJECTED (slot likely just filled).', upd.status,
        JSON.stringify(upd.data).slice(0, 200),
        'still at', a ? `${a.reservation_date} ${hhmm(a.ptype)}` : '?');
    console.log('RESULT=REJECTED');
    process.exit(0);
  }
})().catch(e => { console.error('ERROR', e && e.message); process.exit(2); });
