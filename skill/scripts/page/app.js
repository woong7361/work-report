const SUBMIT_URL = "{{SUBMIT_URL}}";
const HAS_README = {{HAS_README}};
// 알림을 눌러 들어오면 어떤 보고서를 열지 주소가 말해 준다
const START = new URLSearchParams(location.search).get('path') || '';
const NL = String.fromCharCode(10);     // 안내문 줄바꿈
// PMS 주소가 설정돼 있는지. 설정 화면을 아직 열지 않았으면 conf 는 비어 있으므로
// 띄울 때의 값을 박아 둔다. 설정을 저장하면 conf 가 채워져 그때부터는 그쪽이 맞다.
const PMS_ON = {{PMS_ON}};
const AREA_NAME = { daily:'일일 보고', weekly:'주간 보고', log:'한 일 목록', raw:'수집 원본' };
const AREA_TAG  = { daily:'일일', weekly:'주간', log:'한 일', raw:'원본' };
const CUSTOM_NAME = { 'report-format.md':'보고서 양식', 'writing-rules.md':'글쓰기 문체',
                      'my-reports.md':'내 보고서' };
// 제출문 절은 붙여넣기용이라 보고서 전체가 아니라 그 절만 클립보드에 담는다
const SUBMIT_HEAD = '제출문';
// 보고서를 쓸 때 근거로 들춰 보는 것들이다. 매번 펼쳐져 있으면 목록만 길어진다
const FOLDED = ['log', 'raw'];
const WD = ['일','월','화','수','목','금','토'];

let raw = '', orig = '', current = '', dirty = false;
let view = 'doc';            // doc | config | readme | setup
let setup = null;            // 무엇이 되어 있는지 (서버가 센 값)
let mode = 'preview';        // 문서를 읽는 방식: preview | raw
let editing = false, cellEditing = null;
let readme = null, conf = null, confDirty = false;
let INDEX = { byArea:{}, byName:{} };

const $ = id => document.getElementById(id);
const pad = n => (n < 10 ? '0' : '') + n;
const dig = (o, k) => k.split('.').reduce((a, x) => (a || {})[x], o);
const narrow = () => window.matchMedia('(max-width:1100px)').matches;
const areaOf = p => (p || '').split('/')[0];

function el(tag, cls, text){
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
function api(path, params){
  const q = new URLSearchParams(params || {}).toString();
  return q ? path + '?' + q : path;
}
function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

/* ============================================================ 마크다운
   보고서와 수집 원본, 사용 설명이 쓰는 문법만 다룬다. 들여쓴 목록을 단으로
   살려야 수집 원본이 읽힌다 - 지시 수십 개가 세션 정보와 같은 단으로 늘어서면
   무엇에 딸린 것인지 알 수 없다. */
function inline(s){
  return esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>')
    .replace(/~~([^~]+)~~/g, '<del>$1</del>')
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function splitRow(line){
  return line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|');
}
// cells: 표 칸에 원문 줄 번호를 달아 둘지. 그 자리에서 고치는 화면만 쓴다
function render(md, cells){
  const out = [], lines = md.split(/\r?\n/);
  const stack = [], liOpen = [];
  let i = 0, fence = null, para = [], quote = [], lastLi = false;

  const closeLi = () => {
    const n = liOpen.length - 1;
    if (n >= 0 && liOpen[n]) { out.push('</li>'); liOpen[n] = false; }
  };
  const closeList = () => { closeLi(); const s = stack.pop(); liOpen.pop(); out.push('</' + s.tag + '>'); };
  const closePara = () => { if (para.length) { out.push('<p>' + inline(para.join(' ')) + '</p>'); para = []; } };
  const closeQuote = () => { if (quote.length) { out.push('<blockquote>' + inline(quote.join(' ')) + '</blockquote>'); quote = []; } };
  const closeAll = () => { while (stack.length) closeList(); closePara(); closeQuote(); lastLi = false; };

  while (i < lines.length) {
    const ln = lines[i];
    if (fence !== null) {
      if (/^\s*```/.test(ln)) { out.push(esc(fence.join('\n'))); out.push('</code></pre>'); fence = null; }
      else fence.push(ln);
      i++; continue;
    }
    if (/^\s*```/.test(ln)) { closeAll(); out.push('<pre class="code"><code>'); fence = []; i++; continue; }

    const isTable = /^\s*\|/.test(ln) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i+1] || '');
    if (isTable) {
      closeAll();
      const cut = r => splitRow(r).map(c => c.trim());
      const head = cut(ln);
      out.push('<div class="tablewrap"><table' + (cells ? ' class="cells"' : '')
               + ' data-cols="' + head.length + '"><thead><tr>'
               + head.map(c => '<th>' + inline(c) + '</th>').join('') + '</tr></thead><tbody>');
      i += 2;
      while (i < lines.length && /^\s*\|/.test(lines[i])) {
        out.push('<tr>' + cut(lines[i]).map((c, j) =>
          '<td data-ln="' + i + '" data-c="' + j + '">' + inline(c) + '</td>').join('') + '</tr>');
        i++;
      }
      out.push('</tbody></table></div>');
      continue;
    }

    let m;
    if ((m = ln.match(/^(#{1,4})\s+(.*)$/))) {
      closeAll();
      out.push('<h' + m[1].length + '>' + inline(m[2]) + '</h' + m[1].length + '>');
    }
    else if (/^\s*(---+|\*\*\*+|___+)\s*$/.test(ln)) { closeAll(); out.push('<hr>'); }
    else if ((m = ln.match(/^\s*>\s?(.*)$/))) { while (stack.length) closeList(); closePara(); quote.push(m[1]); lastLi = false; }
    else if ((m = ln.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/))) {
      closePara(); closeQuote();
      const indent = m[1].replace(/\t/g, '    ').length;
      const tag = /\d/.test(m[2]) ? 'ol' : 'ul';
      while (stack.length && indent < stack[stack.length - 1].indent) closeList();
      if (!stack.length || indent > stack[stack.length - 1].indent) {
        // 바로 위 항목 안으로 들어간다. 부모의 <li>는 열어 둔 채로 중첩한다
        out.push('<' + tag + '>'); stack.push({ tag: tag, indent: indent }); liOpen.push(false);
      } else {
        closeLi();
        if (stack[stack.length - 1].tag !== tag) {
          closeList();
          out.push('<' + tag + '>'); stack.push({ tag: tag, indent: indent }); liOpen.push(false);
        }
      }
      out.push('<li>' + inline(m[3]));
      liOpen[liOpen.length - 1] = true;
      lastLi = true;
    }
    else if (ln.trim() === '') { closeAll(); }
    else if (lastLi && /^\s{2,}\S/.test(ln)) {
      // 한 항목이 여러 줄에 걸친 경우. 새 항목으로 떼면 목록이 두 배로 길어진다
      out[out.length - 1] += ' ' + inline(ln.trim());
    }
    else { while (stack.length) closeList(); lastLi = false; para.push(ln); }
    i++;
  }
  closeAll();
  return out.join('\n');
}

/* ============================================================ 이름 붙이기
   파일 이름은 2026-10-01 이지만 사람이 찾는 단서는 요일이다 */
function parseName(name){
  let m = name.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (m) return { kind:'day', y:+m[1], m:+m[2], d:+m[3] };
  m = name.match(/^(\d{4})-(\d{2})-(\d{2})_(\d{4})-(\d{2})-(\d{2})$/);
  if (m) return { kind:'span', y:+m[1], m:+m[2], d:+m[3], m2:+m[5], d2:+m[6] };
  return null;
}
function dayOf(p){ return WD[new Date(p.y, p.m - 1, p.d).getDay()]; }
// 레일의 달 묶음 안이라 연도를 뺀다
function shortLabel(name){
  const p = parseName(name);
  if (!p) return name;
  if (p.kind === 'day') return pad(p.m) + '-' + pad(p.d) + ' (' + dayOf(p) + ')';
  return pad(p.m) + '-' + pad(p.d) + ' ~ ' + pad(p.m2) + '-' + pad(p.d2);
}
function longLabel(name){
  const p = parseName(name);
  if (!p) return name;
  if (p.kind === 'day') return name + ' (' + dayOf(p) + ')';
  return p.y + '-' + pad(p.m) + '-' + pad(p.d) + ' ~ ' + pad(p.m2) + '-' + pad(p.d2);
}
function titleOf(path, fallback){
  if (!path) return fallback || 'work-report';
  const parts = path.split('/');
  const last = parts[parts.length - 1];
  if (parts[0] === 'custom') return (CUSTOM_NAME[last] || last) + ' · 내 양식';
  const name = last.replace(/\.(md|log)$/i, '');
  const area = AREA_NAME[parts[0]];
  return area ? longLabel(name) + ' · ' + area : name;
}

/* ============================================================ 문서 사이의 길
   요약에서 빠진 근거는 같은 날짜의 다른 문서에 있다. 왼쪽 트리를 다시
   헤치지 않고 건너갈 수 있어야 한다. */
function buildIndex(groups){
  const byArea = {}, byName = {};
  for (const g of groups) {
    const list = [];
    for (const mo of g.months) for (const it of mo.items) list.push(it);
    list.sort((a, b) => a.name < b.name ? 1 : (a.name > b.name ? -1 : 0));
    byArea[g.area] = list;
    for (const it of list) (byName[it.name] = byName[it.name] || {})[g.area] = it.path;
  }
  INDEX = { byArea: byArea, byName: byName };
}
function nameOf(path){ return (path.split('/').pop() || '').replace(/\.md$/i, ''); }
function siblingsOf(path){
  const area = areaOf(path), nm = nameOf(path);
  // 주간 보고에는 같은 이름의 수집 원본이 없다. 주간은 원본 대화가 아니라 그
  // 구간의 한 일 목록을 읽어서 쓰기 때문이다. 근거도 거기에 있다.
  const span = nm.split('_');
  if (area === 'weekly' && span.length === 2 && parseName(span[0]) && parseName(span[1])) {
    const days = (INDEX.byArea.log || [])
      .filter(it => it.name >= span[0] && it.name <= span[1])
      .sort((a, b) => a.name < b.name ? -1 : 1)
      .map(it => ({ area:'log', path:it.path, label: shortLabel(it.name) }));
    return { lead:'이 구간의 한 일 목록', items: days };
  }
  const row = INDEX.byName[nm] || {}, out = [];
  for (const a of ['daily', 'weekly', 'log', 'raw'])
    if (a !== area && row[a]) out.push({ area:a, path:row[a], label: AREA_NAME[a] });
  return { lead:'같은 날짜', items: out };
}
function neighborsOf(path){
  const list = INDEX.byArea[areaOf(path)] || [];
  let i = -1;
  for (let n = 0; n < list.length; n++) if (list[n].path === path) { i = n; break; }
  if (i < 0) return { prev:null, next:null };
  return { prev: list[i + 1] || null, next: list[i - 1] || null };   // 목록은 최신이 위다
}

/* ============================================================ 화면 상태
   어떤 단추가 보이는지는 여기 한 곳에서만 정한다. 화면마다 따로 숨기면
   읽을 수 없는 문서에 제출 단추가 남는 식으로 어긋난다. */
function setState(t, kind){
  const e = $('state');
  e.textContent = t || '';
  e.className = 'state' + (kind ? ' ' + kind : '');
}
function flash(t, kind){ setState(t, kind || 'good'); setTimeout(() => { if ($('state').textContent === t) setState(''); }, 2600); }
function setHead(title, sub){
  $('docTitle').textContent = title;
  $('docSub').textContent = sub || '';
  document.title = title;
}
function canCells(){
  return view === 'doc' && !editing && !!current
         && /\.md$/i.test(current) && areaOf(current) !== 'raw';
}
function renderPreview(){ $('view').innerHTML = render(raw, canCells()); }
function renderLive(){ $('liveView').innerHTML = render($('src').value, false); }

function markCurrent(){
  document.querySelectorAll('.doclink').forEach(a =>
    a.classList.toggle('on', view === 'doc' && a.dataset.path === current));
}
function drawRel(){
  const bar = $('rel');
  bar.textContent = '';
  const rel = (view === 'doc' && current && !editing) ? siblingsOf(current) : { lead:'', items:[] };
  if (!rel.items.length) { bar.hidden = true; return; }
  bar.appendChild(el('span', '', rel.lead));
  for (const s of rel.items) {
    const a = el('a', s.area === 'log' ? 'num' : '', s.label);
    a.href = '#';
    a.onclick = e => { e.preventDefault(); openReport(s.path); };
    bar.appendChild(a);
  }
  bar.hidden = false;
}
function layout(){
  const isDoc = view === 'doc', isReadme = view === 'readme', isSetup = view === 'setup';
  const area = areaOf(current);
  const writable = isDoc && !!current && /\.md$/i.test(current) && area !== 'raw';
  // 시작하기도 읽는 화면이라 같은 종이 위에 올린다
  const showPaper = (isDoc || isReadme || isSetup) && !editing;
  const live = $('editor').classList.contains('showlive');

  $('paper').hidden = !showPaper;
  $('view').hidden = !showPaper || (isDoc && mode === 'raw');
  $('rawView').hidden = !(showPaper && isDoc && mode === 'raw');
  $('editor').hidden = !(isDoc && editing);
  $('confView').hidden = view !== 'config';
  $('sheet').classList.toggle('editing', isDoc && editing);

  // 고치는 중에도 결과를 볼 수 있어야 한다. 넓은 화면은 좌우로 나누고,
  // 좁은 화면에서는 이 고르개가 두 쪽을 번갈아 보여 준다
  $('seg').hidden = !(isDoc && (!editing || narrow()));
  $('tabPreview').classList.toggle('on', editing ? live : mode === 'preview');
  $('tabRaw').classList.toggle('on', editing ? !live : mode === 'raw');

  $('edit').hidden = !(writable && !editing);
  $('cancel').hidden = !(isDoc && (editing || dirty));
  $('cancel').textContent = dirty ? '되돌리기' : '보기';
  $('save').hidden = !(isDoc && (editing || dirty));
  $('confSave').hidden = view !== 'config';
  // 제출은 보고서가 하는 일이다. 한 일 목록과 수집 원본은 근거라서 내지 않는다
  $('submit').hidden = !(SUBMIT_URL && isDoc && !editing && (area === 'daily' || area === 'weekly'));
  $('copy').hidden = !(isDoc && !editing && !!current);
  if (isDoc && current) $('copy').textContent = toCopy().part ? '제출문 복사' : '복사';
  // 일일보고일 때만. PMS 일일보고는 하루 한 건이고 주간 보고에는 대응하는 칸이 없다
  const pmsReady = conf && conf.pms ? !!conf.pms.url : PMS_ON;
  $('pmsFill').hidden = !(isDoc && !editing && area === 'daily' && pmsReady);

  const nb = (isDoc && current) ? neighborsOf(current) : { prev:null, next:null };
  $('flip').hidden = !(isDoc && !editing && (nb.prev || nb.next));
  $('prev').disabled = !nb.prev;
  $('next').disabled = !nb.next;
  $('prev').title = nb.prev ? '이전 - ' + longLabel(nb.prev.name) + '  ([)' : '이전  ([)';
  $('next').title = nb.next ? '다음 - ' + longLabel(nb.next.name) + '  (])' : '다음  (])';

  $('tabConfig').classList.toggle('on', view === 'config');
  $('tabReadme').classList.toggle('on', view === 'readme');
  $('tabSetup').classList.toggle('on', view === 'setup');
  markCurrent();
  drawRel();
}
function paint(){
  if (view === 'setup') { setHead('시작하기', '지금 무엇이 되어 있는지'); drawSetup(); }
  else if (view === 'readme') { setHead('사용 설명', 'GUIDE.md'); $('view').innerHTML = render(readme || '', false); }
  else if (view === 'config') { setHead('설정', 'config.json'); }
  else {
    setHead(titleOf(current), current + (areaOf(current) === 'raw' ? '   읽기 전용' : ''));
    if (mode === 'preview') renderPreview(); else $('rawView').textContent = raw;
  }
  layout();
}

/* ============================================================ 왼쪽 목록 */
function docLink(it, area, withTag){
  const a = el('a', 'doclink');
  a.href = '#';
  a.dataset.path = it.path;
  a.dataset.find = (it.name + ' ' + (AREA_NAME[area] || '')).toLowerCase();
  const t = el('span', 'num', withTag ? longLabel(it.name) : shortLabel(it.name));
  t.style.overflow = 'hidden';
  t.style.textOverflow = 'ellipsis';
  a.appendChild(t);
  if (withTag) a.appendChild(el('span', 'chip', AREA_TAG[area] || area));
  a.onclick = e => { e.preventDefault(); openReport(it.path); };
  return a;
}

async function loadFiles(){
  const d = await (await fetch(api('files'))).json();
  buildIndex(d.groups || []);
  drawCustom($('customBox'), d.custom || []);
  const box = $('files');
  const opened = new Set([...box.querySelectorAll('.month:not(.closed)')].map(e => e.dataset.key));
  const shut = new Set([...box.querySelectorAll('.group.closed')].map(e => e.dataset.area));
  const first = !box.querySelector('.group');   // 처음 그리는가, 다시 그리는가
  box.textContent = '';

  if (!d.groups.length) {
    box.appendChild(el('div', 'empty', '아직 보고서가 없습니다.'));
    return;
  }

  // 어제 쓴 보고서를 다시 여는 일이 가장 잦다. 접힌 목록을 헤치지 않게 위에 둔다
  const recent = [];
  for (const g of d.groups) {
    if (g.area !== 'daily' && g.area !== 'weekly') continue;
    for (const mo of g.months) for (const it of mo.items) recent.push({ it: it, area: g.area });
  }
  recent.sort((a, b) => a.it.name < b.it.name ? 1 : -1);
  if (recent.length) {
    const wrap = el('div', 'recent');
    wrap.id = 'recentBox';
    wrap.appendChild(el('div', 'sectitle', '최근'));
    recent.slice(0, 5).forEach(r => wrap.appendChild(docLink(r.it, r.area, true)));
    box.appendChild(wrap);
  }

  const all = el('div', 'sectitle', '전체');
  all.id = 'allHead';
  box.appendChild(all);

  for (const g of d.groups) {
    const area = el('div', 'group');
    area.dataset.area = g.area;
    if (first ? FOLDED.includes(g.area) : shut.has(g.area)) area.classList.add('closed');
    const total = g.months.reduce((n, mo) => n + mo.items.length, 0);

    const head = el('button', 'head');
    head.appendChild(caret());
    head.appendChild(document.createTextNode(g.label));
    head.appendChild(el('span', 'count', String(total)));
    head.onclick = () => area.classList.toggle('closed');
    area.appendChild(head);

    const abox = el('div', 'items');
    g.months.forEach((mo, mi) => {
      const key = g.area + '/' + mo.month;
      const month = el('div', 'month');
      month.dataset.key = key;
      // 처음에는 가장 최근 달만 펼친다. 쌓여도 목록이 길어지지 않는다.
      // 다시 그릴 때는 지금 펼쳐 둔 것을 그대로 둔다 - 전부 접어 두었다고 해서
      // 보고서를 만들 때마다 최근 달이 도로 열리면 접어 둔 뜻이 없다.
      if (!(first ? mi === 0 : opened.has(key))) month.classList.add('closed');
      const mhead = el('button', 'mhead');
      mhead.appendChild(caret());
      mhead.appendChild(document.createTextNode(mo.month));
      mhead.onclick = () => month.classList.toggle('closed');
      month.appendChild(mhead);
      const list = el('div', 'items');
      for (const it of mo.items) list.appendChild(docLink(it, g.area, false));
      month.appendChild(list);
      abox.appendChild(month);
    });
    area.appendChild(abox);
    box.appendChild(area);
  }
  applyFilter();
  markCurrent();
}
function caret(){
  const c = el('span', 'caret');
  c.innerHTML = '<svg width="9" height="9" viewBox="0 0 10 10" fill="currentColor"><path d="M1 3h8L5 8z"/></svg>';
  return c;
}

// 문서가 여든 개를 넘으면 트리를 훑는 것보다 날짜 몇 자를 치는 쪽이 빠르다
let preFilter = null;
function applyFilter(){
  const q = $('q').value.trim().toLowerCase();
  $('clearq').hidden = !q;
  if (q && !preFilter) {
    preFilter = new Set();
    document.querySelectorAll('#files .group.closed').forEach(e => preFilter.add(e.dataset.area));
    document.querySelectorAll('#files .month.closed').forEach(e => preFilter.add(e.dataset.key));
  }
  const rec = $('recentBox'), allHead = $('allHead');
  if (rec) rec.hidden = !!q;
  if (allHead) allHead.hidden = !!q;
  document.querySelectorAll('#files .doclink').forEach(a => {
    a.hidden = !!q && (a.dataset.find || '').indexOf(q) < 0;
  });
  document.querySelectorAll('#files .month').forEach(m => {
    const any = [...m.querySelectorAll('.doclink')].some(a => !a.hidden);
    m.hidden = !any;
    if (q) m.classList.remove('closed');
  });
  document.querySelectorAll('#files .group').forEach(g => {
    const any = [...g.querySelectorAll('.doclink')].some(a => !a.hidden);
    g.hidden = !any;
    if (q) g.classList.remove('closed');
  });
  if (!q && preFilter) {
    document.querySelectorAll('#files .group').forEach(g => g.classList.toggle('closed', preFilter.has(g.dataset.area)));
    document.querySelectorAll('#files .month').forEach(m => m.classList.toggle('closed', preFilter.has(m.dataset.key)));
    preFilter = null;
  }
}

// 내 양식. 보고서와 같은 .md라 뷰어가 그대로 열고 저장한다.
// 쓸지 말지는 설정이 정하므로 여기서는 지금 무엇을 쓰는지만 보인다.
function drawCustom(box, items){
  CUSTOM_STATE = items;
  box.textContent = '';
  if (!items.length) return;
  box.appendChild(el('div', 'sectitle custom', '내 양식'));
  for (const it of items) {
    const a = el('a', 'doclink');
    a.href = '#';
    a.style.paddingLeft = 'var(--sp-3)';
    a.appendChild(el('span', '', it.name));
    if (it.mine && it.exists) {
      a.dataset.path = it.path;
      a.onclick = e => { e.preventDefault(); openReport(it.path); };
    } else {
      a.classList.add('none');
      a.appendChild(el('span', 'chip', it.mine ? '다음 실행에 생김' : '기본값'));
      a.onclick = e => { e.preventDefault(); openConfig(); };
    }
    box.appendChild(a);
  }
}

/* ============================================================ 문서 열기 */
function canLeave(){
  if (dirty) {
    if (!confirm('저장하지 않은 수정이 있습니다. 그래도 넘어갈까요?')) return false;
    dirty = false;
  }
  if (confDirty) {
    if (!confirm('저장하지 않은 설정이 있습니다. 그래도 넘어갈까요?')) return false;
    confDirty = false;
  }
  return true;
}
async function openReport(path){
  if (!canLeave()) return;
  const r = await fetch(api('report', path ? { path } : {}));
  if (!r.ok) { setState('열지 못했습니다', 'warn'); return; }
  const d = await r.json();
  raw = d.text; orig = d.text; current = d.path;
  dirty = false; editing = false; cellEditing = null; view = 'doc';
  $('src').value = raw;
  $('rawView').textContent = raw;
  $('editor').classList.remove('showlive');
  // 실행 기록처럼 마크다운이 아닌 것은 꾸미지 않는다
  if (current && !/\.md$/i.test(current)) mode = 'raw';
  setState('');
  paint();
  $('sheet').scrollTop = 0;
  if (narrow() && window.innerWidth <= 860) hideSide();
}
function setRaw(text, mark){
  raw = text;
  $('src').value = text;
  $('rawView').textContent = text;
  if (mark) { dirty = true; setState('수정 중', 'warn'); }
}

/* ============================================================ 표 칸 고치기
   보고서에서 손대는 곳은 대개 수행 업무 표의 칸 하나다. 그걸 위해 원문
   전체를 열면 파이프 기호 사이에서 그 칸을 찾아야 한다. 두 번 눌러 그
   자리에서 고치고, 바꾼 값은 그 줄만 다시 쓴다. */
function startCell(td){
  if (cellEditing) return;
  const ln = +td.dataset.ln, c = +td.dataset.c;
  const parts = splitRow(raw.split(/\r?\n/)[ln] || '');
  if (parts[c] === undefined) return;
  cellEditing = td;
  td.textContent = parts[c].trim();
  td.classList.add('on');
  td.contentEditable = 'true';
  td.focus();
  const r = document.createRange();
  r.selectNodeContents(td);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(r);
  setState('칸을 고치는 중 - Enter 끝냄, Tab 다음 칸, Esc 취소', 'warn');
}
function closeCell(){
  const td = cellEditing;
  cellEditing = null;
  if (td) { td.contentEditable = 'false'; td.classList.remove('on'); }
  return td;
}
function commitCell(step){
  const td = cellEditing;
  if (!td) return;
  const ln = +td.dataset.ln, c = +td.dataset.c;
  // 칸 안의 세로줄은 표를 쪼개므로 받지 않는다. 줄바꿈도 한 칸은 한 줄이다
  const value = td.textContent.replace(/\s+/g, ' ').replace(/\|/g, '/').trim();
  closeCell();
  const lines = raw.split(/\r?\n/);
  const parts = splitRow(lines[ln] || '');
  if (parts[c] !== undefined && parts[c].trim() !== value) {
    parts[c] = ' ' + value + ' ';
    lines[ln] = '|' + parts.join('|') + '|';
    setRaw(lines.join('\n'), true);
  } else {
    setState(dirty ? '수정 중' : '', dirty ? 'warn' : '');
  }
  renderPreview();
  layout();
  if (step) {
    const nxt = $('view').querySelector('td[data-ln="' + ln + '"][data-c="' + (c + step) + '"]');
    if (nxt) startCell(nxt);
  }
}
function cancelCell(){
  if (!cellEditing) return;
  closeCell();
  setState(dirty ? '수정 중' : '', dirty ? 'warn' : '');
  renderPreview();
}

/* ============================================================ 만들기 */
// PMS 작업은 결말이 여섯 가지다. 무엇을 해야 하는지가 상태마다 달라서
// "실패" 한 마디로는 사람이 다음 행동을 알 수 없다.
const PMS_TAIL = { running:' - 폼을 채우는 중', done:' 채웠습니다', opened:' - 폼만 열었습니다',
                   login:' - 로그인이 필요합니다', config:' - 설정이 비었습니다',
                   playwright:' - 준비물이 없습니다', env:' - 브라우저를 열 수 없습니다',
                   failed:' 실패' };

function pmsCard(j){
  const label = j.mode === 'pms-login' ? 'PMS 로그인' : 'PMS 폼 채우기';
  let box = $('job-' + j.id);
  if (!box) { box = el('div'); box.id = 'job-' + j.id; $('jobs').appendChild(box); }
  box.className = 'job ' + (j.state === 'running' ? 'running' : j.state === 'done' ? 'done'
                          : j.state === 'opened' ? 'skipped' : 'failed');
  box.textContent = '';

  const row = el('div', 'row');
  if (j.state !== 'running') row.appendChild(el('span', 'dot'));
  row.appendChild(el('span', 'what', label + (PMS_TAIL[j.state] || ' 실패')));
  row.appendChild(el('span', 'time', j.seconds + '초'));
  if (j.state !== 'running') {
    const x = el('button', 'x');
    x.title = '닫기';
    x.innerHTML = '<svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M5 5l10 10M15 5L5 15"/></svg>';
    x.onclick = async () => {
      box.remove();
      try { await fetch(api('dismiss', { id: j.id }), { method:'POST' }); } catch (e) {}
    };
    row.appendChild(x);
  }
  box.appendChild(row);

  if (j.state === 'running') {
    const bar = el('div', 'bar');
    bar.appendChild(el('i'));
    box.appendChild(bar);
    return;
  }
  // 스크립트가 한 말을 그대로 보여 준다. 안내가 로그에만 남으면 아무도 읽지 않는다
  const note = el('div', 'note');
  for (const line of (j.note || [])) note.appendChild(el('div', '', line));
  if (!(j.note || []).length) note.appendChild(document.createTextNode('runlog의 pms.log에 기록이 남습니다'));
  box.appendChild(note);

  // 바로 고칠 수 있는 것은 단추로 둔다
  if (j.state === 'done' && j.mode === 'pms') {
    // 창은 뒤에서 열린다. 하던 일을 끊지 않으려고 그렇게 했으니, 볼 길을 준다
    const b = el('button', 'quiet', 'PMS 창 보기');
    b.onclick = () => pmsRun('show');
    box.appendChild(b);
  } else if (j.state === 'login') {
    const b = el('button', 'quiet', '로그인 창 열기');
    b.onclick = async () => {
      await fetch(api('pms', { mode:'login' }), { method:'POST' });
      if (!polling) { polling = true; pollJobs(); }
    };
    box.appendChild(b);
  } else if (j.state === 'config') {
    const b = el('button', 'quiet', '설정 열기');
    b.onclick = () => show('config');
    box.appendChild(b);
  }
}

function jobCard(j){
  if (j.mode === 'pms' || j.mode === 'pms-login') return pmsCard(j);
  const label = (j.mode === 'daily' ? '일일보고' : '주간보고') + (j.external ? ' (예약 실행)' : '');
  let box = $('job-' + j.id);
  if (!box) { box = el('div'); box.id = 'job-' + j.id; $('jobs').appendChild(box); }
  box.className = 'job ' + j.state;
  box.textContent = '';

  const row = el('div', 'row');
  if (j.state !== 'running') row.appendChild(el('span', 'dot'));
  const tail = j.state === 'running' ? (j.phase === 'write' ? ' - 보고서 쓰는 중' : ' - 기록 모으는 중')
             : j.state === 'done' ? ' 완료'
             : j.state === 'skipped' ? ' 건너뜀' : ' 실패';
  row.appendChild(el('span', 'what', label + tail));
  row.appendChild(el('span', 'time', j.seconds + '초'));
  if (j.state !== 'running') {
    const x = el('button', 'x');
    x.title = '닫기';
    x.innerHTML = '<svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M5 5l10 10M15 5L5 15"/></svg>';
    x.onclick = async () => {
      box.remove();
      // 서버가 작업을 계속 들고 있으면 다음 폴링이 카드를 다시 만든다
      try { await fetch(api('dismiss', { id: j.id }), { method:'POST' }); } catch (e) {}
    };
    row.appendChild(x);
  }
  box.appendChild(row);

  if (j.state === 'running') {
    const bar = el('div', 'bar');
    bar.appendChild(el('i'));
    box.appendChild(bar);
  } else if (j.state === 'done') {
    const note = el('div', 'note');
    const paths = j.paths && j.paths.length ? j.paths : (j.path ? [j.path] : []);
    for (const p of paths) {
      const a = el('a', '', titleOf(p));
      a.href = '#';
      a.onclick = e => { e.preventDefault(); loadFiles().then(() => openReport(p)); };
      note.appendChild(a);
    }
    if (!paths.length) note.appendChild(document.createTextNode('새로 쓰인 보고서가 없습니다'));
    box.appendChild(note);
  } else if (j.state === 'skipped') {
    box.appendChild(el('div', 'note', '다른 실행이 진행 중이어서 물러났습니다'));
  } else {
    box.appendChild(el('div', 'note', '보고 폴더의 runlog에 이유가 남습니다'));
  }
}

let polling = false;
async function pollJobs(){
  let list;
  try { list = await (await fetch(api('jobs'))).json(); }
  catch (e) { polling = false; return; }
  const alive = new Set(list.map(j => 'job-' + j.id));
  // 서버가 더 들고 있지 않은 카드는 치운다 (예약 실행이 끝난 경우)
  [...$('jobs').children].forEach(c => { if (!alive.has(c.id)) c.remove(); });
  let running = false;
  for (const j of list) { jobCard(j); if (j.state === 'running') running = true; }
  if (running) { polling = true; setTimeout(pollJobs, 1500); }
  else { polling = false; loadFiles(); }
}
async function run(kind){
  await fetch(api('run', { mode: kind }), { method: 'POST' });
  if (!polling) { polling = true; pollJobs(); }
}

/* ============================================================ 설정
   화면에서 고치는 항목만 둔다. 설치가 채우는 경로 값은 여기에 없다. */
const WEEKDAYS = [
  { v:'Monday', t:'월요일' }, { v:'Tuesday', t:'화요일' }, { v:'Wednesday', t:'수요일' },
  { v:'Thursday', t:'목요일' }, { v:'Friday', t:'금요일' },
  { v:'Saturday', t:'토요일' }, { v:'Sunday', t:'일요일' }
];
const FIELDS = [
  { h:'보고서' },
  { k:'author',       t:'text',   label:'작성자', size:'short' },
  { k:'agent',        t:'select', label:'실행 CLI', opts:['claude', 'codex'] },
  { k:'submit_url',   t:'text',   label:'제출 화면 주소', size:'long',
    hint:'비우면 제출 단추가 사라진다' },
  { k:'submit_label', t:'text',   label:'제출 단추 문구', size:'mid' },
  { k:'notify',       t:'bool', def:true, label:'알림 사용' },

  { h:'내 양식',
    note:'켜는 순간 기본값이 보고 폴더의 custom\\ 에 복사된다.\n끄면 기본값으로 돌아가고, 고쳐 둔 파일은 지워지지 않는다' },
  { k:'custom_format',  t:'bool', def:false, label:'내 보고서 양식 쓰기',
    file:'custom/report-format.md', hint:'보고서 양식을 통째로 바꾼다' },
  { k:'custom_rules',   t:'bool', def:false, label:'내 글쓰기 문체 쓰기',
    file:'custom/writing-rules.md', hint:'기본 원칙 뒤에 덧붙인다' },
  { k:'custom_samples', t:'bool', def:false, label:'내 보고서 따라하기',
    file:'custom/my-reports.md',
    hint:'붙여넣은 지난 보고서를 문체 예시로 쓴다.' +
         '\n보고서 맨 앞에 그 문체로 쓴 제출문 절이 생긴다.' +
         '\n붙여넣은 것이 없으면 아무 일도 하지 않는다' },

  { h:'수집' },
  { k:'mine_only', t:'bool', def:true, label:'내 이메일의 커밋만' },
  { k:'redact',    t:'bool', def:true, label:'키·토큰 가리기' },
  { k:'exclude_repos', t:'lines', label:'제외할 저장소',
    hint:'한 줄에 하나. 경로에 그 글자가 들어가면 제외된다.' +
         '\nex. my-project' +
         '\n폴더 이름만 적는 편이 확실하다. 저장소를 옮겨도 계속 걸린다.' +
         '\nex. /c/Users/me/project/my-project → 안 걸린다 (Git Bash 형식)' },
  { k:'exclude_paths', t:'lines', label:'작업으로 안 치는 경로',
    hint:'한 줄에 하나. 경로에 그 글자가 들어가면 뺀다.' +
         '\nex. node_modules' +
         '\nex. \\build\\  (구분자는 \\ 로 적는다)' },

  { h:'PMS 일일보고' },
  { k:'pms.url',      t:'str',  label:'PMS 주소',
    hint:'예: https://pms.example.com' + NL + '비우면 이 기능은 꺼진다' },
  { k:'pms.project',  t:'str',  label:'올릴 프로젝트',
    hint:'PMS 주소의 /projects/<여기> 부분' },
  { k:'pms.projects', t:'lines', label:'지난 보고서를 받을 프로젝트',
    hint:'한 줄에 하나. 비우면 위의 프로젝트만 본다' + NL + '프로젝트를 옮긴 적이 있으면 예전 것도 적는다' },
  { h:'실행' },
  { k:'backfill_days',    t:'num', label:'빠뜨린 날 채우기', hint:'며칠 전까지. 0이면 안 함' },
  { k:'retain_months',    t:'num', label:'수집본 보관(개월)',
    hint:'0이면 전부 보관. 보고서는 지우지 않음' },
  { k:'weekly.end_day',   t:'select', label:'주간 마지막 요일', opts:WEEKDAYS },
  { k:'weekly.span_days', t:'num', label:'주간 기간(일)' },

  { h:'실행 파일', note:'비워 두면 자동으로 찾는다. 흐린 글씨가 지금 찾아 둔 경로다' },
  { k:'claude_bin', t:'text', label:'claude 경로', size:'long', probe:'claude', hint:'찾는 중...' },
  { k:'codex_bin',  t:'text', label:'codex 경로',  size:'long', probe:'codex',  hint:'찾는 중...' },
  { k:'python_bin', t:'text', label:'python 경로', size:'long', probe:'python', hint:'찾는 중...' },
];
let CUSTOM_STATE = [];

// 빈 칸이 "설정이 안 됐다"로 읽히지 않게, 지금 쓰는 경로를 흐린 글씨로 채운다.
// 값이 아니라 안내이므로 저장해도 덮어쓰지 않는다 - 비워 두면 계속 자동으로 찾는다
async function fillBins(){
  let found;
  try { found = await (await fetch(api('bins'))).json(); }
  catch (e) { found = {}; }
  for (const f of FIELDS) {
    if (!f.probe) continue;
    const input = $('f_' + f.k.replace('.', '_'));
    if (!input) continue;
    const got = found[f.probe];
    input.placeholder = got ? got.path : '찾지 못했습니다';
    const row = input.closest('.field');
    const hint = row && row.querySelector('.hint');
    if (!hint) continue;
    hint.textContent = got ? '자동으로 찾았습니다 - ' + got.version
                           : '자동으로 찾지 못했습니다. 전체 경로를 적어 주세요';
  }
}

function drawConfig(){
  const box = $('confView');
  box.textContent = '';
  box.appendChild(el('div', 'lede', '바꾼 값은 다음 실행부터 적용됩니다.'));

  let card = null;
  for (const f of FIELDS) {
    if (f.h) {
      card = el('div', 'card');
      card.appendChild(el('h3', '', f.h));
      if (f.note) {
        const n = el('div', 'hint', f.note);
        n.style.gridColumn = 'auto';
        n.style.margin = '0 0 ' + '4px';
        card.appendChild(n);
      }
      box.appendChild(card);
      continue;
    }
    if (!card) { card = el('div', 'card'); box.appendChild(card); }

    const row = el('div', 'field');
    const lab = el('label', '', f.label);
    row.appendChild(lab);

    let input;
    const val = dig(conf, f.k);
    if (f.t === 'bool') {
      input = el('input');
      input.type = 'checkbox';
      input.className = 'switch';
      // 값이 없으면 항목이 정한 기본값이다. 전부 켜짐으로 그리면 기본이
      // 거짓인 설정이 저장하는 순간 켜져 버린다.
      input.checked = (val === undefined || val === null) ? !!f.def : !!val;
    } else if (f.t === 'num') {
      input = el('input');
      input.type = 'number';
      input.value = val == null ? '' : val;
    } else if (f.t === 'select') {
      input = el('select');
      for (const o of f.opts) {
        const op = el('option');
        op.value = (o && o.v !== undefined) ? o.v : o;
        op.textContent = (o && o.t !== undefined) ? o.t : o;
        input.appendChild(op);
      }
      const first = f.opts[0];
      input.value = val || ((first && first.v !== undefined) ? first.v : first);
    } else if (f.t === 'lines') {
      input = el('textarea');
      input.className = 'lines';
      input.value = (val || []).join('\n');
    } else {
      input = el('input');
      input.type = 'text';
      input.className = f.size || 'mid';
      input.value = val == null ? '' : val;
    }
    input.dataset.key = f.k;
    input.dataset.type = f.t;
    input.id = 'f_' + f.k.replace('.', '_');
    lab.htmlFor = input.id;

    // 켜고 끄는 곳과 고치러 가는 곳이 갈라져 있으면 한 가지 일이 두 화면에
    // 나뉜다. 켜져 있고 파일이 있으면 그 자리에서 열 수 있게 한다.
    if (f.file) {
      const withOpen = el('div', 'with');
      withOpen.appendChild(input);
      const state = CUSTOM_STATE.filter(c => c.path === f.file)[0];
      if (state && state.exists && input.checked) {
        const a = el('a', 'open', '열어서 고치기');
        a.href = '#';
        a.onclick = e => { e.preventDefault(); openReport(f.file); };
        withOpen.appendChild(a);
      }
      row.appendChild(withOpen);
    } else {
      row.appendChild(input);
    }

    if (f.hint) row.appendChild(el('div', 'hint', f.hint));
    card.appendChild(row);
  }

  // 저장하지 않고 떠나면 조용히 사라지던 것을 막는다
  box.oninput = () => { if (!confDirty) { confDirty = true; setState('수정 중', 'warn'); } };
  box.onchange = box.oninput;

  $('confSave').onclick = async () => {
    const out = { weekly: {} };
    box.querySelectorAll('[data-key]').forEach(input => {
      const k = input.dataset.key, t = input.dataset.type;
      let v;
      if (t === 'bool') v = input.checked;
      else if (t === 'num') v = input.value === '' ? 0 : Number(input.value);
      else if (t === 'lines') v = input.value.split(/\r?\n/);
      else v = input.value;
      // 점이 있는 키는 그 앞을 묶음 이름으로 본다. weekly 만 특별히 다루면
      // 묶음을 더할 때마다 이 줄을 또 고쳐야 한다.
      const dot = k.indexOf('.');
      if (dot > 0) { const g = k.slice(0, dot); (out[g] = out[g] || {})[k.slice(dot + 1)] = v; }
      else out[k] = v;
    });
    const r = await fetch(api('config'), { method:'POST',
      headers:{ 'Content-Type':'application/json' }, body: JSON.stringify(out) });
    if (!r.ok) { setState('저장하지 못했습니다', 'warn'); return; }
    conf = await r.json();
    confDirty = false;
    // 양식 토글을 바꿨으면 왼쪽 목록이 바로 따라와야 한다. 켜면 그 자리에서
    // 파일이 생기므로 새로 고치지 않아도 열 수 있다.
    await loadFiles();
    drawConfig();
    fillBins();
    flash('저장했습니다');
  };
}

/* ============================================================ 복사와 제출
   제출문 절이 있으면 그것만, 없으면 보고서 전체를 준다. 무엇을 담았는지는
   단추 이름과 눌렀을 때의 말로 알린다 - 조용히 일부만 복사되면 붙여넣고
   나서야 안다. */
function toCopy(){
  const lines = (editing ? $('src').value : raw).split(/\r?\n/);
  const isHead = s => /^##\s/.test(s);
  let start = -1;
  for (let i = 0; i < lines.length; i++) {
    if (isHead(lines[i]) && lines[i].indexOf(SUBMIT_HEAD) >= 0) { start = i + 1; break; }
  }
  if (start < 0) return { body: lines.join('\n'), part: false };
  let end = lines.length;
  for (let i = start; i < lines.length; i++) { if (isHead(lines[i])) { end = i; break; } }
  const body = lines.slice(start, end).join('\n').trim();
  return body ? { body: body, part: true } : { body: lines.join('\n'), part: false };
}

/* ============================================================ 화면 밝기 */
const THEME_ICON = {
  system:'<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10" cy="10" r="6.4"/><path d="M10 3.6a6.4 6.4 0 0 1 0 12.8z" fill="currentColor" stroke="none"/></svg>',
  light:'<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="10" cy="10" r="3.4"/><path d="M10 2.2v1.6M10 16.2v1.6M17.8 10h-1.6M3.8 10H2.2M15.5 4.5l-1.1 1.1M5.6 14.4l-1.1 1.1M15.5 15.5l-1.1-1.1M5.6 5.6 4.5 4.5"/></svg>',
  dark:'<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M15.8 12.6A6.3 6.3 0 0 1 7.4 4.2a6.5 6.5 0 1 0 8.4 8.4z"/></svg>'
};
const THEME_NAME = { system:'자동', light:'밝게', dark:'어둡게' };
function applyTheme(t){
  if (t === 'system') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', t);
  $('theme').innerHTML = THEME_ICON[t];
  $('theme').title = '화면 밝기: ' + THEME_NAME[t];
  $('theme').dataset.now = t;
  try { localStorage.setItem('wr-theme', t); } catch (e) {}
}

/* ============================================================ 왼쪽 레일 접기 */
function syncScrim(){
  const need = window.innerWidth <= 860 && !$('side').classList.contains('hide');
  let s = document.querySelector('.scrim');
  if (need && !s) {
    s = el('div', 'scrim');
    s.onclick = () => { $('side').classList.add('hide'); syncScrim(); };
    document.querySelector('.main').appendChild(s);
  }
  if (!need && s) s.remove();
}
function hideSide(){ $('side').classList.add('hide'); syncScrim(); }

/* ============================================================ 이어 붙이기 */
$('home').onclick = () => { mode = 'preview'; openReport(''); };
$('toggleSide').onclick = () => { $('side').classList.toggle('hide'); syncScrim(); };
$('theme').onclick = () => {
  const order = ['system', 'light', 'dark'];
  const now = $('theme').dataset.now || 'system';
  applyTheme(order[(order.indexOf(now) + 1) % order.length]);
};
$('tabPreview').onclick = () => {
  if (editing) { $('editor').classList.add('showlive'); renderLive(); }
  else mode = 'preview';
  paint();
};
$('tabRaw').onclick = () => {
  if (editing) $('editor').classList.remove('showlive');
  else mode = 'raw';
  paint();
};
$('tabReadme').onclick = async () => {
  if (!canLeave()) return;
  if (readme === null) readme = await (await fetch(api('readme'))).text();
  view = 'readme'; editing = false;
  paint();
  $('sheet').scrollTop = 0;
};
// 차례대로 넘기는 마법사가 아니라 상태판이다. 사람마다 도착 지점이 다르고,
// 이미 해 둔 것을 다시 묻는 화면은 길잡이가 아니라 방해다.
// 각 줄은 "무엇이 되는가 / 지금 어떤가 / 지금 할 수 있는 것" 셋으로만 쓴다.
function setupSteps(st){
  const p = st.pms || {}, h = p.harvest || {};
  const reports = (st.reports || {}).daily || 0;
  const seen = st.seen || [];
  return [
    { ok: reports > 0,
      title: '보고서 만들기',
      done: '일일보고 ' + reports + '건이 쌓여 있습니다',
      todo: '아직 보고서가 없습니다. 평일 저녁에 저절로 만들어지고, 지금 만들 수도 있습니다',
      act: reports > 0 ? null : { label:'지금 만들어 보기', run:() => run('daily') } },

    { ok: !!st.custom['report-format.md'] || !!st.custom['writing-rules.md'] || seen.indexOf('custom') >= 0,
      title: '내 양식과 문체',
      done: (st.custom['report-format.md'] || st.custom['writing-rules.md'])
            ? '내 것을 쓰고 있습니다 (왼쪽 아래 "내 양식"에서 고칩니다)'
            : '기본값을 쓰고 있습니다. 회사 양식이 다르거나 말투를 바꾸려면 설정에서 켭니다',
      todo: '회사 양식이 다르거나 말투를 바꾸려면 설정에서 켭니다. 켜지 않으면 기본값을 씁니다',
      act: { label:'설정 보기', run:() => { markSeen('custom'); openConfig(); } } },

    { ok: !!p.url,
      title: 'PMS에 자동으로 채우기',
      done: '보고서를 PMS 일일보고 폼에 채워 줍니다',
      todo: 'PMS 주소와 프로젝트를 적으면 보고서를 그 폼에 채워 줍니다',
      fields: [{ k:'pms.url', label:'PMS 주소', hint:'예: https://pms.cemware.com', value: p.url },
               { k:'pms.project', label:'프로젝트', hint:'예: sai — 주소의 /projects/<여기> 부분', value: p.project }] },

    { ok: !!p.has_token, need: !!p.url,
      title: 'PMS 토큰 (일감을 연결하려면)',
      done: '토큰이 들어 있습니다. 보고서를 쓸 때 열린 일감 목록을 받아 와 맞는 것만 연결합니다',
      todo: 'PMS의 "내 계정 > API 접근키"를 넣으면 그날 일과 맞는 일감을 연결해 줍니다. 없어도 나머지는 다 됩니다',
      fields: [{ k:'pms.token', label:'API 접근키', secret:true, saved: !!p.has_token,
                 hint:'PMS 오른쪽 위 "내 계정" 화면 아래쪽에 있습니다', value:'' }],
      optional: true },

    { ok: !!p.signed_in, need: !!p.url,
      title: 'PMS 로그인',
      done: '전용 창에 로그인되어 있습니다 (평소 쓰는 브라우저와 따로입니다)',
      todo: '전용 창에서 한 번만 로그인하면 됩니다. 비밀번호는 저장하지 않습니다',
      act: { label:'로그인 창 열기', run:() => pmsRun('login') } },

    { ok: h.count > 0, need: !!p.url,
      title: '내가 쓰던 보고서 가져오기',
      done: h.count + '건을 받아 두었습니다 (최근 ' + (h.latest || '-') + '). 그 문체와 분류를 따라 씁니다',
      todo: 'PMS에 올렸던 지난 보고서를 받아 오면 그 문체와 분류 습관대로 씁니다. 없으면 기본 문체로 씁니다',
      act: { label: h.count > 0 ? '다시 가져오기' : '최근 3달치 가져오기', run:() => pmsRun('fetch') } },

    { ok: !!st.has_submission, need: !!p.url,
      title: '제출해 보기',
      done: '가장 최근 일일보고에 제출문이 있습니다. 열어서 "PMS에 채우기"를 누르면 됩니다',
      todo: '제출문 절이 있는 보고서가 아직 없습니다. 다음 보고서부터 생깁니다',
      act: st.latest_daily ? { label:'그 보고서 열기', run:() => loadFiles().then(() => openReport(st.latest_daily)) } : null }
  ];
}

async function pmsRun(mode){
  await fetch(api('pms', { mode: mode }), { method:'POST' });
  flash(mode === 'login' ? '로그인 창을 엽니다'
      : mode === 'show' ? 'PMS 창을 가져옵니다' : '지난 보고서를 받아 오는 중입니다');
  if (!polling) { polling = true; pollJobs(); }
}

async function markSeen(what){
  try { await fetch(api('seen', { what: what }), { method:'POST' }); } catch (e) {}
}

// 단계 안에서 바로 고칠 수 있게 한다. "설정 열기"만 두면 화면을 옮겨 다니다
// 어디까지 했는지 잃는다.
function stepFields(st, body){
  const inputs = [];
  for (const f of st.fields) {
    const row = el('div', 'sfield');
    row.appendChild(el('label', '', f.label + (f.saved ? '  ✓ 들어 있음' : '')));
    const i = document.createElement('input');
    i.type = f.secret ? 'password' : 'text';
    i.value = f.value || '';
    if (f.secret) {
      i.autocomplete = 'off';
      // 값을 돌려받지 않으므로 칸은 비어 있다. 비어 있는 칸은 "안 넣었다"로
      // 읽히므로, 들어 있다는 사실은 칸 안에 적어 둔다.
      i.placeholder = f.saved ? '저장되어 있습니다 - 바꾸려면 새 값을 붙여넣으세요' : '붙여넣기';
      if (f.saved) row.classList.add('filled');
    }
    // 안내는 칸 아래 한 줄로만 둔다. placeholder 와 겹쳐 적으면 두 번 읽힌다
    row.appendChild(i);
    if (f.hint) row.appendChild(el('div', 'shint', f.hint));
    body.appendChild(row);
    inputs.push({ k: f.k, el: i, secret: !!f.secret });
  }
  const b = el('button', 'quiet', '저장');
  b.onclick = async () => {
    const out = {};
    for (const i of inputs) {
      const v = i.el.value.trim();
      if (i.secret && !v) continue;          // 빈 칸은 "그대로 둬라"는 뜻이다
      const dot = i.k.indexOf('.');
      const g = i.k.slice(0, dot);
      (out[g] = out[g] || {})[i.k.slice(dot + 1)] = v;
    }
    const r = await fetch(api('config'), { method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(out) });
    if (!r.ok) { setState('저장하지 못했습니다', true); return; }
    conf = await r.json();
    flash('저장했습니다');
    await openSetup();                        // 상태를 다시 읽어 체크를 갱신한다
  };
  body.appendChild(b);
}

function drawSetup(){
  const host = $('view');
  host.textContent = '';
  const box = el('div', 'setup');          // #view 의 class 는 그대로 둔다
  host.appendChild(box);
  const steps = setupSteps(setup || { reports:{}, custom:{}, pms:{} });
  const left = steps.filter(s => !s.ok && !s.optional).length;
  box.appendChild(el('h1', 'setuphead',
    left ? '아직 ' + left + '가지가 남았습니다' : '다 되어 있습니다'));
  box.appendChild(el('div', 'setuplede',
    left ? '아래에서 바로 채울 수 있습니다' : '설정을 바꾸고 싶으면 아래에서 고칩니다'));

  for (const st of steps) {
    const row = el('div', 'step' + (st.ok ? ' ok' : '') + (st.need === false ? ' wait' : ''));
    const mark = el('span', 'mark', st.ok ? '✓' : (st.need === false ? '·' : '○'));
    row.appendChild(mark);
    const body = el('div', 'body');
    body.appendChild(el('div', 't', st.title + (st.optional && !st.ok ? ' (선택)' : '')));
    body.appendChild(el('div', 'd', st.ok ? st.done : st.todo));
    // 끝난 줄에서도 단추는 남긴다. 다시 가져오거나 설정을 다시 여는 일은
    // 처음 한 번으로 끝나지 않는다.
    if (st.need === false) body.appendChild(el('div', 'd', '위의 PMS 주소를 먼저 채우면 할 수 있습니다'));
    else if (st.fields) stepFields(st, body);
    else if (st.act) {
      const b = el('button', 'quiet', st.act.label);
      b.onclick = st.act.run;
      body.appendChild(b);
    }
    row.appendChild(body);
    box.appendChild(row);
  }
}

async function openSetup(){
  if (!canLeave()) return;
  setup = await (await fetch(api('setup'))).json();
  view = 'setup'; editing = false;
  paint();
  $('sheet').scrollTop = 0;
}

async function openConfig(){
  if (!canLeave()) return;
  if (conf === null) conf = await (await fetch(api('config'))).json();
  view = 'config'; editing = false;
  drawConfig();
  paint();
  $('sheet').scrollTop = 0;
  fillBins();
}
$('tabConfig').onclick = openConfig;
$('tabSetup').onclick = openSetup;
$('pmsFill').onclick = async () => {
  const r = await fetch(api('pms', { path: current }), { method:'POST' });
  if (!r.ok) { setState(await r.text(), true); return; }
  flash('PMS 폼을 채우는 중입니다');
  if (!polling) { polling = true; pollJobs(); }
};
$('runDaily').onclick = () => run('daily');
$('runWeekly').onclick = () => run('weekly');
$('prev').onclick = () => { const n = neighborsOf(current).prev; if (n) openReport(n.path); };
$('next').onclick = () => { const n = neighborsOf(current).next; if (n) openReport(n.path); };
$('q').oninput = applyFilter;
$('clearq').onclick = () => { $('q').value = ''; applyFilter(); $('q').focus(); };

$('edit').onclick = () => {
  if (cellEditing) commitCell(0);
  editing = true;
  $('editor').classList.toggle('showlive', false);
  paint();
  renderLive();
  setTimeout(() => $('src').focus(), 0);
};
$('cancel').onclick = () => {
  if (dirty && !confirm('고친 것을 버리고 저장된 내용으로 돌아갑니다. 계속할까요?')) return;
  const had = dirty;
  setRaw(orig, false);
  dirty = false; editing = false; cellEditing = null;
  setState('');
  paint();
  if (had) flash('되돌렸습니다');
};
$('save').onclick = async () => {
  if (cellEditing) commitCell(0);
  const body = editing ? $('src').value : raw;
  const r = await fetch(api('report', { path: current }),
                        { method:'POST', headers:{ 'Content-Type':'text/plain; charset=utf-8' }, body: body });
  if (!r.ok) { setState('저장하지 못했습니다', 'warn'); return; }
  raw = body; orig = body; dirty = false;
  $('rawView').textContent = raw;
  if (!editing) renderPreview();
  layout();
  flash('저장했습니다');
};
let liveTimer = null;
$('src').oninput = () => {
  raw = $('src').value;
  if (!dirty) { dirty = true; setState('수정 중', 'warn'); layout(); }
  clearTimeout(liveTimer);
  liveTimer = setTimeout(renderLive, 180);
};
$('copy').onclick = async () => {
  const c = toCopy();
  try { await navigator.clipboard.writeText(c.body); }
  catch (e) { setState('복사하지 못했습니다', 'warn'); return; }
  flash(c.part ? '제출문을 복사했습니다' : '클립보드에 복사했습니다');
};
$('submit').onclick = async () => {
  const c = toCopy();
  try { await navigator.clipboard.writeText(c.body); } catch (e) {}
  flash(c.part ? '제출문을 복사했습니다. 붙여넣으세요' : '복사했습니다. 붙여넣으세요');
  window.open(SUBMIT_URL, '_blank', 'noopener');
};

$('view').addEventListener('dblclick', e => {
  const td = e.target.closest ? e.target.closest('td[data-ln]') : null;
  if (td && canCells()) startCell(td);
});
$('view').addEventListener('keydown', e => {
  if (!cellEditing) return;
  if (e.key === 'Enter') { e.preventDefault(); commitCell(0); }
  else if (e.key === 'Tab') { e.preventDefault(); commitCell(e.shiftKey ? -1 : 1); }
  else if (e.key === 'Escape') { e.preventDefault(); cancelCell(); }
});
$('view').addEventListener('focusout', e => { if (cellEditing && e.target === cellEditing) commitCell(0); });

window.addEventListener('resize', () => { syncScrim(); layout(); });
window.addEventListener('beforeunload', e => {
  if (dirty || confDirty) { e.preventDefault(); e.returnValue = ''; }
});
document.addEventListener('keydown', e => {
  const t = e.target, tag = (t.tagName || '').toLowerCase();
  const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || t.isContentEditable;
  if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
    if (!$('save').hidden) { e.preventDefault(); $('save').click(); }
    else if (!$('confSave').hidden) { e.preventDefault(); $('confSave').click(); }
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === '\\') { e.preventDefault(); $('toggleSide').click(); return; }
  if (e.key === 'Escape') {
    if (cellEditing) { e.preventDefault(); cancelCell(); }
    else if (t === $('q') && $('q').value) { $('q').value = ''; applyFilter(); }
    else if (editing) { e.preventDefault(); $('cancel').click(); }
    return;
  }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === '/') { e.preventDefault(); $('q').focus(); $('q').select(); }
  else if (e.key === '[' && !$('flip').hidden && !$('prev').disabled) $('prev').click();
  else if (e.key === ']' && !$('flip').hidden && !$('next').disabled) $('next').click();
});

(async () => {
  let saved = 'system';
  try { saved = localStorage.getItem('wr-theme') || 'system'; } catch (e) {}
  applyTheme(saved);
  if (!HAS_README) $('tabReadme').hidden = true;
  if (window.innerWidth <= 860) $('side').classList.add('hide');
  const now = new Date();
  $('runDaily').title = '오늘(' + pad(now.getMonth() + 1) + '-' + pad(now.getDate()) + ')까지 모아서 다시 만든다';
  $('runWeekly').title = '이번 주간 구간을 다시 만든다';
  try {
    await loadFiles();
    await openReport(START);
    // 아직 아무것도 없는 사람에게는 문서 대신 길잡이를 먼저 보여 준다.
    // 쓰던 사람의 첫 화면은 그대로 둔다.
    if (!START && !current) await openSetup();
  } catch (e) {
    $('view').innerHTML = '<p>보고서를 읽지 못했습니다: ' + esc(String(e)) + '</p>';
  }
  // 새로 고치기 전에 시작한 작업과 예약 실행도 여기서 이어 받는다
  pollJobs();
})();
