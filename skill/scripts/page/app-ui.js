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
