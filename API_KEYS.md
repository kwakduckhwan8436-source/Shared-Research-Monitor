# API 키 관리 가이드 (DART · 네이버 뉴스)

이 서비스는 모든 키를 **환경변수로만** 읽습니다. 키를 코드·저장소에 절대
넣지 마세요. 아래 절차대로 하면 GitHub 공개 여부와 무관하게 키가 안전합니다.

---

## 1. DART 전자공시 API 키

- **발급(무료)**: https://opendart.fss.or.kr → 회원가입 → 오픈API 신청 → 인증키
- **환경변수**: `DART_API_KEY=<40자리 키>`
- **용도**: 실시간 공시 피드, 종목명 매핑
- **위험도 낮음**: 무료·금전 피해 없음. 일일 호출 한도(약 2만 건)만 있음.
- 키만 넣으면 공시 피드가 바로 동작합니다(회사명은 DART 응답을 직접 사용).

## 2. 네이버 뉴스 검색 API

- **발급(무료)**: https://developers.naver.com → Application 등록 → "검색" API 선택
- **환경변수**:
  - `NAVER_CLIENT_ID=<Client ID>`
  - `NAVER_CLIENT_SECRET=<Client Secret>`
- **용도**: 실시간 뉴스 피드(제목+링크)
- **위험도 낮음**: 무료. 단 Secret 유출 시 타인이 일일 한도(2.5만 건)를 소진할 수 있으니 환경변수로만 보관.
- 등록 시 "WEB 설정"에 서버 도메인을 넣으세요.

---

## 로컬 개발

프로젝트 루트에 `.env` 파일을 만들고(이미 .gitignore로 제외됨):

```
DART_API_KEY=여기에_DART_키
NAVER_CLIENT_ID=여기에_네이버_ID
NAVER_CLIENT_SECRET=여기에_네이버_시크릿
```

`.env.example`을 복사해서 값만 채우면 됩니다. **`.env`는 절대 커밋하지 마세요.**

## 서버 배포(Render 예시)

`.env` 파일을 서버에 올리지 말고, 플랫폼의 암호화된 환경변수에 입력:

1. Render Dashboard → 서비스 → **Environment** 탭
2. "Add Environment Variable"로 위 3개 키 입력
3. `render.yaml`에서 이 키들은 `sync: false`로 되어 있어 **코드·저장소에 저장되지 않습니다**.

Railway는 Variables 탭, Fly.io는 `fly secrets set DART_API_KEY=...` 명령을 씁니다.

---

## 키 노출 사고 시 대처

1. **즉시 발급처에서 키를 폐기·재발급** (가장 먼저!)
   - DART: 마이페이지에서 인증키 재발급
   - 네이버: Application에서 Secret 재발급
2. git에 올라갔다면 파일 삭제만으로는 부족(커밋 히스토리에 남음).
   재발급으로 기존 키를 무력화하는 것이 핵심.

## 안전장치(이미 적용됨)

- `.gitignore`가 `.env`·`*.key`·`secrets.json` 제외
- `/api/health`는 키 **존재 여부(true/false)만** 노출하고 값은 절대 반환 안 함
- 모든 키는 `os.getenv`로만 읽힘(하드코딩 없음)
- 프런트엔드(브라우저)로는 키가 전혀 전달되지 않음(백엔드에서만 호출)

## 참고: 넣지 않아도 되는 키

공개 안전모드(`RECO_PUBLIC_MODE=1`)에서는 **KIS(증권 거래) 키가 불필요**합니다.
KIS 키는 실제 계좌에 접근하므로 공개 서버에 두지 마세요. 거래 기능은 본인 로컬에서만.
