# 📄 프로젝트 기획서: Chat2Order (메신저 주문 자동 정리기)

## 1. 프로젝트 개요 (Project Overview)
* **서비스명**: Chat2Order
* **한 줄 소개**: "사장님은 소통에만 집중하세요. 대화 속 주문 정리는 Chat2Order가 알아서 엑셀로 만들어 드립니다."
* **목적**: 판매자와 고객의 메신저 대화 기록(`order_chat.jsonl`)과 상품 카탈로그(`catalog.jsonl`)를 기반으로 LLM(Gemini API)을 활용해 고객의 주문 및 배송 정보를 엑셀 파일로 자동 변환하는 웹 어플리케이션 구축.
* **주요 기술 스택**: Python 3.10+, Streamlit, Google GenAI SDK (Gemini API), Pandas, Requests

## 2. 데이터 명세 (Data Specification)

### 2.1. Input Data (사용자 업로드)
🚨 **[중요] 데이터 포맷 유의사항**: 업로드되는 `.jsonl` 파일들의 각 라인은 표준 JSON 포맷(큰따옴표)이 아닌, **파이썬 딕셔너리 문자열 형태(작은따옴표 사용)**로 이루어져 있습니다. 파싱 시 주의가 필요합니다.

**카탈로그 파일 (`catalog.jsonl`)**
- 판매 중인 상품과 옵션 정보를 담은 기준 데이터.
- **예시**:

```text
{'id': 73, 'product': '프리미엄 빅토리아 빅 숄더백(에토프)'}
```

**대화 기록 파일 (`order_chat.jsonl`) 다중 업로드 지원**
- 고객과 판매자가 나눈 메신저 대화 내역.
- **파일명 형식**: `{고객명}_{YYYY-MM-DD-HH-MM-SS}.jsonl` — 파일명의 날짜/시간이 주문 접수 시각(`time` 컬럼)으로 자동 추출됩니다.
- **예시**:

```
{'user': '메이진', 'message': '장현진/010-8610-1429/성남시 분당구 판교로30 201-104'}
```

**카카오톡 채널 CSV → JSONL 변환 (`convert_chat_csv_to_jsonl.py`)**
- 카카오톡 비즈니스 채널에서 내보낸 CSV 파일을 위 `.jsonl` 형식으로 일괄 변환합니다.
- UTF-8 BOM 인코딩 자동 처리, 시스템 메시지 자동 제외.

### 2.2. Output Data (최종 출력)
- 형식: Excel File (`orders_extracted.xlsx`)
- 추출 타겟 컬럼:

| 컬럼 | 설명 |
|------|------|
| `time` | 주문 접수 시각 (파일명에서 추출, `YYYY-MM-DD HH:MM:SS` 형식) |
| `name` | 주문자명 |
| `phone_number` | 연락처 (`010-XXXX-XXXX` 형식 자동 정규화) |
| `address` | 고객이 입력한 전체 배송지 주소 |
| `zip_code` | 우편번호 (도로명주소 검색 API로 자동 조회, 선택) |
| `product` | 주문 상품명 (카탈로그 기준 매핑) |
| `option` | 색상, 사이즈 등 옵션 |
| `volume` | 수량 |

## 3. 핵심 아키텍처 및 데이터 파이프라인 (Crucial)
본 서비스는 데이터 유실 방지와 안정성을 위해 [LLM JSON 응답 강제 ➔ 후처리 ➔ Pandas 변환 ➔ Excel 출력] 의 파이프라인을 엄격히 따릅니다.

**Step A. 데이터 파싱 로직 적용**
- 파이썬 딕셔너리 형태의 문자열이므로, 단순 `json.loads`가 아닌 `ast.literal_eval`을 활용하여 문자열을 안전하게 Python 객체로 변환합니다.

**Step B. Gemini API 호출 및 JSON 데이터 추출 (Structured Output)**
- 프롬프트를 통해 대화 내역에서 8가지 정보 추출을 지시합니다:
  - 주문자명, 연락처, 배송지 주소(`address`), 우편번호 검색용 주소(`search_address`), 상품명, 옵션, 수량
- `search_address`는 `address`에서 동/호수 등 상세주소를 제거한 도로명+건물번호만 포함 (우편번호 API 검색 정확도 향상 목적).
- 상품명(product)과 옵션(option)은 `catalog.jsonl` 데이터를 참조하여 매핑합니다. (대화에서 파악 불가한 정보는 null 처리)
- API 호출 시 `response_schema`를 Pydantic 모델로 정의하여, 응답이 지정된 키값을 가진 순수한 JSON 배열(List of Dicts) 형태로만 반환되도록 강제합니다.

**Step C. 후처리**
- 전화번호 포맷 정규화: 숫자만 추출 후 `010-XXXX-XXXX` 형식으로 변환.
- 우편번호 자동 조회: `search_address`를 행정안전부 도로명주소 검색 API에 전달하여 `zipNo` 추출. (juso API 키 입력 시에만 동작)
- 주문 접수 시각: 파일명에서 `YYYY-MM-DD-HH-MM-SS` 패턴을 파싱하여 datetime 객체로 변환.

**Step D. 데이터프레임 및 엑셀 변환**
- 반환된 JSON 데이터를 `pandas.DataFrame`으로 변환 후, `.to_excel()`을 사용하여 메모리 버퍼(`io.BytesIO()`)에 바이너리 엑셀 포맷으로 저장합니다.
- datetime 컬럼은 `YYYY-MM-DD HH:MM:SS` 형식으로 자동 포맷되어 출력됩니다.

## 4. UI/UX 요구사항 (Web Interface)
1. Title: "📦 Chat2Order: 메신저 주문 자동 정리기"
2. Sidebar:
   - Gemini API Key 입력란 (Password masking 처리 필수)
   - 도로명주소 API Key 입력란 (선택, 입력 시 우편번호 자동 조회 활성화)
3. File Upload:
   - `catalog.jsonl` 파일 업로드 위젯
   - `order_chat.jsonl` 다중 파일 업로드 위젯 (`accept_multiple_files=True`)
4. Action: [🚀 주문서 추출 실행] 버튼 및 실행 중 로딩 상태 표시 (`st.spinner`)
5. Result:
   - 추출 완료 후 데이터프레임 화면 표출 (`st.dataframe`)
   - [📥 엑셀 파일(.xlsx) 다운로드] 버튼 제공 (`st.download_button`)

## 5. 배포 전략 (Deployment Strategy: Prototype)
- 배포 환경: GitHub Repository 연동을 통한 Streamlit Community Cloud 자동 배포.
- 의존성 관리: 프로젝트 루트 디렉토리에 `requirements.txt` 파일을 포함할 것.
- 🚨 보안 유의사항: 퍼블릭 클라우드 환경이므로, 실제 고객의 민감한 개인정보(PII)가 포함된 원본 데이터 업로드를 지양하고 프로토타입 검증용 더미 데이터로 테스트할 것을 UI 상단에 안내 문구로 추가할 것.

## 6. 코딩 에이전트를 위한 Action Items
- Task 1: `requirements.txt` 작성 및 Streamlit UI 레이아웃 구현 (보안 안내 문구 포함).
- Task 2: `ast.literal_eval`을 활용한 커스텀 JSONL 파싱 유틸리티 함수 작성.
- Task 3: Pydantic을 이용해 8가지 필드를 명시한 스키마 클래스 정의 및 Gemini API 호출 로직 작성.
- Task 4: API 응답을 DataFrame으로 변환하고 `.xlsx` 메모리 버퍼로 저장하는 다운로드 연동 작업 완료.
- Task 5: 파일명 기반 주문 접수 시각(`time`) 자동 추출 및 Excel datetime 포맷 적용.
- Task 6: 행정안전부 도로명주소 검색 API 연동으로 우편번호(`zip_code`) 자동 조회 구현.
- Task 7: `convert_chat_csv_to_jsonl.py` — 카카오톡 채널 CSV를 JSONL로 일괄 변환하는 전처리 스크립트 작성.
