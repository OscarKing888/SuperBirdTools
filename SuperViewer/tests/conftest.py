"""SuperViewer tests: never read or write the user's saved model chain."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_model_chain_state(monkeypatch, tmp_path):
    from SuperViewer.superviewer import model_chain_state

    monkeypatch.setattr(model_chain_state, "default_path", lambda: tmp_path / "model_chain.json")
