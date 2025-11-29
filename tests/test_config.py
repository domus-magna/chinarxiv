import os
from src.config import load_dotenv


def test_load_dotenv_override(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=bar\nBAZ=one\n")

    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAZ", raising=False)

    load_dotenv(str(env_path))
    assert os.getenv("FOO") == "bar"
    assert os.getenv("BAZ") == "one"

    env_path.write_text("FOO=new\nBAZ=two\n")

    load_dotenv(str(env_path), override=False)
    assert os.getenv("FOO") == "bar"
    assert os.getenv("BAZ") == "one"

    load_dotenv(str(env_path), override=True)
    assert os.getenv("FOO") == "new"
    assert os.getenv("BAZ") == "two"
