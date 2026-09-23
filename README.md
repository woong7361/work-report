# work-report

AI와 나눈 대화(Claude Code·Codex)와 git 이력에서 그날 한 일을 뽑아
**일일·주간 업무 보고서**를 만든다. 평일 저녁에 저절로 실행된다.

> **쓰는 법은 앱의 `사용 설명` 탭에 있다** (원문: `skill\GUIDE.md`).
> 이 문서는 설치하고 명령줄에서 부르는 사람을 위한 것이다.

## 설치

zip을 **영구 폴더에 푼다.** `Downloads`나 임시 폴더는 안 된다.
풀어 둔 폴더에서 **`install.cmd`를 더블클릭**하면 끝이다.
`설치 완료`가 나오고 앱이 열린다. (`-Force`로 설치하면 앱을 열지 않는다.)

옵션을 주려면 PowerShell에서 실행한다.

```powershell
cd <풀어 둔 폴더>
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -DailyTime 18:00
```

마지막에 이렇게 나오고 앱이 열린다.

```
  설치 완료

  보고서 폴더     : C:\Users\<계정>\work-report
  skill 위치      : C:\Users\<계정>\.claude\skills\work-report
  다음 자동 실행  : 2026-09-22 오후 5:30:00
```

### 옵션

| 옵션 | 뜻 |
|---|---|
| `-Author <이름>` | 보고서에 찍힐 이름 (기본 `git config user.name`) |
| `-DailyTime 17:30` | 평일 실행 시각 |
| `-WeeklyDay Wednesday -WeeklyTime 17:35` | 주간 실행 요일·시각 |
| `-Root <경로>` | 보고서를 둘 폴더 (기본 `~\work-report`) |
| `-Agents claude` | skill을 설치할 CLI만 고른다 (기본: 설치된 CLI 전부) |
| `-SkillName <이름>` | 같은 이름의 skill이 이미 있을 때 다른 이름으로 설치 |
| `-ClaudeHome` / `-CodexHome <경로>` | 에이전트 홈을 못 찾을 때 직접 지정 |
| `-Copy` | 연결 대신 복사해서 설치 |
| `-NoSchedule` | 자동 실행을 등록하지 않는다 |
| `-Force` | 묻지 않고 교체 |

skill은 **그 PC에 설치된 에이전트 전부**에 들어간다. Claude Code와 Codex가 다 있으면 양쪽에서
`/work-report`를 부를 수 있다. 어느 쪽이 자동 실행을 맡을지는 `config.json`의 `agent`로 정한다.

같은 이름의 다른 skill이 있으면 설치가 멈춘다. `-SkillName`으로 나란히 두거나 `-Force`로 교체한다.

## 명령줄에서 부르기

평일 17:30과 지정한 주간 요일에 저절로 실행된다. 시작 메뉴의 `work-report`로 열리는 앱에서도 만들 수 있고, 직접 부를 수도 있다.

에이전트에서 (Claude Code·Codex 어느 쪽이든):

```
/work-report                오늘치 일일 보고
/work-report 2026-09-18     특정 날짜
/work-report 주간 보고       이번 구간 주간 보고
```

명령줄에서:

```powershell
$s = "$env:USERPROFILE\.claude\skills\work-report\scripts"

powershell -NoProfile -ExecutionPolicy Bypass -File "$s\run-report.ps1" -Mode daily -From 2026-09-18
powershell -NoProfile -ExecutionPolicy Bypass -File "$s\run-report.ps1" -Mode daily -DryRun
powershell -NoProfile -ExecutionPolicy Bypass -File "$s\collect.ps1" -From 2026-09-18
```

## 산출물

```
~\work-report\
  daily\2026-09\     제출용 일일 보고서
  weekly\2026-09\    제출용 주간 보고서
  log\2026-09\       한 일 목록 (보고서의 근거)
  raw\2026-09\       수집 원본
  runlog\2026-09\    실행 기록
  custom\            내 양식 (업데이트가 덮지 않는다)
  config.json        설정
```

## 설정

`~\work-report\config.json`

| 키 | 뜻 |
|---|---|
| `author` | 보고서에 찍히는 이름 |
| `agent` | 자동 실행을 맡을 CLI (`claude` / `codex`) |
| `notify` | 알림 사용 여부 |
| `submit_url` | 제출 화면 주소. 넣으면 뷰어에 `제출하러 가기` 단추가 생긴다 |
| `exclude_repos` | 보고에서 뺄 저장소 |
| `exclude_paths` | 작업으로 치지 않을 경로 |
| `mine_only` | 내 이메일의 커밋만 센다 (기본 참) |
| `redact` | 프롬프트에 섞인 키·토큰을 가린다 (기본 참) |
| `backfill_days` | PC가 꺼져 빠뜨린 평일 보고서를 며칠 전까지 채울지 (기본 2) |
| `retain_months` | `raw\`·`runlog\`를 몇 달치만 남길지 (기본 0 = 전부 보관) |
| `weekly.end_day`, `weekly.span_days` | 주간 구간 (기본 수요일에 끝나는 7일) |
| `claude_bin`, `codex_bin`, `python_bin` | 자동 탐색이 실패할 때만 전체 경로 |

`*_homes`, `*_dirs`, `skill_dirs`는 설치가 채운다. 직접 고치지 않는다.

알림은 Windows 알림 설정에 **work-report**라는 이름으로 나타난다. 거기서 따로 켜고 끌 수 있다.
문구를 바꾸려면 `skill\scripts\messages.json`을 고친다.

## 제거

**`uninstall.cmd`를 더블클릭**하면 확인을 묻고 지운다.

예약 작업과 skill을 지운다. **보고서는 남는다.** 보고서까지 지우려면 `-PurgeReports`,
다른 이름으로 설치했다면 `-SkillName <이름>`을 함께 준다. 확인 없이 지우려면 `-Yes`.

## 문제 해결

| 증상 | 조치 |
|---|---|
| 보고서가 없다 | `runlog\<월>\<날짜>-daily.log` 마지막 줄을 본다. `written=False`면 그 위에 이유가 있다 |
| 예약 시간에 PC가 꺼져 있었다 | 다음 기동 때 실행된다. 빠진 날은 `backfill_days`가 채운다 |
| 알림이 안 뜬다 | Windows 설정 → 알림에서 `work-report`가 켜져 있는지 본다. 집중 지원도 끈다. 예약 작업이 "로그온 여부와 상관없이 실행"이면 알림이 뜨지 않는다 |
| 알림을 눌러도 안 열린다 | `runlog\<월>\click.log`를 본다. `opened`인데 화면이 없으면 `.md`에 연결된 프로그램이 없는 것이다 |
| 다른 CLI로 돌리고 싶다 | `config.json`의 `agent`를 바꾼다. 다음 실행부터 적용된다 (그 CLI에 skill이 설치돼 있어야 한다) |
| CLI가 없다 | 설치 중에 물어보면 `Y`. 로그인하라고 하면 안내대로 하고 엔터 |
| Python을 못 찾는다 | 설치 중에 물어보면 `Y`를 누른다. 직접 깔려면 python.org에서 받고 `Add python.exe to PATH`를 켠다. `WindowsApps`의 것은 실제 Python이 아니다 |
| 기록이 안 잡힌다 | `collect.ps1 -Check`로 찾은 폴더와 파일 수를 확인한다 |
| 문체를 바꾸고 싶다 | `skill\references\writing-rules.md`를 고친다 |
| 더블클릭했더니 경고가 뜬다 | 인터넷에서 받은 파일 표시 때문이다. `실행`을 누른다 |
| zip 파일이 실행을 거부한다 | `Get-ChildItem -Recurse \| Unblock-File` |

## 필요한 것

- Windows, Windows PowerShell 5.1
- Python 3.8 이상 — 없으면 설치할지 물어보고 python.org 설치본으로 깔아 준다 (관리자 권한 불필요)
- Claude Code CLI 또는 Codex CLI — 없으면 설치할지 물어보고 공식 설치 스크립트로 깔아 준다
- git (없으면 커밋 이력이 빠진다)

## 알아 둘 것

- 보고서에는 **자신이 친 프롬프트가 근거로 남는다.** 제출 전에 한 번 읽어 본다.
  키·토큰처럼 보이는 값은 가려서 수집하지만, 문장 속에 적어 둔 짧은 값까지 잡지는 못한다.
- 자동 실행은 에이전트를 권한 확인 없이 돌린다. 부담되면 `-NoSchedule`로 설치하고 직접 실행한다.
- 수집기는 기록을 읽기만 한다.

## 나눠줄 때

폴더 전체를 준다. `scripts\`만으로는 동작하지 않는다. 받는 사람은 `install.cmd`만 더블클릭하면 된다.
연결 설치는 푼 폴더를 계속 가리키므로 그 폴더를 지우면 안 된다. 남기기 싫으면 `-Copy`로 설치한다.

새 버전은 같은 자리에 덮어 풀고 `install.cmd`를 다시 실행한다. 설정값은 그대로 남는다.

팀원에게 보낼 안내:

> 1. 첨부 zip을 영구 폴더에 풉니다. 예: `C:\tools\work-report`
> 2. 그 폴더의 `install.cmd`를 더블클릭합니다. 경고가 뜨면 `실행`을 누릅니다.
> 3. `설치 완료`가 나오고 앱이 열리면 끝입니다. 창은 아무 키나 누르면 닫힙니다.
> 4. 평일 17:30에 저절로 실행되고, 완료 알림을 누르면 보고서가 열립니다.
> 5. 쓰는 법은 앱을 열고 왼쪽 아래 `사용 설명`을 누르면 나옵니다.
