import hashlib
import re

from modelforge_lineage.canonical import canonical_json, sha256_hex
from modelforge_lineage.ids import new_id


def test_canonical_json_is_sorted_compact_and_unicode() -> None:
    assert (
        canonical_json({"b": 1, "a": [1, {"d": None, "c": "é"}]})
        == '{"a":[1,{"c":"é","d":null}],"b":1}'
    )
    assert sha256_hex("abc") == hashlib.sha256(b"abc").hexdigest()


def test_new_id_shape() -> None:
    assert re.fullmatch(r"run_[0-9a-f]{12}", new_id("run"))
