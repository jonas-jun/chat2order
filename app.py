import streamlit as st
import pandas as pd
import json
import io
import ast
from pydantic import BaseModel
from google import genai
from google.genai import types

# --- 1. 데이터 모델 정의 (Pydantic을 이용한 Structured Output 스키마) ---
class OrderItem(BaseModel):
    name: str | None
    phone_number: str | None
    address: str | None
    product: str | None
    option: str | None
    volume: int | None

# --- 2. 유틸리티 함수: 파일 파싱 ---
def parse_custom_jsonl(uploaded_file):
    """
    작은따옴표가 포함된 파이썬 dict 형태의 텍스트 라인을 파싱합니다.
    """
    lines = uploaded_file.getvalue().decode("utf-8").splitlines()
    data = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            # 안전하게 문자열을 파이썬 딕셔너리로 변환
            parsed_dict = ast.literal_eval(line)
            data.append(parsed_dict)
        except Exception as e:
            st.warning(f"파싱 오류 발생 라인 건너뜀: {line[:30]}... ({e})")
    return data

# --- 3. 핵심 로직: Gemini API 호출 ---
def extract_orders_from_chat(api_key: str, catalog_data: list, chat_data: list):
    """
    Gemini API를 호출하여 대화에서 주문 정보를 추출합니다.
    """
    client = genai.Client(api_key=api_key)
    
    catalog_str = json.dumps(catalog_data, ensure_ascii=False, indent=2)
    chat_str = json.dumps(chat_data, ensure_ascii=False, indent=2)
    
    prompt = f"""
    너는 쇼핑몰의 메신저 대화에서 주문 데이터를 추출하는 전문가야.
    아래 제공된 '상품 카탈로그'를 기준으로, '고객 대화 내역'을 분석해서 주문자의 이름, 연락처, 주소, 주문상품, 옵션, 수량을 추출해.
    
    [상품 카탈로그]
    {catalog_str}
    
    [고객 대화 내역]
    {chat_str}
    
    지시사항:
    1. 대화 내역에서 주문이 확정된 내역(입금 완료 등)을 위주로 추출해.
    2. 상품명(product)은 반드시 카탈로그에 있는 가장 유사한 상품명으로 매핑해.
    3. 색상, 사이즈 등의 옵션(option)이 있다면 분리해서 적고, 수량(volume)은 정수로 적어.
    4. 대화에서 파악할 수 없는 정보는 null로 비워둬.
    """
    
    try:
        # Gemini 2.5 Flash 모델 사용 및 Structured Output 강제
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=list[OrderItem], # Pydantic 모델을 이용해 리스트 형태로 반환 강제
                temperature=0.1, # 일관된 결과를 위해 온도 낮춤
            ),
        )
        # JSON 문자열 응답을 파이썬 리스트/딕셔너리로 변환
        return json.loads(response.text)
    except Exception as e:
        st.error(f"Gemini API 호출 중 오류가 발생했습니다: {e}")
        return None

# --- 4. Streamlit UI 구성 ---
st.set_page_config(page_title="메신저 주문서 자동 추출기", layout="wide")

st.title("📦 Chat2Order: 메신저 주문 자동 정리기")
st.markdown("사장님은 소통에만 집중하세요. 대화 속 주문 정리는 Chat2Order가 알아서 엑셀로 만들어 드립니다.")

st.warning(
    "⚠️ **보안 안내**: 본 서비스는 프로토타입 검증 환경입니다. "
    "실제 고객의 민감한 개인정보(이름, 연락처, 주소 등)가 포함된 원본 데이터 업로드를 지양하고, "
    "**테스트용 더미 데이터**로 검증해 주세요."
)

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
            # 1. 파일 파싱
            catalog_data = parse_custom_jsonl(catalog_file)
            
            all_extracted_orders = []
            
            # 2. 업로드된 여러 개의 채팅 파일을 순회하며 추출
            for chat_file in chat_files:
                chat_data = parse_custom_jsonl(chat_file)
                extracted_data = extract_orders_from_chat(api_key_input, catalog_data, chat_data)
                
                if extracted_data:
                    all_extracted_orders.extend(extracted_data)
            
            # 3. 데이터프레임 변환 및 결과 출력
            if all_extracted_orders:
                df = pd.DataFrame(all_extracted_orders)
                
                # 열 순서 정렬 (누락된 컬럼은 빈 값으로 채움)
                expected_columns = ['name', 'phone_number', 'address', 'product', 'option', 'volume']
                df = df.reindex(columns=expected_columns)
                
                st.success("🎉 주문 데이터 추출이 완료되었습니다!")
                st.dataframe(df, use_container_width=True)
                
                # 4. 엑셀 파일로 변환 및 다운로드 버튼 생성
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False, sheet_name='주문내역')
                processed_data = output.getvalue()
                
                st.download_button(
                    label="📥 엑셀 파일(.xlsx) 다운로드",
                    data=processed_data,
                    file_name="orders_extracted.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary"
                )
            else:
                st.error("추출된 데이터가 없습니다. 원본 데이터나 API 상태를 확인해 주세요.")