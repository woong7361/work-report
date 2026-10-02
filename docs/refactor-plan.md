# 리팩터링 작업 지시

구조는 `architecture.md`에 있다. 먼저 읽는다.

목표는 **파일을 책임 단위로 가르는 것**이다. 동작은 지금 그대로여야 한다.
기능을 더하거나 빼지 않고, 고치고 싶은 것이 보여도 이 작업에서는 손대지 않는다.
발견한 것은 `docs/found.md`에 적어 두고 넘어간다.

## 지켜야 할 선

**동작은 모놀리식으로 둔다.** 프로세스를 늘리지 않고, 서버를 세우지 않고,
새 의존성을 깔지 않는다. 지금처럼 스크립트를 직접 불러 쓰는 구조 그대로다.

**AI가 읽는 글은 파이프라인 코드에 섞지 않는다.** 지시문, 양식, 문체, 안내
문구는 데이터 파일에 있고 코드는 경로만 다룬다. 코드 안에 사람이 읽을 문장을
문자열로 조립해 두지 않는다.

**설치와 업데이트가 깨지지 않아야 한다.** 받는 사람은 zip을 풀고 `install.cmd`를
누른다. 그러므로 다음은 바뀌면 안 된다.

- 진입점 이름과 인자: `collect.ps1`, `pms.ps1`, `run-report.ps1`, `open-viewer.ps1`,
  `open-path.ps1`, `install.ps1`, `uninstall.ps1`
- 보고 폴더의 구조와 파일 이름
- `config.json`의 키
- 종료 코드의 뜻
- 뷰어 끝점의 주소와 응답 모양

**파이썬은 표준 라이브러리만 쓴다.** playwright는 PMS 기능에서만 쓰고, 없으면
그 기능만 꺼진다. 다른 곳에서 import 하지 않는다.

**인터프리터를 직접 부르지 않는다.** 파이썬은 언제나 `.ps1` 래퍼를 거친다.
이름이 PC마다 다르다(`py`, `python3`, `python`).

## 바뀌면 안 되는 이름

인수 테스트가 이 이름들로 붙잡고 있다. 안을 어떻게 나누든 **이 모듈에서 이
이름으로 계속 불러낼 수 있어야 한다.** 옮긴 뒤 다시 내보내도 된다.

```
collect.py   redact   scan_custom   _repo_of
pms.py       load_cfg   require_config   validate_report   category_value
             Stop   EXIT_CONFIG
viewer.py    parse_submission   setup_status   write_config
secrets.py   put   get   has
```

테스트 파일(`tests/acceptance.py`)은 **고치지 않는다.** 고쳐야만 통과한다면
그것은 겉이 바뀌었다는 뜻이고, 그 변경은 이 작업의 범위 밖이다.

## 검증

단계마다 이 셋을 돌리고, 전부 통과해야 다음으로 간다.

```powershell
python tests\acceptance.py
powershell -NoProfile -ExecutionPolicy Bypass -File skill\scripts\run-report.ps1 -Mode daily -NoToast -DryRun -Backfill 0
powershell -NoProfile -ExecutionPolicy Bypass -File skill\scripts\collect.ps1 -Check
```

뷰어를 건드린 단계에서는 두 가지를 더 본다.

```powershell
# 페이지 자바스크립트 문법
python -c "import sys;sys.path.insert(0,'skill/scripts');import viewer,re,io;io.open('page.js','w',encoding='utf-8').write(re.search(r'<script>(.*)</script>',viewer.build_page('x','','제출',True,True),re.S).group(1))"
node --check page.js

# 실제로 떠서 그려지는지
powershell -NoProfile -ExecutionPolicy Bypass -File skill\scripts\open-viewer.ps1
```

**단계마다 커밋한다.** 한 단계가 한 커밋이다. 메시지에는 무엇을 왜 옮겼는지만
적는다. 도구나 어시스턴트의 흔적을 남기지 않는다.

## 순서

앞 단계가 뒤 단계의 발판이 되도록 짰다. 건너뛰지 않는다.

### 1. 프롬프트를 코드에서 꺼낸다

지금 `run-report.ps1` 91~121줄이 에이전트에게 보낼 영어 지시문을 문자열
덧셈으로 조립한다. 제어 흐름과 사람이 읽을 글이 한자리에 있다.

- `skill/prompts/ask.json`을 만든다. 모드별 지시문과 "이 파일을 먼저 읽어라"
  문구, 파일마다 붙이는 설명을 키로 둔다.
- `run-report.ps1`은 **어떤 파일이 있는지 판단하고 경로를 넘기는 일만** 한다.
  문장은 `ask.json`에서 온다. `messages.json`을 읽는 방식(`Get-Messages`)이
  이미 있으니 그대로 따른다.
- 조립 함수는 `_env.ps1`이 아니라 `run-report.ps1` 안에 둔다. 러너만 쓴다.

검증: DryRun 출력의 지시문이 바뀌기 전과 **글자 단위로 같아야 한다.**
바꾸기 전 출력을 파일로 떠 두고 비교한다.

### 2. `_env.ps1`을 책임별로 가른다

385줄에 일곱 가지가 있다. 경로·설정, 인터프리터 탐색, 에이전트 홈, 오류 기록,
뷰어 프로세스, 알림, 양식 파일 선택. 설치와 제거는 `Stop-Viewer` 하나를 쓰려고
이 전부를 dot-source 한다.

- `_env.ps1`은 **경로·설정·에이전트 홈**만 남긴다.
- `_bins.ps1` — 인터프리터와 CLI 탐색 (`Resolve-Python`, `Resolve-Claude`,
  `Resolve-Codex`, `Test-Interpreter`, `Test-ClaudeBin`)
- `_toast.ps1` — 알림 (`Show-Toast`, `Hide-Toast`, `Get-Messages`)
- `_formats.ps1` — 어떤 양식·문체·예시 파일을 쓸지 (`Get-FormatFile`,
  `Get-SampleFile`)
- `_viewer.ps1` — 뷰어 프로세스 (`Stop-Viewer`)
- `_env.ps1`이 나머지를 dot-source 해서 **지금 부르는 쪽은 그대로 동작하게**
  한다. 부르는 쪽을 한꺼번에 고치지 않는다.

이 단계에서 `Get-FormatFile`의 40자 기준과 `Get-SampleFile`의 40자 기준이
한 파일에 모인다. **합치지 말고 그대로 둔다.** 숫자를 공유하게 만드는 것은
별도 결정이고 여기서는 옮기기만 한다.

### 3. 페이지를 `viewer.py` 밖으로 꺼낸다

2681줄 중 1802줄이 `PAGE` 문자열 하나다(자바스크립트 1240, CSS 457).
린트도 문법 검사도 붙지 않고, 파이썬 파일을 고치다 페이지를 깨뜨리기 쉽다.

- `skill/scripts/page/index.html`, `page/app.js`, `page/app.css`로 가른다.
- `build_page()`는 파일을 읽어 치환자(`{{TITLE}}` 등)를 채운다. 치환자 이름과
  뜻은 그대로 둔다.
- 뷰어가 `app.js`와 `app.css`를 각각 내주는 길을 만든다. 경로는 `/page/app.js`,
  `/page/app.css`. 기존 끝점 이름과 겹치지 않는다.
- 설치가 `skill/` 폴더를 통째로 옮기므로 파일이 늘어도 배포는 그대로다.
  junction으로 설치된 경우를 위해 **`os.path.realpath(__file__)` 기준**으로
  찾는다(`find_guide()`가 이미 그렇게 한다).

검증에 `node --check`를 반드시 넣는다. 이 단계에서 깨지면 화면이 통째로 죽는다.

### 4. `viewer.py`의 파이썬 쪽을 가른다

페이지를 뺀 나머지 879줄이 서버, 파일 목록, 설정, 작업, 제출문 파싱, 상태를
한자리에서 한다.

- `viewer_files.py` — 보고서 목록, 경로 안전 검사, 사용자 양식 파일
- `viewer_jobs.py` — 작업 띄우기와 상태 (`start_job`, `start_pms`, `job_status`,
  `drop_job`, 종료 코드 해석)
- `viewer_setup.py` — `setup_status`
- `submission.py` — 제출문 파싱 (`parse_submission`과 그 정규식)
- `viewer.py` — HTTP 처리와 조립. 끝점 목록이 한눈에 보이는 자리로 남긴다.

`parse_submission`, `setup_status`, `write_config`는 `viewer.py`에서 계속
불러낼 수 있어야 한다.

### 5. `pms.py`를 가른다

903줄에 설정, 브라우저 수명, 폼 지식, 수집, 통계, 검사, CLI가 있다.

- `pms_config.py` — 설정 읽기와 비밀값 연결 (`load_cfg`, `require_config`)
- `pms_browser.py` — 크롬 띄우기, CDP 연결, 창 상태, 로그인 판정
- `pms_form.py` — **폼에 대한 지식만**. 선택자, 분류 표, 채우기, 검사
  (`fill_form`, `validate_report`, `category_value`, `CATEGORIES`)
- `pms_harvest.py` — 지난 보고서 수집, 통계, 예시 파일 쓰기
- `pms_issues.py` — 열린 일감 받기
- `pms.py` — CLI. 모드별로 위를 부르고 `Stop`을 받아 안내를 찍는다.

폼 선택자가 한 파일에 모이는 것이 이 단계의 핵심이다. PMS 화면이 바뀌면
고칠 자리가 하나여야 한다.

### 6. `collect.py`를 가른다

1104줄이 네 종류의 기록을 읽고 하나의 마크다운으로 만든다.

- `sources_claude.py`, `sources_codex.py`, `sources_git.py`, `sources_files.py`
- `custom_files.py` — 사용자 양식 선택과 옛 사본 감지 (`scan_custom`)
- `render.py` — 수집 결과 → 마크다운
- `collect.py` — 설정, 조립, CLI

`redact`는 어디에 둘지 생각한다. 프롬프트를 다루는 쪽(claude/codex)만 쓰므로
`sources_*`가 함께 쓰는 `prompts.py`가 맞을 수 있다. `collect.py`에서 계속
불러낼 수 있으면 된다.

## 하지 않을 것

- 기능 추가. 보이는 결함도 이 작업에서는 고치지 않는다
- 공개 이름 바꾸기. 설정 키, 파일 이름, 끝점, 종료 코드
- 테스트 수정
- 의존성 추가
- 한 커밋에 두 단계

## 끝났는지 보는 법

- 모든 단계가 커밋되어 있고, 각 커밋에서 검증 명령이 통과한다
- 어떤 파일도 600줄을 넘지 않는다
- `skill/scripts/*.py` 중 사람이 읽을 긴 문장을 문자열로 들고 있는 파일이 없다
- 새로 받은 PC에서 `install.cmd`로 설치하면 지금과 같이 동작한다
