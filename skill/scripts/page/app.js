const SUBMIT_URL = {{SUBMIT_URL}};      // 따옴표까지 서버가 JSON으로 붙인다
const HAS_README = {{HAS_README}};
// 알림을 눌러 들어오면 어떤 보고서를 열지 주소가 말해 준다
const START = new URLSearchParams(location.search).get('path') || '';
const NL = String.fromCharCode(10);     // 안내문 줄바꿈
// PMS 주소가 설정돼 있는지. 설정 화면을 아직 열지 않았으면 conf 는 비어 있으므로
// 띄울 때의 값을 박아 둔다. 설정을 저장하면 conf 가 채워져 그때부터는 그쪽이 맞다.
const PMS_ON = {{PMS_ON}};
const AREA_NAME = { daily:'일일 보고', weekly:'주간 보고', log:'한 일 목록', raw:'수집 원본' };
const AREA_TAG  = { daily:'일일', weekly:'주간', log:'한 일', raw:'원본' };
const CUSTOM_NAME = { 'report-format.md':'보고서 양식', 'writing-rules.md':'글쓰기 문체' };
// 내 양식 파일을 열었을 때 알려 줄 것. 양식 파일 안에 적으면 에이전트가 함께 읽어
// 토큰만 쓰고, 사람은 정작 그 설명을 볼 자리가 없다. 그래서 화면이 들고 있는다.
const CUSTOM_HELP = {
  'report-format.md': [
    '켜는 순간 이 사본은 그 시점에 멈춥니다. 기본 양식이 좋아져도 따라오지 않습니다.',
    '제출문의 분류 줄과 일감 줄 모양은 바꿀 수 없습니다 - PMS 폼이 정합니다.',
    '「묶는 방식과 말투」를 비워 두면 지난 제출문을 따릅니다. 적으면 그것이 앞섭니다.'
  ],
  'writing-rules.md': [
    '켜는 순간 이 사본은 그 시점에 멈춥니다.',
    '기본 원칙 뒤에 덧붙습니다. 제출문 절처럼 형식이 정해진 자리에는 적용되지 않습니다.'
  ]
};
// 제출문 절은 붙여넣기용이라 보고서 전체가 아니라 그 절만 클립보드에 담는다.
// 앞의 번호(`0. `)는 양식이 붙이는 것이라 떼고 본다. 화면에 원문으로 보여 주는
// 판단과 복사하는 판단이 같은 절을 가리켜야 하므로 한 곳에서만 정한다.
const SUBMIT_HEAD = '제출문';
const isSubmitHead = t => String(t).replace(/^[\d.\s]+/, '').trim() === SUBMIT_HEAD;
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
// 따옴표도 가린다. 아래 링크 치환이 결과를 href 속성 안에 넣으므로, 따옴표가
// 남으면 보고서 글이 속성을 하나 더 만들어 붙일 수 있다.
function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;')
                         .replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

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
    // http(s)만 링크로 만든다. javascript: 같은 주소는 글자 그대로 둔다 -
    // 보고서에는 PMS에서 받아온 남의 글도 들어오므로 쓸 수 있는 주소를 좁힌다.
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (all, text, url) =>
      /^https?:\/\//i.test(url)
        ? '<a href="' + url + '" target="_blank" rel="noopener">' + text + '</a>'
        : all);
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

    // 제출문 절은 붙여넣어 그대로 내는 글이다. 마크다운으로 해석하면 화면과
    // 올라갈 글이 달라진다 - 분류 줄과 주제 줄이 한 문단으로 붙고 "- "가
    // 목록 기호로 먹힌다. 그러면 사람이 화면을 보고 틀린 것을 믿는다.
    // 제목이 번호를 뺀 뒤 정확히 "제출문"인 절만 본다. 양식 파일의
    // "제출문 절에 무엇을 쓰나"처럼 설명하는 절까지 원문으로 두면 읽기 어렵다.
    const head = ln.match(/^(#{1,4})\s+(.*)$/);
    if (head && head[1].length <= 2 && isSubmitHead(head[2])) {
      closeAll();
      out.push('<h' + head[1].length + '>' + inline(head[2]) + '</h' + head[1].length + '>');
      const kept = [];
      i++;
      while (i < lines.length && !/^#{1,2}\s/.test(lines[i])) { kept.push(lines[i]); i++; }
      out.push('<pre class="submit">' + esc(kept.join('\n').replace(/^\s*\n|\s+$/g, '')) + '</pre>');
      continue;
    }

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
  // 업무보고에서 근거를 찾는 동작은 문서 종류에 따라 흔들리면 안 된다.
  // 주간 보고도 시작일을 기준으로 같은 세 종류의 문서를 보여 준다.
  const date = nm.match(/^\d{4}-\d{2}-\d{2}/);
  const row = date ? (INDEX.byName[date[0]] || {}) : {};
  const fixed = ['daily', 'log', 'raw'];
  return { lead:'같은 날짜', items: fixed.map(a => ({
    area:a, path:row[a] || null, label:AREA_NAME[a],
    selected:a === area || (area === 'weekly' && a === 'daily')
  })) };
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
    const a = el('a', 'relitem' + (s.selected ? ' selected' : '') + (!s.path ? ' missing' : ''), s.label);
    if (s.path) {
      a.href = '#';
      a.onclick = e => { e.preventDefault(); openReport(s.path); };
    } else {
      a.setAttribute('aria-disabled', 'true');
      a.title = '아직 만들어지지 않았습니다';
    }
    bar.appendChild(a);
  }
  bar.hidden = false;
}
// PMS를 쓰는데 일일보고에 제출문 절이 없으면 폼을 채울 것이 없다. 절을 찾는 단서는
// 절 제목이라, 양식에서 제목을 고치면 절이 있어도 없는 것으로 보인다. 검사기는
// "절이 없는 것은 잘못이 아니다"로 통과시키므로(만들지 않아도 되는 사람이 있다)
// 그 사실을 아는 곳은 화면뿐이다. 프롬프트로 보내지 않는다 - 사람이 고칠 일이다.
function drawNotice(){
  const box = $('notice');
  box.textContent = '';
  box.className = 'notice';
  box.hidden = true;
  if (view !== 'doc' || editing || !current) return;

  // 내 양식을 열었을 때: 이 파일이 무엇이고 무엇을 바꿀 수 없는지
  if (areaOf(current) === 'custom') {
    const help = CUSTOM_HELP[current.split('/').pop()];
    if (!help) return;
    box.className = 'notice info';
    for (const line of help) box.appendChild(el('div', '', line));
    box.hidden = false;
    return;
  }

  // 일일보고인데 제출문 절이 없을 때: PMS 채우기가 폼만 연다
  const pmsReady = conf && conf.pms ? !!conf.pms.url : PMS_ON;
  if (areaOf(current) === 'daily' && pmsReady && !toCopy().part) {
    box.appendChild(el('div', '',
      '이 보고서에는 제출문 절이 없습니다. PMS에 채우면 폼만 열립니다.'));
    box.appendChild(el('div', '',
      '절 제목은 "## 0. 제출문" 이어야 합니다 - 보고서 양식에서 이 제목을 바꿨다면 되돌려 주세요.'));
    box.hidden = false;
  }
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
  drawNotice();
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
