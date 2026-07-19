import io

import pandas as pd


def write_excel_with_text_zipcode(
    df: pd.DataFrame,
    sheet_name: str,
    zip_col: str = "우편번호",
    extra_sheets: dict[str, pd.DataFrame] | None = None,
) -> bytes:
    """DataFrame을 Excel bytes로 만들고 우편번호 컬럼을 텍스트로 지정한다."""
    output = io.BytesIO()
    with pd.ExcelWriter(
        output,
        engine="openpyxl",
        datetime_format="YYYY-MM-DD HH:MM:SS",
    ) as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        _set_text_column(writer.sheets[sheet_name], df, zip_col)

        for extra_name, extra_df in (extra_sheets or {}).items():
            extra_df.to_excel(writer, index=False, sheet_name=extra_name)
            _set_text_column(writer.sheets[extra_name], extra_df, zip_col)

    return output.getvalue()


def _set_text_column(worksheet, df: pd.DataFrame, column: str) -> None:
    if column not in df.columns:
        return
    column_index = df.columns.get_loc(column) + 1
    for row in worksheet.iter_rows(
        min_row=2,
        max_row=worksheet.max_row,
        min_col=column_index,
        max_col=column_index,
    ):
        row[0].number_format = "@"
