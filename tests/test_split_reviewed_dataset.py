import json

from split_reviewed_dataset import split_dataset


def test_same_content_hash_is_always_in_same_split(tmp_path):
    examples = tmp_path / "examples.jsonl"
    manifest = tmp_path / "manifest.jsonl"
    examples.write_text(
        "\n".join(
            json.dumps({"messages": [], "target": {"items": []}})
            for _ in range(3)
        )
        + "\n"
    )
    manifest.write_text(
        "\n".join(
            json.dumps(
                {"example_id": str(index), "canonical_chat_hash": content_hash}
            )
            for index, content_hash in enumerate(("same", "same", "different"))
        )
        + "\n"
    )

    split_dataset(examples, manifest, tmp_path / "output", seed="fixed")

    rows = [
        json.loads(line)
        for line in (tmp_path / "output" / "split_manifest.jsonl")
        .read_text()
        .splitlines()
    ]
    assert rows[0]["split"] == rows[1]["split"]


def test_reconstructed_silver_is_forced_to_train(tmp_path):
    examples = tmp_path / "examples.jsonl"
    manifest = tmp_path / "manifest.jsonl"
    examples.write_text(
        json.dumps({"messages": [], "target": {"items": []}}) + "\n"
    )
    manifest.write_text(
        json.dumps(
            {
                "example_id": "one",
                "canonical_chat_hash": "content",
                "decision": "reconstructed_marker_hints",
            }
        )
        + "\n"
    )

    split_dataset(examples, manifest, tmp_path / "output", seed="fixed")

    row = json.loads(
        (tmp_path / "output" / "split_manifest.jsonl").read_text().strip()
    )
    assert row["split"] == "train"
