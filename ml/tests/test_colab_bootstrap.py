"""Kiểm tra bootstrap/ZIP bằng runtime giả lập; không chạy training trên Windows."""

import ast
import runpy
import sys
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import nbformat
import pytest

import colab_runtime
from package_colab import source_files, write_archive

ML_ROOT = Path(__file__).resolve().parents[1]
TASKS = ["leaf-disease", "fruit-health", "weather"]


def notebook(task):
    return nbformat.read(next((ML_ROOT / task / "notebooks").glob("*.ipynb")), as_version=4)


def bootstrap(task):
    return next(cell.source for cell in notebook(task).cells if cell.cell_type == "code")


@pytest.mark.parametrize("task", TASKS)
def test_notebook_valid_and_matches_builder(task):
    nb = notebook(task)
    nbformat.validate(nb)
    ns = runpy.run_path(str(ML_ROOT / task / "scripts/build_notebook.py"))
    built = ns["build_notebook"]()["cells"] if "build_notebook" in ns else ns["cells"]
    assert len(built) == len(nb.cells)
    for expected, actual in zip(built, nb.cells):
        assert "".join(expected["source"]) == actual.source
        if actual.cell_type == "code":
            ast.parse(actual.source)


@pytest.mark.parametrize("task", TASKS)
def test_notebook_rejects_windows_before_accessing_drive(task, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="hosted runtime"):
        exec(bootstrap(task), {})


@pytest.mark.parametrize("task", TASKS)
def test_bootstrap_mount_extract_and_rerun(task, tmp_path, monkeypatch):
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    with zipfile.ZipFile(drive_root / "aiot-ml-colab.zip", "w") as archive:
        archive.writestr(f"{task}/README.md", "source")
        archive.writestr("colab_runtime.py", "# fake runtime injected by test")
    calls = []
    fake_google, fake_colab = ModuleType("google"), ModuleType("google.colab")
    fake_colab.drive = SimpleNamespace(mount=lambda path: calls.append(("mount", path)))
    fake_google.colab = fake_colab
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.colab", fake_colab)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setenv("COLAB_DRIVE_ROOT", str(drive_root))
    monkeypatch.delenv("FRUIT_OUTPUT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    real_import = __import__("importlib").import_module

    def import_module(name):
        if name != "colab_runtime":
            return real_import(name)
        return SimpleNamespace(prepare_runtime=lambda root, **kwargs: calls.append(("prepare", kwargs)),
                               extract_zip_once=colab_runtime.extract_zip_once)

    monkeypatch.setattr("importlib.import_module", import_module)
    # Helper cũ trong process test không phải module được load trong kernel notebook.
    monkeypatch.delitem(sys.modules, "colab_runtime")
    content = tmp_path / "content"
    source = bootstrap(task).replace('Path("/content")', f"Path({str(content)!r})")
    context = {}
    exec(source, context)
    assert context["CODE_ROOT"].is_dir()
    assert (context["ML_ROOT"] / ".ready").is_file()
    assert calls[-1] == ("prepare", {"require_gpu": task != "weather", "install": True})
    exec(source, context)  # Không giải nén lại hoặc ghi đè source hoàn tất.
    assert len([call for call in calls if call[0] == "prepare"]) == 2


def test_source_zip_allowlist_and_idempotent_packaging(tmp_path):
    files = source_files(ML_ROOT)
    archive_path = tmp_path / "source.zip"
    first = write_archive(archive_path, files, ML_ROOT)
    assert first == write_archive(archive_path, files, ML_ROOT)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert "colab_runtime.py" in names and "requirements-colab.lock" in names
        assert sum(name.endswith(".ipynb") for name in names) == 3
        for name in names:
            assert not set(Path(name).parts) & {".venv", "data", "outputs", "checkpoints", "dist"}
            assert not name.endswith((".pt", ".pth", ".env", ".pyc"))
    with pytest.raises(FileExistsError):
        write_archive(archive_path, files[:-1], ML_ROOT)


@pytest.mark.parametrize("filename", ["../escape.txt", "/absolute.txt", "folder\\escape.txt"])
def test_dataset_zip_rejects_unsafe_paths(tmp_path, filename):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        entry = zipfile.ZipInfo(filename)
        entry.filename = filename  # Giữ tên ZIP nguyên gốc, không chuẩn hóa dấu trên Windows.
        archive.writestr(entry, "invalid")
    with pytest.raises(ValueError):
        colab_runtime.extract_zip_once(path, tmp_path / "data")


def test_dataset_zip_reuse_changed_archive_and_partial_extraction(tmp_path):
    archive_path, destination = tmp_path / "data.zip", tmp_path / "data"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("images/image.txt", "original")
    colab_runtime.extract_zip_once(archive_path, destination)
    assert colab_runtime.extract_zip_once(archive_path, destination) == destination.resolve()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("images/image.txt", "changed")
    with pytest.raises(FileExistsError):
        colab_runtime.extract_zip_once(archive_path, destination)
    assert (destination / "images/image.txt").read_text() == "original"
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "incomplete.txt").write_text("partial")
    with pytest.raises(FileExistsError):
        colab_runtime.extract_zip_once(archive_path, partial)


def test_locks_never_install_torch_or_cuda_stack():
    for name in ("requirements-colab.lock", "requirements-dev.lock"):
        packages = [line.split("==")[0] for line in (ML_ROOT / name).read_text().splitlines()
                    if line and not line.lstrip().startswith("#")]
        assert not any(name.startswith(("torch", "nvidia-", "triton")) for name in packages)


def test_runtime_rejects_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="hosted runtime"):
        colab_runtime.prepare_runtime(ML_ROOT, require_gpu=True)


def test_dependency_installer_preserves_torch_and_requests_restart(tmp_path, monkeypatch):
    (tmp_path / "requirements-colab.lock").write_text("numpy==2.1.3\n")
    versions = {"torch": "2.6.0+cu124", "torchvision": "0.21.0+cu124", "numpy": "2.0.0"}
    monkeypatch.setattr(colab_runtime, "require_colab", lambda: None)
    monkeypatch.setattr(colab_runtime.metadata, "version", lambda name: versions[name])
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(__version__="2.0.0"))
    calls = []

    def install(command):
        calls.append(command)
        versions["numpy"] = "2.1.3"

    monkeypatch.setattr(colab_runtime.subprocess, "check_call", install)
    with pytest.raises(RuntimeError, match="Restart session"):
        colab_runtime.prepare_runtime(tmp_path, require_gpu=True)
    assert len(calls) == 1 and "--no-deps" in calls[0]
    assert versions["torch"] == "2.6.0+cu124"
    assert versions["torchvision"] == "0.21.0+cu124"
