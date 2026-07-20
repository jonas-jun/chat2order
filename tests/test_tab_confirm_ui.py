from streamlit.testing.v1 import AppTest


def _review_app(raw_product="드래곤"):
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
        "catalog_json": {
            "드래곤백": ["레드"],
            "드래곤 트리플백": ["카멜"],
        },
        "chat_json": [
            {
                "user": "customer",
                "message": f"[주문완료] {raw_product} 레드 1",
            }
        ],
        "predicted_json": {
            "order_name": None,
            "phone_number": None,
            "address": None,
            "search_address": None,
            "items": [
                {"raw_product": raw_product, "raw_option": "레드", "volume": 1}
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
    assert len(app.selectbox) == 4
    assert app.selectbox[0].label == "보완할 추출 작업"
    assert app.selectbox[1].label == "보완할 채팅 파일"
    assert any(button.label == "이 파일 보완 완료" for button in app.button)
    assert any(
        button.label == "🔄 보완 완료 및 주문서 다시 생성"
        for button in app.button
    )


def test_confirm_tab_can_complete_valid_file():
    app = AppTest.from_function(_review_app).run()
    complete = next(
        button for button in app.button if button.label == "이 파일 보완 완료"
    )

    app = complete.click().run()

    assert not app.exception
    assert "확인 완료" in app.selectbox[1].options[0]


def test_confirm_tab_hides_exact_items_from_editor():
    app = AppTest.from_function(_review_app, args=("드래곤백",)).run()

    assert not app.exception
    assert len(app.selectbox) == 1
    assert any("보완할 항목이 없습니다" in success.value for success in app.success)
    assert not any(button.label == "이 파일 보완 완료" for button in app.button)


def test_product_selection_limits_option_choices():
    app = AppTest.from_function(_review_app).run()
    product = next(box for box in app.selectbox if box.label == "상품명")

    app = product.select("드래곤 트리플백").run()

    assert not app.exception
    option = next(box for box in app.selectbox if box.label == "옵션명")
    assert option.options == ["", "카멜"]
    assert option.value == "카멜"
