---
name: handoff
description: 컨텍스트 리셋 전 작업을 저장하고 새 세션에서 복원합니다. Keel Stop 훅의 자동 핸드오프 요청에도 사용합니다.
---
<!-- keel -->

# 핸드오프

## 자동 동작

Keel Stop 훅이 자동 핸드오프를 요청하면 훅 이유에 나온 세션 ID, CLI, cwd를 사용합니다. 사용자에게 확인을 묻지 않습니다.

1. `<cwd>/.keel/`을 만들고 `.gitignore`가 없으면 내용이 `*`인 파일을 만든 뒤 `<cwd>/.keel/handoff/handoff-<YYYY-MM-DD-HH-MM-SS>.md`를 만듭니다. 사용자의 목적과 제약, 완료한 작업, 수정한 파일, 현재 브랜치, 남은 작업, 블로커, 정확한 다음 동작을 기록합니다. 새 세션이 추측 없이 이어갈 수 있어야 합니다. 비밀값은 넣지 않습니다.
2. `python3 ~/.keel/hooks/handoff_auto.py --handoff <핸드오프 절대경로> --sid <세션 ID> --target <claude|codex|grok> --cwd <cwd 절대경로>`를 실행합니다. 이 명령은 마커를 쓰고 Herdr 또는 tmux 안에서 분리된 리셋 드라이버를 시작합니다.
3. 턴을 끝냅니다. Herdr나 tmux 밖에서는 명령이 출력한 수동 리셋 안내를 사용자에게 그대로 전합니다.

## 복원 동작

SessionStart가 핸드오프를 알리면 지정된 파일을 읽습니다. 이름을 유지한 채 `<cwd>/.keel/handoff/active/`로 옮깁니다. 이동 직후 지정된 마커를 삭제합니다. 사용자에게 복원 승인을 묻지 말고 남은 작업을 이어갑니다. 다른 세션이 가져간 active 핸드오프는 건드리지 않습니다.
