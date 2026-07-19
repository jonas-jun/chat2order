import argparse
import glob
import os
import re

import pandas as pd
from tqdm import tqdm


DEFAULT_PREFIX = "이지픽_"

# 채널 공지/시스템 메시지 등 학습에서 제외할 문구
EXCLUDE_MESSAGES = [
    "'이지픽' 채널을 추가해 주셔서 감사합니다.",
    "임실장이 알려주는 명품코디 꿀 TIP!",
    "알림톡/친구톡 메시지는 관리자센터에서 확인할 수 없습니다.",
]


def normalize_multispaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_nickname(filepath: str, prefix: str = DEFAULT_PREFIX) -> str:
    filename = os.path.basename(filepath)
    return filename.removeprefix(prefix).removesuffix(".csv")


def convert_df_to_jsonl(
    df: pd.DataFrame, time_after: str = None, exclude_messages: list = None
):
    exclude_messages = exclude_messages or []
    messages = []

    if time_after:
        df["DATE"] = pd.to_datetime(df["DATE"])
        time_after_dt = pd.to_datetime(time_after)
        df = df[df["DATE"] >= time_after_dt]

    for _, data in df.iterrows():
        message = data["MESSAGE"]
        if any(message.startswith(msg) for msg in exclude_messages):
            continue
        messages.append(
            {"user": data["USER"], "message": message, "date": data["DATE"]}
        )
    return messages


def export_jsonl(data: list, out_f: str):
    with open(out_f, "w", encoding="utf-8") as f:
        for line in tqdm(data, desc="writing", mininterval=10):
            f.write(f"{line}\n")
    print(f"{len(data):,} lines exported to file: {out_f}")


def convert_folder(
    input_dir: str,
    output_dir: str,
    prefix: str = DEFAULT_PREFIX,
    time_after: str = None,
) -> None:
    os.makedirs(output_dir, exist_ok=True)
    fnames = glob.glob(os.path.join(input_dir, f"{prefix}*.csv"))
    if not fnames:
        print(f"[WARN] {input_dir} 에서 '{prefix}*.csv' 파일을 찾지 못했습니다.")
        return
    for fname in fnames:
        order_name = extract_nickname(fname, prefix=prefix)
        df = pd.read_csv(fname, encoding="utf-8-sig", encoding_errors="replace")
        end_date = df.iloc[-1]["DATE"]
        chats = convert_df_to_jsonl(
            df, time_after=time_after, exclude_messages=EXCLUDE_MESSAGES
        )
        for chat_data in chats:
            chat_data["message"] = normalize_multispaces(chat_data["message"])
        safe_end_date = str(end_date).replace(" ", "-").replace(":", "-")
        export_jsonl(
            chats, os.path.join(output_dir, f"{order_name}_{safe_end_date}.jsonl")
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="카카오톡 CSV 대화를 학습용 JSONL로 변환합니다."
    )
    parser.add_argument("--input-dir", required=True, help="CSV 파일이 있는 폴더")
    parser.add_argument("--output-dir", required=True, help="JSONL 출력 폴더")
    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"파일명 접두사 및 glob 패턴 구성에 사용 (기본값: {DEFAULT_PREFIX})",
    )
    parser.add_argument(
        "--time-after",
        default=None,
        help="이 시각 이후의 메시지만 포함 (예: '2026-07-01 00:00')",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    convert_folder(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        prefix=args.prefix,
        time_after=args.time_after,
    )


if __name__ == "__main__":
    main()
