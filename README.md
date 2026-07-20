# Chat2Order: 메신저 주문 자동 정리기

> "사장님은 소통에만 집중하세요. 대화 속 주문 정리는 Chat2Order가 알아서 엑셀로 만들어 드립니다."

판매자-고객 메신저 대화와 상품 카탈로그를 기반으로 LLM(Gemini API)이 주문 정보를 추출하여 엑셀 파일로 자동 변환합니다.

## 서비스
- https://chat2order.up.railway.app/

## 기술 스택

- Python 3.13.14, Streamlit, Google GenAI SDK (Gemini), Pandas, Pydantic, Requests, Supabase

---

## 프로젝트 구조

```
MarketMate_Chat2Order/
├── app.py                        # Streamlit 웹 앱 (메인 UI)
├── main.py                       # CLI 실행 진입점
├── services.py                   # 핵심 비즈니스 로직 (파싱, LLM 호출, 후처리)
├── settings.py                   # 환경변수 및 프롬프트 파일 로딩
├── models.py                     # Pydantic 데이터 모델
├── database.py                   # Supabase 연동 (인증, 주문 저장, 학습 데이터 저장)
├── config.yaml                   # 공개 설정 파일 (모델, 출력 컬럼, CSV 파싱 등)
├── .env.example                  # Railway 환경변수 이름 예시
├── prompts/                      # Git으로 관리하는 LLM 프롬프트
│   ├── order_extraction.txt
│   └── address_to_search.txt
├── convert_chat_csv_to_jsonl.py  # CSV → JSONL 변환 스크립트
├── requirements.txt
├── styles/
│   └── main.css                  # Streamlit 커스텀 CSS
└── .streamlit/
    └── config.toml               # Streamlit 테마 설정
```

---

## 실행 방법

### Streamlit 웹 앱

```bash
conda activate c2o
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

앱 실행 후 로그인이 필요합니다. 계정은 Supabase `accounts` 테이블에서 관리합니다.

로컬 실행 전 필요한 환경변수를 설정합니다.

```bash
export SUPABASE_URL="<SUPABASE_URL>"
export SUPABASE_KEY="<SUPABASE_KEY>"
export JUSO_API_KEY="<JUSO_API_KEY>"
export AUTH_SECRET="<AUTH_SECRET>"
python -m streamlit run app.py
```

### Railway 배포 설정

Railway의 Streamlit 서비스에서 **Variables** 탭에 다음 변수를 등록한 후 배포합니다.

| 환경변수 | 설명 | 필수 여부 |
|---|---|---|
| `SUPABASE_URL` | Supabase 프로젝트 URL | 웹 로그인 사용 시 필수 |
| `SUPABASE_KEY` | Supabase API Key | 웹 로그인 사용 시 필수 |
| `JUSO_API_KEY` | 행정안전부 도로명주소 API Key | 우편번호 조회 시 필수 |
| `AUTH_SECRET` | 로그인 쿠키 서명용 비밀키 | 로그인 유지(권장) |

> `AUTH_SECRET`은 로그인 상태를 서명 쿠키로 영속화해, 추출 도중 웹소켓이 재연결돼도
> 로그인 화면으로 튕기지 않게 합니다. 미설정 시 로그인은 세션에만 유지됩니다.
> `python -c "import secrets; print(secrets.token_urlsafe(32))"` 로 긴 무작위 값을 생성하세요.

`.env.example`은 필요한 변수 이름을 보여주는 템플릿이며 실제 비밀값을 저장하거나 Git에 커밋하지 않습니다.

### Railway Variables로 로컬 실행

Railway CLI는 Python 패키지가 아니므로 npm으로 Conda 환경에 설치합니다. Node.js 16 이상이 필요합니다.

```bash
conda activate c2o
npm install --global --prefix "$CONDA_PREFIX" @railway/cli@5.26.1
railway --version
```

최초 한 번 Railway에 로그인하고 현재 저장소를 배포 프로젝트 및 서비스에 연결합니다.

```bash
railway login
railway link
railway status
```

브라우저를 열 수 없는 환경에서는 `railway login --browserless`를 사용합니다. 연결이 끝나면 Railway 서비스의 Variables를 로컬 Streamlit 프로세스에 주입하여 실행할 수 있습니다.

```bash
conda activate c2o
railway run python -m streamlit run app.py
```

`railway run`은 연결된 프로젝트·환경·서비스를 기본으로 사용합니다. 대상을 명시하려면 다음과 같이 실행합니다.

```bash
railway run \
  --environment production \
  --service <STREAMLIT_SERVICE_NAME> \
  python -m streamlit run app.py
```

이 방식으로 전달된 `SUPABASE_URL`, `SUPABASE_KEY`, `JUSO_API_KEY`는 `app.py`에서 일반 환경변수로 읽습니다. Railway에서 Sealed 처리한 변수는 `railway run`으로 전달되지 않으므로 로컬 테스트가 필요하면 별도의 비밀이 아닌 개발 환경 변수를 사용하세요.

### CLI

```bash
python3 main.py \
  --api-key <GEMINI_API_KEY> \
  --catalog catalog.json \
  --chat 고객A.csv 고객B.csv
```

| 옵션 | 설명 |
|---|---|
| `--api-key` | Gemini API Key (필수) |
| `--catalog` | 카탈로그 JSON 파일 경로 |
| `--chat` | 대화 파일 경로, 여러 개 가능 (CSV 또는 JSONL) |
| `--output` | 출력 엑셀 파일명 (기본값: `config.yaml`의 `file_name`) |
| `--config` | 설정 파일 경로 (기본값: `config.yaml`) |

### 학습용 JSONL 변환 스크립트

카카오톡 CSV 대화를 학습용 JSONL로 변환하는 보조 스크립트입니다(앱 런타임과 분리).

```bash
python convert_chat_csv_to_jsonl.py \
  --input-dir ./chat_csv \
  --output-dir ./chat_data \
  --prefix "이지픽_"
```

| 옵션 | 설명 |
|---|---|
| `--input-dir` | CSV 파일이 있는 폴더 (필수) |
| `--output-dir` | JSONL 출력 폴더 (없으면 생성, 필수) |
| `--prefix` | 파일명 접두사 및 glob 패턴 구성 (기본값: `이지픽_`) |
| `--time-after` | 이 시각 이후 메시지만 포함 (선택, 예: `'2026-07-01 00:00'`) |

---

## 설정

### `config.yaml` (공개)

| 항목 | 설명 |
|---|---|
| `gemini.model` | 사용할 Gemini 모델명 |
| `gemini.temperature` | LLM 응답 temperature |
| `prompts.order_extraction` | 주문 추출 프롬프트 파일 경로 |
| `prompts.address_to_search` | 주소 정제 프롬프트 파일 경로 |
| `output.file_name` | 출력 엑셀 파일명 |
| `output.sheet_name` | 엑셀 시트명 |
| `output_columns` | 출력 컬럼 매핑 (출력명: 원본필드명) |
| `csv.filename_prefix` | 카카오톡 채널 CSV 파일명 접두사 |
| `csv.exclude_messages` | 파싱 시 제외할 시스템 메시지 목록 |

### 프롬프트

프롬프트는 `prompts/` 디렉터리에서 Git으로 버전 관리합니다.

- `prompts/order_extraction.txt`: `{catalog}`, `{chat}` 플레이스홀더 사용
- `prompts/address_to_search.txt`: `{address}` 플레이스홀더 사용

플레이스홀더는 실행 시 실제 입력 데이터로 치환되므로 삭제하지 마세요.

### Supabase `accounts` 테이블

계정 인증 및 Gemini API Key를 DB에서 관리합니다.

| 컬럼 | 설명 |
|---|---|
| `user_id` | 로그인 이메일 |
| `password` | 비밀번호 |
| `gemini_api_key` | 계정별 Gemini API Key (로그인 시 자동 할당) |
| `is_active` | 계정 활성화 여부 |

---

## 사용 흐름 (웹 앱)

### 탭 1 — 📦 주문서 추출

1. 로그인 (Supabase `accounts` 테이블 인증)
2. 카탈로그 파일(`.json`) 업로드
3. 대화 내역 파일(`.csv`) 업로드 (여러 파일 가능)
4. 라이브쇼핑 시간 범위 지정
5. "주문서 추출 실행" 클릭
6. 추출 결과 확인 및 엑셀 다운로드

### 탭 2 — 📋 카탈로그 생성

1. 재고 CSV 파일 업로드 (`상품명`, `옵션내용` 컬럼 필요)
2. 상품/옵션 미리보기 확인
3. `catalog.json` 다운로드

### 탭 3 — 📮 우편번호 추출

1. 주소 컬럼이 포함된 엑셀 파일 업로드
2. "우편번호 조회 실행" 클릭
3. 우편번호가 채워진 엑셀 다운로드

### 탭 4 — 🗂️ 나의 추출 이력

- 최근 5건의 추출 작업 이력 조회
- 작업별 주문 데이터 미리보기
- 엑셀 파일(`.xlsx`) 다운로드
- 당시 사용한 카탈로그(`.json`) 재다운로드 (저장된 데이터가 있는 경우)

---

## 입력 데이터

### 카탈로그 파일 (`catalog.json`)

판매 중인 상품 목록. `{"상품명": ["옵션1", "옵션2"]}` 형태의 JSON 파일입니다.

```json
{
  "디오르 D스트랩 스커트": ["단일상품"],
  "발렌 봄가디건": ["그레이", "레드", "블랙", "화이트"]
}
```

탭 2(카탈로그 생성)에서 재고 CSV로부터 자동 생성할 수 있습니다.

### 대화 파일 (CSV 또는 JSONL)

고객과 판매자의 메신저 대화 내역. 여러 파일 동시 업로드를 지원합니다.

**CSV (카카오톡 채널 내보내기)**
- 파일명 형식: `다애모드(daae_mode)_<채팅명>.csv`
- 컬럼: `DATE`, `USER`, `MESSAGE`
- 채팅명은 파일명에서 자동 추출
- 타임스탬프는 `DATE` 컬럼 마지막 행에서 자동 추출
- `config.yaml`의 `exclude_messages`에 등록된 시스템 메시지 자동 제외

**JSONL**
- 파일명 형식: `<채팅명>_YYYY-MM-DD-HH-MM-SS.jsonl`
- 각 라인: `{'user': '메이진', 'message': '장현진/010-8610-1429/...'}`
- 채팅명과 타임스탬프 모두 파일명에서 자동 추출

---

## 출력 데이터

Excel 파일 (`orders_extracted.xlsx`)

| 컬럼 | 설명 |
|---|---|
| 주문번호 | `YYYYMMDD` + 3자리 순번 (예: `20260315001`) |
| 상품명 | 주문 상품명 (카탈로그 기준 매핑) |
| 옵션명 | 색상, 사이즈 등 옵션 |
| 수량 | 주문 수량 |
| 채팅명 | 파일명에서 자동 추출 |
| 수령자 | 배송 받을 실제 이름 (고객이 별도 명시한 경우만) |
| 전화번호 | 연락처 (`010-XXXX-XXXX` 형식 자동 정규화) |
| 주소 | 고객이 입력한 전체 배송지 주소 |
| 우편번호 | 도로명주소 API 자동 조회 (`JUSO_API_KEY` 설정 시 활성화) |

---

## 데이터 파이프라인

```
입력 파일 (CSV / JSONL)
    │
    ▼
파싱 & 전처리
 - CSV: 시스템 메시지 제거, 공백 정규화, 시간 범위 필터링
 - JSONL: ast.literal_eval 파싱
 - 파일명에서 chat_name, timestamp 추출
    │
    ▼
Gemini API (Structured Output)
 - Pydantic 스키마로 JSON 응답 강제
 - 추출 필드: order_name, phone_number, address, search_address, product, option, volume
    │
    ▼
후처리
 - 전화번호 포맷 정규화 (010-XXXX-XXXX)
 - 우편번호 자동 조회 (도로명주소 API)
 - chat_name, time, order_number 컬럼 주입
    │
    ▼
DB 저장 (Supabase)
 - 추출 작업 및 주문 데이터 저장 (이력 조회용)
 - 학습 데이터로 입력/출력 쌍 저장 (카탈로그 포함)
    │
    ▼
사용자 검수 (선택)
 - 채팅 파일별 주문·고객정보 승인 또는 수정
 - 최초 예측을 보존하고 확정 revision 및 corrected_json 저장
 - 확정 결과로 Excel 재생성
    │
    ▼
Excel 출력 (.xlsx)
```

### 주문 결과 검수 DB migration

`✅ 주문 결과 검수` 탭을 사용하려면 Supabase SQL Editor에서 다음 migration을
먼저 적용해야 합니다.

```text
migrations/001_order_reviews.sql
```

검수 탭은 최초 모델 예측을 덮어쓰지 않고 draft와 confirmed revision을 별도
저장합니다. 확정본이 있으면 `나의 추출 이력` 탭에서도 최신 confirmed revision을
우선 다운로드합니다.

---

## 보안 유의사항

- API 키와 DB 접속 정보는 Railway Variables 등 실행 환경의 환경변수로 관리하고 Git에 커밋하지 않습니다.
- `.streamlit/secrets.toml`과 흔한 오타인 `.streamlit/secret.toml`은 모두 `.gitignore`에 등록되어 있습니다.
- 프롬프트에는 비밀값이나 고객 개인정보를 넣지 않습니다.
- 계정 정보 및 Gemini API Key는 Supabase `accounts` 테이블에서 관리하며, 클라이언트에 노출되지 않습니다.
- 실제 고객 개인정보(이름, 연락처, 주소)가 포함된 원본 데이터는 프로토타입 환경에 업로드하지 마세요.
