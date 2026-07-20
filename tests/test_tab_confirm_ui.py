from streamlit.testing.v1 import AppTest


def _review_app():
    import ui.tab_confirm as tab
    from ui.common import AppContext

    job = {
        "id": "job-1",
        "title": "20260720_100000",
        "created_at": "2026-07-20T10:00:00",
        "review_status": "pending",
        "current_confirmed_revision": None,
        "total_orders": 1,
    }
    record = {
        "id": "td-1",
        "job_id": "job-1",
        "chat_filename": "이지픽_고객.csv",
        "catalog_json": {"드래곤백": ["레드"]},
        "chat_json": [
            {"user": "customer", "message": "[주문완료] 드래곤백 레드 1"}
        ],
        "predicted_json": {
            "order_name": None,
            "phone_number": None,
            "address": None,
            "search_address": None,
            "items": [
                {"raw_product": "드래곤백", "raw_option": "레드", "volume": 1}
            ],
        },
        "created_at": "2026-07-20T10:00:00",
    }
    tab.get_jobs_by_user = lambda *args, **kwargs: [job]
    tab.get_training_records_by_job = lambda *args, **kwargs: [record]
    tab.get_latest_review_version = lambda *args, **kwargs: None
    tab.get_review_draft = lambda *args, **kwargs: None
    tab.get_orders_by_job = lambda *args, **kwargs: []
    tab.save_review_draft = lambda *args, **kwargs: None

    tab.render(
        AppContext(
            db_conn=object(),
            config={
                "csv": {"filename_prefix": "이지픽_"},
                "output": {"sheet_name": "주문내역"},
                "output_columns": {
                    "주문번호": "order_number",
                    "상품명": "product",
                    "옵션명": "option",
                    "수량": "volume",
                    "채팅명": "chat_name",
                    "수령자": "order_name",
                    "전화번호": "phone_number",
                    "주소": "address",
                    "우편번호": "zip_code",
                },
            },
            api_key="",
            user_id="owner@example.com",
            juso_api_key="",
            order_extraction_prompt="",
            address_to_search_prompt="",
        )
    )


def test_confirm_tab_initial_render_smoke():
    app = AppTest.from_function(_review_app).run()

    assert not app.exception
    assert len(app.selectbox) == 2
    assert app.selectbox[0].label == "검수할 추출 작업"
    assert app.selectbox[1].label == "검수할 채팅 파일"
    assert any(button.label == "이 파일 확인 완료" for button in app.button)
    assert any(button.label == "✅ 작업 전체 확정" for button in app.button)


def test_confirm_tab_can_complete_valid_file():
    app = AppTest.from_function(_review_app).run()
    complete = next(
        button for button in app.button if button.label == "이 파일 확인 완료"
    )

    app = complete.click().run()

    assert not app.exception
    assert "예측 승인" in app.selectbox[1].options[0]
