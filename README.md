# 📦 Chat2Order: 메신저 주문 자동 정리기

> "사장님은 소통에만 집중하세요. 대화 속 주문 정리는 Chat2Order가 알아서 엑셀로 만들어 드립니다."

판매자-고객 메신저 대화와 상품 카탈로그를 기반으로 LLM(Gemini API)이 주문 정보를 추출하여 엑셀 파일로 자동 변환합니다.

## 기술 스택

- Python 3.10+, Streamlit, Google GenAI SDK (Gemini), Pandas, Pydantic, Requests

---

## 프로젝트 구조

```
MarketMate_Chat2Order/
├── app.py                      # Streamlit UI
├── main.py                     # CLI 실행 진입점
├── models.py                   # Pydantic 데이터 모델
├── services.py                 # 핵심 비즈니스 로직
├── config.yaml                 # 설정 (API 키 포함, .gitignore 처리)
├── config_template.yaml        # 설정 템플릿 (API 키 미포함)
├── requirements.txt
└── convert_chat_csv_to_jsonl.py  # (레거시) CSV → JSONL 변환 스크립트
```

---

## 실행 방법

### Streamlit 웹 앱

```bash
streamlit run app.py
```

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

## 설정 (`config.yaml`)

`config_template.yaml`을 복사해서 `config.yaml`로 만들고 API 키를 입력합니다.

```bash
cp config_template.yaml config.yaml
```

| 항목 | 설명 |
|---|---|
| `gemini.model` | 사용할 Gemini 모델명 |
| `gemini.temperature` | LLM 응답 temperature |
| `juso.api_key` | 행정안전부 도로명주소 API 키 (우편번호 자동 조회용) |
| `csv.filename_prefix` | 카카오톡 채널 CSV 파일명 접두사 |
| `csv.exclude_messages` | 파싱 시 제외할 시스템 메시지 목록 |
| `prompt.order_extraction` | Gemini에 전달할 프롬프트 템플릿 (`{catalog}`, `{chat}` 플레이스홀더 사용) |

---

## 입력 데이터

### 카탈로그 파일 (`catalog.jsonl`)

판매 중인 상품 목록. 각 라인은 Python dict 문자열 형태입니다.

```
{'id': 73, 'product': '프리미엄 빅토리아 빅 숄더백(에토프)'}
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
| `time` | 주문 접수 시각 (파일에서 자동 추출) |
| `order_name` | 배송 받을 실제 이름 (고객이 별도 명시한 경우만, 없으면 null) |
| `chat_name` | 채팅명 (파일명에서 자동 추출, LLM 불사용) |
| `phone_number` | 연락처 (`010-XXXX-XXXX` 형식 자동 정규화) |
| `address` | 고객이 입력한 전체 배송지 주소 |
| `zip_code` | 우편번호 (도로명주소 API 자동 조회, juso API 키 설정 시 활성화) |
| `product` | 주문 상품명 (카탈로그 기준 매핑) |
| `option` | 색상, 사이즈 등 옵션 |
| `volume` | 수량 |

---

## 데이터 파이프라인

```
입력 파일 (CSV / JSONL)
    │
    ▼
파싱 & 전처리
 - CSV: 시스템 메시지 제거, 공백 정규화
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
 - chat_name, time 컬럼 주입
    │
    ▼
Excel 출력 (.xlsx)
```

---

## 보안 유의사항

`config.yaml`은 API 키를 포함하므로 `.gitignore`에 등록되어 있습니다. 실제 고객 개인정보(이름, 연락처, 주소)가 포함된 원본 데이터는 프로토타입 환경에 업로드하지 마세요.
