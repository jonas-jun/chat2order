# 작업지시서

## 제목
- account별 월 최대 extract 횟수 제한 기능 추가

## 목적
- api 사용량 제한

## 방법

### 1. Supabase 스키마 변경
- `accounts` 테이블에 `monthly_extract_limit INTEGER NULL` 컬럼 추가 (NULL = 무제한)
- `extract_call_logs` 테이블 신규 생성
  - `id` UUID PK, `user_id` TEXT, `job_id` UUID FK, `chat_filename` TEXT, `called_at` TIMESTAMPTZ

### 2. API 호출 기록
- 주문서 추출 루프에서 파일 1개당 `extract_orders_from_chat` 호출 직후 `extract_call_logs`에 1행 삽입
- 주문 0건이어도 기록 (실제 API 호출 횟수 기준)

### 3. 월별 집계 및 제한
- 집계: `extract_call_logs`에서 `user_id` + 이번 달 1일 이후 `called_at` 기준으로 COUNT
- 추출 실행 전 잔여 횟수 계산 → 0이면 에러 표시 후 중단
- 잔여 횟수 < 업로드 파일 수이면 잔여 횟수만큼만 처리 후 경고 표시

### 4. UI
- 사이드바 Account State 섹션에 이번 달 사용량 표시 (`N / M회` 또는 `N회 / 무제한`)

## 관련 이슈