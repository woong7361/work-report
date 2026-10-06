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
  { k:'custom_samples', t:'bool', def:false, label:'내가 쓰던 대로 쓰기',
    hint:'PMS에서 받아온 내 지난 제출문의 끝맺는 말과 어휘를 따른다.' +
         '\n모양과 분량은 보고서 양식이 정하므로 바뀌지 않는다.' +
         '\n받아온 것이 없으면 아무 일도 하지 않는다 (위 "지난 제출문 가져오기")' },

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
    if (isHead(lines[i]) && isSubmitHead(lines[i].replace(/^#+\s*/, ''))) { start = i + 1; break; }
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
  if (!THEME_NAME[t]) t = 'system';
  if (t === 'system') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', t);
  $('theme').innerHTML = THEME_ICON[t];
  $('theme').title = '화면 밝기: ' + THEME_NAME[t];
  $('theme').dataset.now = t;
  try { localStorage.setItem('wr-theme', t); } catch (e) {}
}
function toggleTheme(){
  const now = $('theme').dataset.now || 'system';
  if (now === 'system') {
    // 시스템 모드에서 첫 클릭이 아무 변화도 만들지 않는 것처럼 보이지 않게
    // 현재 실제 화면의 반대 밝기를 바로 선택한다.
    const systemDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    applyTheme(systemDark ? 'light' : 'dark');
    return;
  }
  applyTheme(now === 'dark' ? 'light' : 'dark');
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
  toggleTheme();
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
      title: '지난 제출문 가져오기',
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
  const total = steps.length;
  const done = steps.filter(s => s.ok).length;
  const left = steps.filter(s => !s.ok && !s.optional).length;
  const requiredDone = steps.filter(s => !s.optional).every(s => s.ok);
  const complete = requiredDone;
  const head = el('h1', 'setuphead ' + (complete ? 'complete' : 'incomplete'),
    left ? '아직 ' + left + '가지가 남았습니다' : '다 되어 있습니다');
  head.appendChild(el('span', 'setup-progress ' + (complete ? 'complete' : 'incomplete'),
    complete ? '✓ ' + done + '/' + total : done + '/' + total + ' 완료'));
  box.appendChild(head);
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
  // 이름 있는 탭을 재사용한다. 연속으로 누르거나 보고서를 다시 열어도
  // 제출 화면이 새 탭으로 계속 늘어나지 않는다.
  const submitWindow = window.open(SUBMIT_URL, 'work-report-submit');
  if (submitWindow) {
    try { submitWindow.focus(); } catch (e) {}
  }
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
