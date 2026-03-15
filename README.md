# Chat2Order: 메신저 주문 자동 정리기

> "사장님은 소통에만 집중하세요. 대화 속 주문 정리는 Chat2Order가 알아서 엑셀로 만들어 드립니다."

판매자-고객 메신저 대화와 상품 카탈로그를 기반으로 LLM(Gemini API)이 주문 정보를 추출하여 엑셀 파일로 자동 변환합니다.

## 기술 스택

- Python 3.10+, Streamlit, Google GenAI SDK (Gemini), Pandas, Pydantic, Requests, Supabase

---

## 프로젝트 구조

```
MarketMate_Chat2Order/
├── app.py                        # Streamlit 웹 앱 (메인 UI)
├── main.py                       # CLI 실행 진입점
├── services.py                   # 핵심 비즈니스 로직 (파싱, LLM 호출, 후처리)
├── models.py                     # Pydantic 데이터 모델
├── database.py                   # Supabase 연동 (학습 데이터 저장)
├── config.yaml                   # 설정 파일 (모델, 프롬프트, 출력 컬럼 등)
├── convert_chat_csv_to_jsonl.py  # CSV → JSONL 변환 스크립트
├── requirements.txt
└── .streamlit/
    ├── config.toml               # Streamlit 테마 설정
    └── secrets.toml              # 인증 정보 (API 키, DB 접속 등)
```

---

## 실행 방법

### Streamlit 웹 앱

```bash
streamlit run app.py
```

앱 실행 후 로그인이 필요합니다. 테스터 계정은 `.streamlit/secrets.toml`의 `tester_accounts`에 설정합니다.

### CLI

```bash
python3 main.py \
  --api-key <GEMINI_API_KEY> \
  --catalog catalog.jsonl \
  --chat 고객A.csv 고객B.csv
```

| 옵션 | 설명 |
|---|---|
| `--api-key` | Gemini API Key (필수) |
| `--catalog` | 카탈로그 JSONL 파일 경로 |
| `--chat` | 대화 파일 경로, 여러 개 가능 (CSV 또는 JSONL) |
| `--output` | 출력 엑셀 파일명 (기본값: `config.yaml`의 `file_name`) |
| `--config` | 설정 파일 경로 (기본값: `config.yaml`) |

---

## 설정

### `config.yaml`

| 항목 | 설명 |
|---|---|
| `gemini.model` | 사용할 Gemini 모델명 |
| `gemini.temperature` | LLM 응답 temperature |
| `output.file_name` | 출력 엑셀 파일명 |
| `output.sheet_name` | 엑셀 시트명 |
| `output_columns` | 출력 컬럼 매핑 (출력명: 원본필드명) |
| `csv.filename_prefix` | 카카오톡 채널 CSV 파일명 접두사 |
| `csv.exclude_messages` | 파싱 시 제외할 시스템 메시지 목록 |
| `prompt.order_extraction` | Gemini에 전달할 프롬프트 템플릿 (`{catalog}`, `{chat}` 플레이스홀더 사용) |

### `.streamlit/secrets.toml`

| 항목 | 설명 |
|---|---|
| `tester_accounts` | 로그인 허용 이메일/비밀번호 |
| `juso.api_key` | 행정안전부 도로명주소 API 키 (우편번호 자동 조회용) |
| `supabase.url` | Supabase 프로젝트 URL |
| `supabase.key` | Supabase API 키 |

---

## 사용 흐름 (웹 앱)

1. 로그인
2. 사이드바에서 Gemini API Key 입력
3. 카탈로그 파일(`.jsonl`) 업로드
4. 대화 내역 파일(`.csv` 또는 `.jsonl`) 업로드 (여러 파일 가능)
5. 라이브쇼핑 시간 범위 지정
6. "주문서 추출 실행" 클릭
7. 추출 결과 확인 및 엑셀 다운로드

---

## 입력 데이터

### 카탈로그 파일 (`catalog.jsonl`)

판매 중인 상품 목록. 각 라인은 Python dict 문자열 형태입니다.

```
{"상품명": "디오르 D스트랩 스커트", "옵션": ["단일상품"]}
{"상품명": "발렌 봄가디건", "옵션": ["그레이", "레드", "블랙", "화이트"]}
```

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
| 우편번호 | 도로명주소 API 자동 조회 (`juso.api_key` 설정 시 활성화) |

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
 - 학습 데이터로 입력/출력 쌍 저장
    │
    ▼
Excel 출력 (.xlsx)
```

---

## 보안 유의사항

- `.streamlit/secrets.toml`에 API 키와 인증 정보가 포함되므로 `.gitignore`에 등록되어 있습니다.
- 실제 고객 개인정보(이름, 연락처, 주소)가 포함된 원본 데이터는 프로토타입 환경에 업로드하지 마세요.
