from tqdm import tqdm
import pandas as pd
import re
import os
import glob


def normalize_multispaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


PREFIX = "이지픽_"


def extract_nickname(filepath: str, prefix=PREFIX) -> str:
    filename = os.path.basename(filepath)
    name = filename.removeprefix(prefix).removesuffix(".csv")
    return name


def convert_df_to_jsonl(
    df: pd.DataFrame, time_after: str = None, exclude_messages: list = list()
):
    messages = list()

    if time_after:
        df["DATE"] = pd.to_datetime(df["DATE"])
        time_after_dt = pd.to_datetime(time_after)
        df = df[df["DATE"] >= time_after_dt]

    for i, data in df.iterrows():
        user = data["USER"]
        message = data["MESSAGE"]
        date = data["DATE"]
        if any(message.startswith(msg) for msg in exclude_messages):
            continue
        messages.append({"user": user, "message": message, "date": date})
    return messages


def export_jsonl(data: list, out_f: str):
    with open(out_f, "w", encoding="utf-8") as f:
        for line in tqdm(data, desc=f"writing", mininterval=10):
            f.write(f"{line}\n")
    print(f"{len(data):,} lines exported to file: {out_f}")


exclude_messages = [
    "'이지픽' 채널을 추가해 주셔서 감사합니다.",
    "임실장이 알려주는 명품코디 꿀 TIP!",
    "알림톡/친구톡 메시지는 관리자센터에서 확인할 수 없습니다.",
]


def main():
    folder = "/home/jonas/workspace/git/test"
    fnames = glob.glob(os.path.join(folder, "이지픽_*.csv"))
    for fname in fnames:
        order_name = extract_nickname(fname)
        df = pd.read_csv(fname, encoding="utf-8-sig", encoding_errors="replace")
        start_date, end_date = df.iloc[0]["DATE"], df.iloc[-1]["DATE"]
        chats = convert_df_to_jsonl(df, exclude_messages=exclude_messages)
        for chat_data in chats:
            chat_data["message"] = normalize_multispaces(chat_data["message"])
        safe_end_date = end_date.replace(" ", "-").replace(":", "-")
        export_jsonl(
            chats,
            os.path.join(
                "/home/jonas/workspace/git/test/chat_data",
                f"{order_name}_{safe_end_date}.jsonl",
            ),
        )


if __name__ == "__main__":
    main()
