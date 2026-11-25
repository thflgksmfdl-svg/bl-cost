# jp-pack 정산 메뉴 연결

`jp-pack.netlify.app/bl-cost/`는 이 폴더의 준비 화면을 먼저 표시하고, Render의 `/bl-cost/` 화면을 같은 주소에 엽니다. 서버가 유휴 상태에서 시작되거나 잠시 응답하지 않으면 자동으로 다시 연결합니다.

- `home-entry.html`을 기존 `web/index.html`의 도구 목록에 추가합니다.
- `bl-cost/`와 `_redirects`를 기존 패킹 사이트의 `web/` 및 배포 산출물에 복사합니다. 기존 리다이렉트 규칙이 있다면 병합합니다.
- 기존 패킹 파일·BL 데이터·인보이스 함수는 배포된 버전을 유지합니다. 해당 사이트의 서버 함수와 함께 배포하며 환경변수나 인증키를 정적 파일에 복사하지 않습니다.
- API와 정적 파일은 `/bl-cost/api/`, `/bl-cost/static/` 경로로 Render에 연결합니다. Django는 지정한 Netlify 출처의 CSRF 요청만 추가 허용합니다.
- 기존 Render 주소도 계속 사용할 수 있으며 DB와 공유시트는 같은 것을 사용합니다. 정산 화면은 기존 한국어 UI입니다.
- 준비 화면의 토끼 모션은 `bl-cost/bunny-working.svg`, 공통 파비콘은 `bl-cost/favicon.svg`를 사용합니다. 정산 앱은 같은 디자인의 `static/` 에셋을 사용하며 동작 줄이기 설정을 따릅니다.
