from aurora.autonomy.verification import sha256_file

def test_sha256(tmp_path):
    p = tmp_path / "x"
    p.write_text("aurora", encoding="utf-8")
    assert len(sha256_file(str(p))) == 64
