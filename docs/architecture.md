# 구조

work-report는 AI 대화 기록과 git 이력에서 그날 한 일을 뽑아 보고서를 만들고,
그 보고서를 회사 PMS의 일일보고 폼에 채워 주는 도구다. Windows에서 돌고,
한 사람의 PC 안에서 끝난다. 서버도 없고 외부로 나가는 것은 PMS뿐이다.

이 문서는 **지금 무엇이 어디 있는지**를 적는다. 고칠 계획은 `refactor-plan.md`에 있다.

## 네 가지 일

도구가 하는 일은 넷이고, 서로 다른 시점에 다른 주체가 실행한다.

| 일 | 언제 | 누가 실행하나 |
|---|---|---|
| 수집 | 보고서를 만들 때 | `collect.ps1` → `collect.py` |
| 보고서 쓰기 | 수집 다음 | AI 에이전트 (Claude Code 또는 Codex) |
| 보기·고치기 | 사람이 열 때 | `viewer.py` (로컬 HTTP 서버) |
| PMS 채우기 | 사람이 누를 때 | `pms.ps1` → `pms.py` → 전용 크롬 |

**AI가 하는 일은 하나뿐이다.** 수집 결과를 읽고 글을 쓰는 것. 나머지는 전부
결정적인 스크립트다. 에이전트는 `run-report.ps1`이 조립한 한 줄짜리 지시로 불린다.

## 실행 경로

```
작업 스케줄러 (평일 17:30)
  또는 뷰어의 "일일보고 만들기"
        ↓
run-report.ps1          구간 결정, 잠금, 에이전트 호출, 알림, 결과 판정
        ↓
에이전트  ──→ collect.ps1 → collect.py     기록 → raw/<월>/<날짜>.md
          ──→ (글쓰기)                      → log/, daily/, weekly/
        ↓
알림 클릭 → open-path.ps1 → open-viewer.ps1 → viewer.py
        ↓
뷰어의 "PMS에 채우기" → pms.ps1 → pms.py → 전용 크롬 → 폼
        ↓
사람이 [저장]
```

## 파일

### 스크립트 (`skill/scripts/`)

| 파일 | 줄 | 하는 일 |
|---|---|---|
| `viewer.py` | HTTP 서버와 조립 | 끝점과 페이지 조립. 파일·작업·설정·제출문 기능은 아래 모듈에서 가져온다 |
| `viewer_files.py` | 보고서 파일·설정 | 보고서 목록, 경로 안전 검사, 사용자 양식, 설정 저장 |
| `viewer_jobs.py` | 작업 수명주기 | 보고서·PMS 작업 시작과 상태 판정 |
| `viewer_setup.py` | 시작하기 상태 | 보고서·PMS 준비 상태 집계 |
| `submission.py` | 제출문 파싱 | 제출문 절을 폼 입력으로 바꾸는 규칙 |
| `page/index.html`·`app*.js`·`app.css` | 정적 페이지 | 뷰어 화면의 HTML, JavaScript, CSS |
| `collect.py` | 설정·조립·CLI | 수집기 설정과 기록원 결과를 하나로 묶는다 |
| `sources_claude.py`·`sources_codex.py` | AI 기록원 | Claude Code와 Codex 세션 기록을 읽는다 |
| `sources_git.py` | git 기록원 | 커밋과 미커밋 파일을 읽는다 |
| `sources_files.py` | 파일 기록원 | PC에서 수정된 파일을 읽는다 |
| `custom_files.py` | 사용자 양식 기록원 | 사용자 양식·문체·예시 파일을 판정한다 |
| `render.py` | 수집 결과 출력 | 수집 결과를 Markdown으로 렌더링한다 |
| `prompts.py` | 프롬프트 기록 공통 | 프롬프트 정리와 비밀값 가리기 |
| `pms.py` | CLI와 작업 조립 | PMS 모드별 진입점과 폼 채우기 조립 |
| `pms_config.py` | PMS 설정·종료 코드 | 설정 읽기와 `Stop` 계약 |
| `pms_browser.py` | 브라우저 수명 | 크롬·CDP 연결, 창 상태, 로그인 판정 |
| `pms_form.py` | PMS 폼 지식 | 선택자, 분류 표, 입력, 제출문 검사 |
| `pms_harvest.py` | 지난 보고서 수집 | 통계와 문체 예시 파일 쓰기 |
| `pms_issues.py` | 열린 일감 | API에서 연결 가능한 일감을 받는다 |
| `_env.ps1` | 경로·설정·에이전트 홈 | 공통 환경 값과 하위 PowerShell 모듈 로딩 |
| `_bins.ps1` | 실행 파일 탐색 | Python·Claude·Codex 탐색 |
| `_toast.ps1` | 알림 | Windows 알림과 메시지 파일 읽기 |
| `_formats.ps1` | 양식 파일 선택 | 보고서 양식·문체·예시 경로 선택 |
| `_viewer.ps1` | 뷰어 프로세스 | 실행 중인 뷰어 종료 |
| `_errors.ps1` | 오류 기록 | 한 달 보존 실행 오류 기록 |
| `run-report.ps1` | 296 | 예약 실행의 본체. 에이전트를 부르고 결과를 판정한다 |
| `register-appid.ps1` | 138 | 알림 신원과 시작 메뉴 등록 |
| `secrets.py` | 100 | 비밀값을 DPAPI로 묶어 보관 |
| `open-path.ps1` | 76 | 알림 클릭이 도착하는 자리 |
| `_log.py` | 70 | 파이썬 쪽 오류 기록 |
| `open-viewer.ps1` | 68 | 뷰어를 띄우거나 떠 있는 것을 재사용 |
| `collect.ps1` | 52 | 수집기 래퍼. 파이썬을 찾아 부른다 |
| `pms.ps1` | 51 | PMS 래퍼. 같은 이유로 존재한다 |
| `resolve-bins.ps1` | 40 | 설정 화면에 보여 줄 실행 파일 경로 탐색 |

래퍼(`.ps1`)가 따로 있는 이유는 인터프리터 이름이 PC마다 다르기 때문이다
(`py`, `python3`, `python`). **파이썬을 직접 부르는 곳은 없다.**

### 에이전트가 읽는 글

| 파일 | 누가 읽나 |
|---|---|
| `skill/SKILL.md` | 에이전트. 절차와 지키는 선 |
| `skill/templates/report-format.md` | 에이전트. 보고서의 절 구성과 제출문 모양 |
| `skill/templates/writing-rules.md` | 에이전트. 문체 |
| `skill/templates/my-reports.md` | 보고 폴더로 복사되는 빈 그릇. 지난 제출물이 들어간다 |
| `skill/GUIDE.md` | 사람. 뷰어의 `사용 설명` 탭 |

### 설치

| 파일 | 하는 일 |
|---|---|
| `install.cmd` / `install.ps1` | skill 배치, 예약 등록, 알림 신원, playwright 설치 |
| `uninstall.cmd` / `uninstall.ps1` | 되돌리기. 보고서는 남긴다 |

skill은 `%LOCALAPPDATA%\work-report\skills\<이름>`에 한 벌만 두고 에이전트 홈마다
junction으로 가리킨다. 한 번 고치면 전부 따라오고, 설치가 중간에 실패해도
에이전트마다 버전이 엇갈리지 않는다.

## 보고 폴더

기본 위치는 `~\work-report`다. 업데이트가 덮지 않는다.

```
daily/<월>/<날짜>.md          제출용 일일 보고서
weekly/<월>/<시작>_<끝>.md     주간 보고서
log/<월>/<날짜>.md            한 일 목록 (보고서의 근거)
raw/<월>/<날짜>.md|.json      수집 원본
runlog/<월>/                  실행 기록, 클릭 기록, PMS 기록
runlog/.running               실행 잠금
runlog/viewer.json            떠 있는 뷰어의 포트와 pid
custom/                       내 양식·문체·지난 제출물
pms/my-daily-reports.json     PMS에서 받아 온 내 지난 보고서
pms/patterns.md               그 보고서를 센 값 (분류 분포, 행 수, 일감 사용)
pms/open-issues.md            연결할 수 있는 일감
pms/rows/<날짜>.json          제출문을 폼 입력으로 바꾼 것
pms/secrets.dat               DPAPI로 묶인 비밀값
browser/                      PMS 전용 크롬 프로파일
config.json                   설정
```

**산출물은 월 폴더 아래 둔다.** 해가 쌓여도 한 폴더를 훑을 수 있어야 한다.
월은 범위의 끝 날짜로 정한다.

## 경계

### 설정

`config.json` 하나다. 사람이 고치는 값, 설치가 채우는 값, 그리고 비밀값의
자리가 나뉜다.

- 사람이 고치는 값: `author`, `agent`, `notify`, `submit_url`, `exclude_*`,
  `custom_*`, `backfill_days`, `retain_months`, `weekly.*`, `pms.*`
- 설치가 채우는 값: `*_homes`, `*_dirs`, `skill_dirs`. 손으로 고치면 깨진다
- 비밀값: **config.json에 없다.** `pms/secrets.dat`에 DPAPI로 묶여 있다

뷰어의 설정 화면은 허용 목록(`EDITABLE`, `WEEKLY_KEYS`, `PMS_KEYS`)에 있는 키만
저장한다. 토큰은 들어오면 금고로 보내고 설정에는 쓰지 않는다.

### 비밀값

`config.json`은 보고 폴더 안에 있고, 러너는 에이전트에게 `--add-dir <보고 폴더>`를
준다. 즉 **에이전트가 읽을 수 있는 자리다.** 그래서 토큰은 거기 두지 않는다.

DPAPI(`CryptProtectData`)는 같은 PC의 같은 계정에서만 푼다. 파일을 복사해 가도
다른 계정에서는 열리지 않고, 외울 비밀번호도 추가 설치물도 없다. 읽는 쪽은
뷰어와 `pms.py`뿐이다.

### PMS

PMS의 일일보고는 Redmine 플러그인이고 **REST API를 내주지 않는다**
(컨트롤러가 API 인증을 선언하지 않아 `.json` 요청이 403이다). 그래서 폼은
브라우저로 채운다. 로그인은 SSO라 사람이 한 번 해야 한다.

전용 크롬 프로파일을 쓰는 이유는 Playwright가 **자기가 띄운 브라우저만**
조작하기 때문이다. 평소 쓰는 크롬은 디버깅 포트 없이 떠 있어 붙을 수 없고,
같은 프로파일로 다시 띄우면 기존 프로세스에 창만 붙는다. 전용 프로파일에
한 번 로그인해 두면 세션이 그 폴더에 남는다. 비밀번호는 저장하지 않는다.

코어 Redmine API는 열려 있다. **토큰이 하는 일은 하나**다 — 보고서를 쓰는
시점에 열린 일감 목록을 받아 오는 것. 그때는 브라우저가 없고 예약 실행이면
창을 띄울 수도 없다.

### 종료 코드

`pms.ps1`과 뷰어 사이의 약속이다. 사람이 다음에 할 일이 저마다 달라서
하나로 묶지 않는다.

| 코드 | 뜻 | 화면이 띄우는 것 |
|---|---|---|
| 0 | 채웠다 | PMS 창 보기 |
| 10 | 채울 것이 없어 폼만 열었다 | 안내만 |
| 2 | 로그인이 필요하다 | 로그인 창 열기 |
| 3 | 설정이 비었다 | 설정 열기 |
| 4 | playwright가 없다 | 설치 명령 |
| 5 | 브라우저를 열 수 없다 | 경로·포트 안내 |

`run-report.ps1`은 따로다. 0은 썼다, 2는 다른 실행이 돌아 건너뛰었다.

### 뷰어 끝점

전부 `127.0.0.1`의 임시 포트에 붙는다. 바깥 출처의 요청은 `Origin`으로 거른다.

```
GET   /files /jobs /config /bins /setup /readme /report /favicon.ico
POST  /config /run /pms /seen /dismiss
```

## 알아 둘 것

**제출문이 원본이다.** 뷰어가 보고서의 `## 0. 제출문` 절을 읽어 폼 입력으로
바꾼다. 에이전트가 기계용 파일을 따로 쓰지 않으므로 사람이 읽는 글과 실제로
올라가는 내용이 어긋날 수 없다.

**사람마다 다른 것은 코드에 넣지 않는다.** 어떤 분류를 쓰는지, 작업 항목을
몇 덩어리로 나누는지, 일감을 연결하는지는 사람마다 다르다. 그래서 규칙으로
박지 않고 그 사람의 지난 보고서를 세어 `pms/patterns.md`로 넘긴다.

**근거가 없으면 비운다.** 수집기는 무시한 것을 왜 무시했는지 적고, 폼을
채우는 쪽은 분류를 고르지 못하면 무엇이 남았는지 말한다. 조용히 지어내는
것이 가장 나쁘다.
