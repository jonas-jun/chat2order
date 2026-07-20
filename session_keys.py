"""``st.session_state`` 키 상수.

문자열 리터럴을 직접 쓰면 오타·불일치를 컴파일 시점에 잡을 수 없어, 세션 상태
접근에 사용하는 키를 한곳에 모은다. (DB/응답 dict의 키와는 별개다.)
"""

# 인증 / 계정
LOGGED_IN_USER = "logged_in_user"
API_KEY = "api_key"
MONTHLY_EXTRACT_LIMIT = "monthly_extract_limit"

# 주문서 추출 탭
CHAT_DISPLAY_NAMES = "chat_display_names"
CHAT_UPLOADER_KEY = "chat_uploader_key"

# 주문 결과 검수 탭
REVIEW_DEFAULT_JOB_ID = "review_default_job_id"
REVIEW_JOB_ID = "review_job_id"
REVIEW_SNAPSHOT = "review_snapshot"
REVIEW_TRAINING_RECORDS = "review_training_records"
REVIEW_SELECTED_FILE_ID = "review_selected_file_id"
REVIEW_CONFIRMED_REVISION = "review_confirmed_revision"
REVIEW_NEXT_FILE_ID = "review_next_file_id"

# 채팅 검색 탭
SEARCH_TRIGGER = "search_trigger"
SEARCH_RESULTS = "search_results"
SEARCH_PAGE = "search_page"
SEARCH_RESULTS_KEYWORD = "search_results_keyword"
