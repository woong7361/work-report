# 리팩터링 인수 테스트

`acceptance.py`는 기존 외부 동작을 18개 시나리오로 확인한다. 보고서 파싱·검사,
비밀값 보관, 설정 허용 목록, 수집기 마스킹·저장소 판정, 러너 잠금, PMS 설정과
시작하기 상태를 임시 보고 폴더에서 검사한다.

`refactor_acceptance.py`는 모듈을 나눈 뒤 경계 사이의 연결을 확인한다.

- `test_http_read_write_assets_and_origin`: 실제 로컬 HTTP 서버에서 기존 끝점과
  새 페이지 자산을 읽고, 보고서 저장·설정 저장·Origin 차단·경로 이탈 차단을 확인한다.
- `test_pms_issue_output_and_exit_code`: 가짜 PMS JSON 응답을 열린 일감 Markdown으로
  저장하고 성공 종료 코드를 확인한다.
- `test_harvest_keeps_user_text_and_counts_reports`: 지난 보고서 예시를 표시선 안에
  갱신하면서 사용자가 쓴 앞뒤 문장을 보존하고 통계 값을 확인한다.
- `test_pms_fill_appends_without_saving`: 기존 폼 행을 보존한 채 분류·본문·일감을
  추가하고 저장 동작을 호출하지 않는지 확인한다.
- `test_job_exit_codes_and_dismissal`: PMS 종료 코드와 뷰어 상태의 대응, 완료 작업
  삭제, 실행 중 작업 보존을 확인한다.
- `test_collect_transcripts_and_output`: 가짜 Claude·Codex 기록에서 지시·편집 파일을
  수집하고 Markdown 출력에 반영되는지 확인한다.
- `test_python_modules_have_no_unbound_global_names`: 함수를 옮긴 뒤 모듈 전역 이름이
  빠지지 않았는지 표준 라이브러리 심볼 테이블로 점검한다.

실행 방법:

```powershell
python tests\acceptance.py
python -W ignore::ResourceWarning tests\refactor_acceptance.py
```
