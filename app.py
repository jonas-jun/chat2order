from pathlib import Path

import streamlit as st
import yaml

from database import authenticate_user, get_connection, get_monthly_api_call_count
from settings import get_env, load_prompt
from ui import tab_catalog, tab_history, tab_order, tab_search, tab_zipcode
from ui.common import AppContext


@st.cache_resource
def get_db():
    url = get_env("SUPABASE_URL")
    key = get_env("SUPABASE_KEY")
    return get_connection(url, key) if url and key else None


@st.cache_data
def load_config(path: str = "config.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


@st.cache_data
def load_static_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


@st.cache_data
def cached_prompt(path: str) -> str:
    return load_prompt(path)


@st.cache_data(ttl=60)
def monthly_api_usage(user_id: str) -> int:
    connection = get_db()
    return get_monthly_api_call_count(connection, user_id) if connection else 0


def render_login(db_conn) -> None:
    _, center, _ = st.columns([1, 2, 1])
    with center:
        st.markdown(
            """
            <div style="text-align:center; padding:2rem 0;">
              <h1 style="color:#FF6B35;">Chat2Order</h1>
              <p style="color:#888;">로그인하여 시작하세요</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        with st.form("login_form"):
            email = st.text_input("이메일")
            password = st.text_input("비밀번호", type="password")
            submitted = st.form_submit_button(
                "LogIn", width="stretch", type="primary"
            )
        if not submitted:
            return
        if not db_conn:
            st.error("DB 연결이 설정되지 않았습니다. 관리자에게 문의하세요.")
            return
        account = authenticate_user(db_conn, user_id=email, password=password)
        if not account:
            st.error("이메일/비밀번호가 올바르지 않거나 비활성화된 계정입니다.")
            return
        st.session_state["api_key"] = account["gemini_api_key"]
        st.session_state["monthly_extract_limit"] = account["monthly_extract_limit"]
        st.session_state["logged_in_user"] = email
        st.rerun()


def render_sidebar(ctx: AppContext) -> None:
    with st.sidebar:
        st.write(f"👤 **{ctx.user_id}**님 환영합니다.")
        if st.button("LogOut"):
            st.session_state["logged_in_user"] = None
            st.session_state["api_key"] = None
            st.rerun()
        st.divider()
        st.header("Account State")
        if ctx.api_key:
            st.success("✅ 관리자 승인 완료")
        else:
            st.error("❌ API Key 연동 실패")

        used = monthly_api_usage(ctx.user_id) if ctx.db_conn else 0
        limit = st.session_state.get("monthly_extract_limit")
        st.info(
            f"이번 달 사용량: {used} / "
            f"{'무제한' if limit is None else limit}"
        )


def main() -> None:
    st.set_page_config(page_title="Chat2Order: Convert Chat to Order", layout="wide")
    st.markdown(
        f"<style>{load_static_text('styles/main.css')}</style>",
        unsafe_allow_html=True,
    )
    db_conn = get_db()
    st.session_state.setdefault("logged_in_user", None)
    st.session_state.setdefault("api_key", None)
    st.session_state.setdefault("monthly_extract_limit", None)

    if not st.session_state["logged_in_user"]:
        render_login(db_conn)
        st.stop()

    config = load_config()
    ctx = AppContext(
        db_conn=db_conn,
        config=config,
        api_key=st.session_state.get("api_key") or "",
        user_id=st.session_state["logged_in_user"],
        juso_api_key=get_env("JUSO_API_KEY"),
        order_extraction_prompt=cached_prompt(config["prompts"]["order_extraction"]),
        address_to_search_prompt=cached_prompt(config["prompts"]["address_to_search"]),
    )
    render_sidebar(ctx)
    st.markdown(
        "## 📦 <span style='color:#FF6B35;font-weight:bold;'>C</span>hat"
        "<span style='color:#FF6B35;font-weight:bold;'>2O</span>rder",
        unsafe_allow_html=True,
    )
    st.markdown(
        "사장님은 소통에만 집중하세요. 대화 속 주문 정리는 C2O가 알아서 엑셀로 만들어 드립니다."
    )

    tabs = st.tabs(
        [
            "📦 주문서 추출",
            "📋 카탈로그 생성",
            "📮 우편번호 추출",
            "🗂️ 나의 추출 이력",
            "🔎 채팅 검색",
        ]
    )
    renderers = (
        tab_order.render,
        tab_catalog.render,
        tab_zipcode.render,
        tab_history.render,
        tab_search.render,
    )
    for tab, renderer in zip(tabs, renderers):
        with tab:
            renderer(ctx)


if __name__ == "__main__":
    main()
