from types import SimpleNamespace
from unittest.mock import Mock
import subprocess
import sys

import pytest

from build_tools import prepare_windows_cuda as cuda


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(cuda.sys, "platform", "win32")
    monkeypatch.setattr(cuda, "installed_pair", lambda: True)
    nms = Mock(return_value=SimpleNamespace(cpu=lambda: SimpleNamespace(tolist=lambda: [0])))
    torch = SimpleNamespace(__version__="2.10.0+cu128", version=SimpleNamespace(cuda="12.8"),
                            cuda=SimpleNamespace(is_available=lambda: False), tensor=Mock())
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "torchvision", SimpleNamespace(__version__="0.25.0+cu128", ops=SimpleNamespace(nms=nms)))
    return torch, nms


def test_cpu_environment_is_rejected_without_installing(monkeypatch):
    monkeypatch.setattr(cuda.sys, "platform", "win32")
    monkeypatch.setattr(cuda, "installed_pair", lambda: False)
    run = Mock()
    monkeypatch.setattr(cuda.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="Expected torch"):
        cuda.prepare()
    run.assert_not_called()


def test_install_uses_exact_cuda_wheels_and_selected_interpreter(monkeypatch, runtime):
    monkeypatch.setattr(cuda, "installed_pair", lambda: False)
    run = Mock()
    monkeypatch.setattr(cuda.subprocess, "run", run)
    cuda.prepare(install=True)
    command = run.call_args.args[0]
    assert command[:4] == [sys.executable, "-m", "pip", "install"]
    assert "torch==2.10.0+cu128" in command and "torchvision==0.25.0+cu128" in command
    assert command[-2:] == ["--index-url", "https://download.pytorch.org/whl/cu128"]
    assert run.call_args.kwargs["check"]


def test_matching_environment_does_not_reinstall(monkeypatch, runtime, capsys):
    run = Mock()
    monkeypatch.setattr(cuda.subprocess, "run", run)
    cuda.prepare(install=True)
    run.assert_not_called()
    assert "GPU inference is unverified" in capsys.readouterr().out


def test_pip_failure_stops_before_runtime_checks(monkeypatch, runtime):
    monkeypatch.setattr(cuda, "installed_pair", lambda: False)
    monkeypatch.setattr(cuda.subprocess, "run", Mock(side_effect=subprocess.CalledProcessError(1, "pip")))
    with pytest.raises(subprocess.CalledProcessError):
        cuda.prepare(install=True)
    runtime[1].assert_not_called()


def test_broken_gpu_operator_is_not_treated_as_success(runtime):
    torch, nms = runtime
    torch.cuda.is_available = lambda: True
    nms.side_effect = [nms.return_value, RuntimeError("CUDA kernel failed")]
    with pytest.raises(RuntimeError, match="CUDA kernel failed"):
        cuda.prepare()


def test_cpu_runtime_cannot_masquerade_as_cuda(runtime):
    runtime[0].version.cuda = None
    with pytest.raises(RuntimeError, match="Expected CUDA"):
        cuda.prepare()
