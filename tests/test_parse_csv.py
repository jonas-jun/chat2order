from services import parse_csv


CSV_HEADER = "DATE,USER,MESSAGE\n"


def _write_csv(tmp_path, body: str):
    path = tmp_path / "chat.csv"
    path.write_text(CSV_HEADER + body, encoding="utf-8")
    return path


def test_multiline_message_linebreak_preserved(tmp_path):
    body = (
        '2026-07-12 10:00:00,customer,"드래곤백 레드 1\n'
        '드래곤 진브라운 1"\n'
    )
    path = _write_csv(tmp_path, body)

    messages, _ = parse_csv(path, filename_prefix="", exclude_messages=[])

    assert messages[0]["message"] == "드래곤백 레드 1\n드래곤 진브라운 1"


def test_internal_spaces_and_tabs_normalized(tmp_path):
    body = '2026-07-12 10:01:00,customer,"안녕하세요   \t문의드립니다"\n'
    path = _write_csv(tmp_path, body)

    messages, _ = parse_csv(path, filename_prefix="", exclude_messages=[])

    assert messages[0]["message"] == "안녕하세요 문의드립니다"


def test_leading_trailing_blank_lines_stripped(tmp_path):
    body = '2026-07-12 10:02:00,customer,"\n드래곤백 레드 1\n\n"\n'
    path = _write_csv(tmp_path, body)

    messages, _ = parse_csv(path, filename_prefix="", exclude_messages=[])

    assert messages[0]["message"] == "드래곤백 레드 1"


def test_exclude_messages_filter_still_works(tmp_path):
    body = (
        "2026-07-12 10:00:00,customer,\"'이지픽' 채널을 추가해 주셔서 감사합니다.\"\n"
        '2026-07-12 10:01:00,customer,"드래곤백 레드 1"\n'
    )
    path = _write_csv(tmp_path, body)

    messages, _ = parse_csv(
        path,
        filename_prefix="",
        exclude_messages=["'이지픽' 채널을 추가해 주셔서 감사합니다."],
    )

    assert len(messages) == 1
    assert messages[0]["message"] == "드래곤백 레드 1"
