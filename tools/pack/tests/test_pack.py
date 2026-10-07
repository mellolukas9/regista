"""regista-pack end to end: keys, builds (offline, with local wheels) and verification."""

import json
import uuid
import zipfile
from pathlib import Path

import pytest

from regista_pack import build as builder
from regista_pack import keys
from regista_pack.cli import main
from regista_pack.keys import ENV_PASSPHRASE, PackError
from regista_pkg import PackageError, parse_signature_doc
from regista_pkg import archive as arch

from .support import make_playwright_wheel, make_robot, make_wheel

PASSWORD = "uma-senha-bem-longa-e-unica"
CLIENT_A = str(uuid.uuid4())
CLIENT_B = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def passphrase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PASSPHRASE, PASSWORD)


@pytest.fixture
def key_files(tmp_path: Path) -> tuple[Path, Path, str]:
    return keys.generate(tmp_path / "keys", "ativa", PASSWORD.encode())


def run(*args: str) -> int:
    with pytest.raises(SystemExit) as caught:
        main(list(args))
    return int(caught.value.code or 0)


def build_cli(
    robot: Path, key: Path, out: Path, *extra: str, clients: tuple[str, ...] = (CLIENT_A,)
) -> int:
    client_args = [a for c in clients for a in ("--client", c)]
    return run(
        "build", str(robot), "--version", "1.2.0", "--key", str(key), "--out", str(out),
        "--python", "3.13.5", *client_args, *extra,
    )  # fmt: skip


# --- keys --------------------------------------------------------------------------------------


def test_keygen_writes_an_encrypted_private_key_and_a_public_file(
    tmp_path: Path, key_files: tuple[Path, Path, str]
) -> None:
    private, public, key_id = key_files
    assert b"ENCRYPTED PRIVATE KEY" in private.read_bytes()
    assert list(json.loads(public.read_text("utf-8"))) == [key_id]
    key, loaded_id = keys.load_private(private, PASSWORD.encode())
    assert loaded_id == key_id and key is not None


def test_a_wrong_passphrase_does_not_open_the_key(key_files: tuple[Path, Path, str]) -> None:
    with pytest.raises(PackError, match="Senha errada"):
        keys.load_private(key_files[0], b"another-long-passphrase")


def test_keygen_never_overwrites_and_needs_a_real_passphrase(tmp_path: Path) -> None:
    keys.generate(tmp_path / "k", "a", PASSWORD.encode())
    with pytest.raises(PackError, match="vou sobrescrever"):
        keys.generate(tmp_path / "k", "a", PASSWORD.encode())
    with pytest.raises(PackError, match="12 caracteres"):
        keys.passphrase(confirm=False, env={ENV_PASSPHRASE: "short"})


def test_keygen_refuses_a_folder_inside_a_repository(tmp_path: Path) -> None:
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(PackError, match="repositório"):
        keys.generate(tmp_path / "repo" / "keys", "a", PASSWORD.encode())
    assert not (tmp_path / "repo" / "keys").exists()


def test_the_cli_makes_keys(tmp_path: Path) -> None:
    assert run("keygen", "--out", str(tmp_path / "k"), "--name", "reserva") == 0
    assert (tmp_path / "k" / "reserva.rgkey").is_file()


# --- build -------------------------------------------------------------------------------------


def test_a_robot_without_dependencies_builds_signs_and_verifies(
    tmp_path: Path, key_files: tuple[Path, Path, str]
) -> None:
    private, public, key_id = key_files
    robot = make_robot(tmp_path, "demo_robot")
    out = tmp_path / "dist"
    assert build_cli(robot, private, out) == 0
    package, sig = (
        out / f"demo_robot-1.2.0-{CLIENT_A[:8]}.rgpkg",
        out / (f"demo_robot-1.2.0-{CLIENT_A[:8]}.rgsig"),
    )
    assert package.is_file() and sig.is_file()
    signed, _ = parse_signature_doc(sig.read_bytes())
    assert signed.key_id == key_id and signed.manifest.tenant_id == CLIENT_A
    assert signed.manifest.package_name == "demo_robot" and signed.manifest.version == "1.2.0"
    assert signed.manifest.python == "3.13.5" and signed.manifest.playwright is None
    with zipfile.ZipFile(package) as zf:
        assert sorted(zf.namelist()) == [
            "bot/main.py",
            "manifest.json",
            "requirements.lock",
        ]
    assert run("verify", str(package), str(sig), "--keys", str(public)) == 0


def test_the_same_input_builds_the_same_bytes(
    tmp_path: Path, key_files: tuple[Path, Path, str]
) -> None:
    robot = make_robot(tmp_path, "demo_robot")
    build_cli(robot, key_files[0], tmp_path / "one")
    build_cli(robot, key_files[0], tmp_path / "two")
    name = f"demo_robot-1.2.0-{CLIENT_A[:8]}.rgpkg"
    assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes()


def test_dependencies_go_inside_with_hashes_and_the_runtime_is_declared(
    tmp_path: Path, key_files: tuple[Path, Path, str]
) -> None:
    index = tmp_path / "index"
    make_wheel(index, "helperlib", "2.0", requires=("subdep>=1",))
    make_wheel(index, "subdep", "1.5")
    make_playwright_wheel(index, "1.55.0", "1187")
    robot = make_robot(tmp_path, "demo_robot", requirements="helperlib\nplaywright==1.55.0\n")
    out = tmp_path / "dist"
    assert build_cli(robot, key_files[0], out, "--find-links", str(index), "--no-index") == 0

    package = out / f"demo_robot-1.2.0-{CLIENT_A[:8]}.rgpkg"
    with zipfile.ZipFile(package) as zf:
        names = set(zf.namelist())
        lock = zf.read("requirements.lock").decode()
    assert {
        "wheels/helperlib-2.0-py3-none-any.whl",
        "wheels/subdep-1.5-py3-none-any.whl",
        "wheels/playwright-1.55.0-py3-none-win_amd64.whl",
    } <= names
    assert "helperlib==2.0" in lock and "--hash=sha256:" in lock  # pinned, with hashes
    signed, _ = parse_signature_doc((out / package.name.replace(".rgpkg", ".rgsig")).read_bytes())
    assert signed.manifest.playwright == "1.55.0" and signed.manifest.chromium_revision == "1187"


def test_one_package_per_client_with_the_same_wheels(
    tmp_path: Path, key_files: tuple[Path, Path, str]
) -> None:
    index = tmp_path / "index"
    make_wheel(index, "helperlib", "2.0")
    robot = make_robot(tmp_path, "demo_robot", requirements="helperlib\n")
    out = tmp_path / "dist"
    assert (
        build_cli(
            robot,
            key_files[0],
            out,
            "--find-links",
            str(index),
            "--no-index",
            clients=(CLIENT_A, CLIENT_B),
        )
        == 0
    )
    a = out / f"demo_robot-1.2.0-{CLIENT_A[:8]}.rgpkg"
    b = out / f"demo_robot-1.2.0-{CLIENT_B[:8]}.rgpkg"
    assert a.read_bytes() != b.read_bytes()
    with zipfile.ZipFile(a) as za, zipfile.ZipFile(b) as zb:
        wheels_a = {n: za.read(n) for n in za.namelist() if n.startswith("wheels/")}
        wheels_b = {n: zb.read(n) for n in zb.namelist() if n.startswith("wheels/")}
        assert wheels_a == wheels_b and wheels_a
        assert arch.read_inner_manifest(za).tenant_id == CLIENT_A
        assert arch.read_inner_manifest(zb).tenant_id == CLIENT_B
    for client in (CLIENT_A, CLIENT_B):
        sig = out / f"demo_robot-1.2.0-{client[:8]}.rgsig"
        signed, _ = parse_signature_doc(sig.read_bytes())
        assert signed.manifest.tenant_id == client


def test_a_dependency_with_only_source_code_fails_naming_the_package(
    tmp_path: Path, key_files: tuple[Path, Path, str]
) -> None:
    index = tmp_path / "index"
    index.mkdir()
    (index / "sdistonly-1.0.tar.gz").write_bytes(b"not really a source distribution")
    robot = make_robot(tmp_path, "demo_robot", requirements="sdistonly\n")
    out = tmp_path / "dist"
    with pytest.raises(PackError) as caught:
        builder.build(
            robot,
            version="1.0.0",
            clients=[CLIENT_A],
            key=keys.load_private(key_files[0], PASSWORD.encode())[0],
            key_id=key_files[2],
            out_dir=out,
            python="3.13.5",
            find_links=index,
            no_index=True,
        )
    message = str(caught.value)
    assert "sdistonly" in message and "wheel" in message and "win_amd64" in message
    assert not list(out.glob("*.rgpkg")), "no package is left behind"


def test_compiled_files_and_caches_stay_out_of_the_package(
    tmp_path: Path, key_files: tuple[Path, Path, str]
) -> None:
    robot = make_robot(tmp_path, "demo_robot")
    (robot / "__pycache__").mkdir()
    (robot / "__pycache__" / "main.cpython-313.pyc").write_bytes(b"x")
    (robot / "helper.py").write_text("x = 1\n", encoding="utf-8")
    build_cli(robot, key_files[0], tmp_path / "dist")
    with zipfile.ZipFile(tmp_path / "dist" / f"demo_robot-1.2.0-{CLIENT_A[:8]}.rgpkg") as zf:
        assert "bot/helper.py" in zf.namelist()
        assert not [n for n in zf.namelist() if "pycache" in n or n.endswith(".pyc")]


@pytest.mark.parametrize(
    ("python", "message"),
    [("3.13", "exato"), ("latest", "exato")],
)
def test_the_python_must_be_exact(
    tmp_path: Path, key_files: tuple[Path, Path, str], python: str, message: str
) -> None:
    robot = make_robot(tmp_path, "demo_robot")
    with pytest.raises(PackError, match=message):
        builder.python_of(robot, python)
    with pytest.raises(PackError, match=message):
        builder.python_of(robot, None)  # and there is no .python-version


def test_python_version_file_is_used_when_there_is_no_option(tmp_path: Path) -> None:
    robot = make_robot(tmp_path, "demo_robot")
    (robot / ".python-version").write_text("3.13.5\n", encoding="utf-8")
    assert builder.python_of(robot, None) == "3.13.5"
    (robot / ".python-version").write_text("3.13\n", encoding="utf-8")
    with pytest.raises(PackError):
        builder.python_of(robot, None)


def test_bad_inputs_are_explained(tmp_path: Path, key_files: tuple[Path, Path, str]) -> None:
    key = keys.load_private(key_files[0], PASSWORD.encode())[0]

    def attempt(robot: Path, clients: list[str]) -> None:
        builder.build(
            robot, version="1.0.0", clients=clients, key=key, key_id=key_files[2],
            out_dir=tmp_path / "dist", python="3.13.5",
        )  # fmt: skip

    with pytest.raises(PackError, match="nome do pacote"):
        attempt(make_robot(tmp_path, "Bad-Name"), [CLIENT_A])
    empty = tmp_path / "no_main"
    empty.mkdir()
    with pytest.raises(PackError, match=r"main\.py"):
        attempt(empty, [CLIENT_A])
    ok = make_robot(tmp_path, "fine_robot")
    with pytest.raises(PackError, match="--client"):
        attempt(ok, [])
    with pytest.raises(PackError, match="não é o id de um cliente"):
        attempt(ok, ["not-a-uuid"])


# --- verify ------------------------------------------------------------------------------------


@pytest.fixture
def built(tmp_path: Path, key_files: tuple[Path, Path, str]) -> tuple[Path, Path, Path]:
    robot = make_robot(tmp_path, "demo_robot")
    build_cli(robot, key_files[0], tmp_path / "dist")
    stem = tmp_path / "dist" / f"demo_robot-1.2.0-{CLIENT_A[:8]}"
    return Path(str(stem) + ".rgpkg"), Path(str(stem) + ".rgsig"), key_files[1]


def test_verify_refuses_a_key_that_is_not_trusted(
    built: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    package, sig, _ = built  # no --keys: the compiled-in list is empty
    assert run("verify", str(package), str(sig)) == 1
    assert "unknown_key" in capsys.readouterr().out


def test_verify_catches_a_changed_package(
    built: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    package, sig, public = built
    data = bytearray(package.read_bytes())
    data[len(data) // 2] ^= 0xFF
    package.write_bytes(bytes(data))
    assert run("verify", str(package), str(sig), "--keys", str(public)) == 1
    assert "hash_mismatch" in capsys.readouterr().out


def test_verify_catches_a_changed_signature_document(
    built: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    package, sig, public = built
    doc = json.loads(sig.read_text("utf-8"))
    doc["manifest"]["version"] = "9.9.9"
    sig.write_text(json.dumps(doc), encoding="utf-8")
    assert run("verify", str(package), str(sig), "--keys", str(public)) == 1
    assert "signature_invalid" in capsys.readouterr().out


def test_verify_catches_a_manifest_inside_that_differs_from_the_signed_one(
    tmp_path: Path, key_files: tuple[Path, Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """Swap the inner manifest and re-sign the new bytes: the signature is valid but the signed
    manifest and the inner one must still agree, which only the real build guarantees."""
    robot = make_robot(tmp_path, "demo_robot")
    build_cli(robot, key_files[0], tmp_path / "dist")
    stem = tmp_path / "dist" / f"demo_robot-1.2.0-{CLIENT_A[:8]}"
    package, sig = Path(str(stem) + ".rgpkg"), Path(str(stem) + ".rgsig")
    signed, _ = parse_signature_doc(sig.read_bytes())
    other = signed.manifest.__class__(
        tenant_id=CLIENT_B,
        package_name=signed.manifest.package_name,
        version=signed.manifest.version,
        python=signed.manifest.python,
    )
    with zipfile.ZipFile(package) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    members["manifest.json"] = other.to_json()
    with zipfile.ZipFile(package, "w") as zf:
        for name, content in members.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o100644 << 16
            zf.writestr(info, content)
    import hashlib

    from regista_pkg import sign

    data = package.read_bytes()
    key = keys.load_private(key_files[0], PASSWORD.encode())[0]
    sig.write_bytes(
        sign(
            key,
            signed.manifest,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            key_id=key_files[2],
        )
    )
    assert run("verify", str(package), str(sig), "--keys", str(key_files[1])) == 1
    assert "manifest" in capsys.readouterr().out


def test_nothing_the_tool_builds_would_be_refused_by_the_agent(
    built: tuple[Path, Path, Path],
) -> None:
    package, _, _ = built
    with arch.open_package(package) as zf:
        try:
            arch.validate_members(zf)
            arch.require_layout(zf)
        except PackageError as exc:  # pragma: no cover
            pytest.fail(f"refused: {exc}")
