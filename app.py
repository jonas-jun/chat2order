import io
import yaml
import pandas as pd
import streamlit as st

from services import (
    parse_custom_jsonl,
    extract_orders_from_chat,
    lookup_zip_code,
    format_phone_number,
    extract_timestamp,
)

# --- 설정 파일 로드 ---
with open("config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

# --- UI 구성 ---
st.set_page_config(page_title="메신저 주문서 자동 추출기", layout="wide")

st.title("📦 Chat2Order: 메신저 주문 자동 정리기")
st.markdown("사장님은 소통에만 집중하세요. 대화 속 주문 정리는 Chat2Order가 알아서 엑셀로 만들어 드립니다.")

st.warning(
    "⚠️ **보안 안내**: 본 서비스는 프로토타입 검증 환경입니다. "
    "실제 고객의 민감한 개인정보(이름, 연락처, 주소 등)가 포함된 원본 데이터 업로드를 지양하고, "
    "**테스트용 더미 데이터**로 검증해 주세요."
)

juso_api_key = config["juso"]["api_key"]

# 사이드바: API 키 입력
with st.sidebar:
    st.header("설정")
    api_key_input = st.text_input("Gemini API Key를 입력하세요", type="password")
    st.markdown("[API Key 발급받기](https://aistudio.google.com/app/apikey)")

# 메인 화면: 파일 업로드
col1, col2 = st.columns(2)
with col1:
    st.subheader("1. 카탈로그 업로드")
    catalog_file = st.file_uploader("catalog.jsonl 파일을 업로드하세요.", type=["jsonl", "txt"])

with col2:
    st.subheader("2. 대화 내역 업로드")
    chat_files = st.file_uploader("order_chat.jsonl 파일들을 업로드하세요.", type=["jsonl", "txt"], accept_multiple_files=True)

# 실행 버튼
if st.button("🚀 주문서 추출 실행", type="primary", use_container_width=True):
    if not api_key_input:
        st.warning("왼쪽 사이드바에 Gemini API Key를 입력해 주세요.")
    elif not catalog_file:
        st.warning("카탈로그 파일을 업로드해 주세요.")
    elif not chat_files:
        st.warning("대화 내역 파일을 1개 이상 업로드해 주세요.")
    else:
        with st.spinner("Gemini가 대화를 분석하고 주문서를 작성 중입니다... (데이터량에 따라 10~30초 소요)"):
            catalog_data = parse_custom_jsonl(catalog_file)
            all_extracted_orders = []

            for chat_file in chat_files:
                chat_data = parse_custom_jsonl(chat_file)
                extracted_data = extract_orders_from_chat(
                    api_key_input,
                    catalog_data,
                    chat_data,
                    model=config["gemini"]["model"],
                    temperature=config["gemini"]["temperature"],
                    prompt_template=config["prompt"]["order_extraction"],
                )

                if extracted_data:
                    ts = extract_timestamp(chat_file.name)
                    for order in extracted_data:
                        order["time"] = ts
                    all_extracted_orders.extend(extracted_data)

        if all_extracted_orders:
            df = pd.DataFrame(all_extracted_orders)
            df["phone_number"] = df["phone_number"].apply(format_phone_number)

            if juso_api_key:
                with st.spinner("우편번호를 조회 중입니다..."):
                    df["zip_code"] = df["search_address"].apply(
                        lambda addr: lookup_zip_code(addr, juso_api_key)
                    )

            df = df.reindex(columns=config["columns"])

            st.success("🎉 주문 데이터 추출이 완료되었습니다!")
            st.dataframe(df, use_container_width=True)

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl", datetime_format="YYYY-MM-DD HH:MM:SS") as writer:
                df.to_excel(writer, index=False, sheet_name=config["output"]["sheet_name"])

            st.download_button(
                label="📥 엑셀 파일(.xlsx) 다운로드",
                data=output.getvalue(),
                file_name=config["output"]["file_name"],
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )
        else:
            st.error("추출된 데이터가 없습니다. 원본 데이터나 API 상태를 확인해 주세요.")
