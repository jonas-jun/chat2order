import json

from services import catalog_to_list, normalize_catalog, parse_catalog_json


def test_parse_catalog_json_uses_dict_as_internal_format(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"상품": ["옵션"]}), encoding="utf-8")

    assert parse_catalog_json(path) == {"상품": ["옵션"]}


def test_legacy_catalog_list_is_normalized_only_at_boundary():
    legacy = [{"상품명": "상품", "옵션": ["옵션"]}]
    catalog = normalize_catalog(legacy)

    assert catalog == {"상품": ["옵션"]}
    assert catalog_to_list(catalog) == legacy
