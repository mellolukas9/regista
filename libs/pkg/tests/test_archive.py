"""A package is read as hostile input: zip slip, links, bombs and Windows tricks are refused."""

import io
import stat
import uuid
import zipfile
from pathlib import Path

import pytest

from regista_pkg import Manifest, PackageError
from regista_pkg import archive as arch

MANIFEST = Manifest(
    tenant_id=str(uuid.uuid4()), package_name="demo", version="1.0.0", python="3.13.5"
)


def _zip(
    members: dict[str, bytes | tuple[bytes, int]] | None = None,
    *,
    base: bool = True,
    compression: int = zipfile.ZIP_DEFLATED,
) -> zipfile.ZipFile:
    """An in-memory package. A value may be `(content, external_attr)` to craft odd entries."""
    entries: dict[str, bytes | tuple[bytes, int]] = {}
    if base:
        entries.update(
            {
                "manifest.json": MANIFEST.to_json(),
                "requirements.lock": b"",
                "bot/main.py": b"print('hi')\n",
            }
        )
    entries.update(members or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as zf:
        for name, value in entries.items():
            content, attr = value if isinstance(value, tuple) else (value, 0o100644 << 16)
            info = zipfile.ZipInfo(name)
            info.external_attr = attr
            info.compress_type = compression
            zf.writestr(info, content)
    return zipfile.ZipFile(io.BytesIO(buffer.getvalue()))


def _reason(zf: zipfile.ZipFile, tmp_path: Path) -> str:
    with pytest.raises(PackageError) as caught:
        arch.safe_extract(zf, tmp_path / "out")
    return caught.value.reason


def test_a_good_package_extracts(tmp_path: Path) -> None:
    zf = _zip({"bot/sub/helper.py": b"x = 1\n", "wheels/dep-1.0-py3-none-any.whl": b"w"})
    arch.safe_extract(zf, tmp_path / "out")
    assert (tmp_path / "out" / "bot" / "main.py").read_bytes() == b"print('hi')\n"
    assert (tmp_path / "out" / "bot" / "sub" / "helper.py").is_file()
    assert (tmp_path / "out" / "wheels" / "dep-1.0-py3-none-any.whl").is_file()
    assert arch.read_inner_manifest(zf) == MANIFEST


@pytest.mark.parametrize(
    "name",
    [
        "../evil.py",
        "bot/../../evil.py",
        "bot/../../../Windows/evil.dll",
        "/etc/cron.d/evil",
        "\\Windows\\evil.dll",
        "bot\\..\\..\\evil.py",
        "C:/Windows/evil.dll",
        "C:evil.dll",
        "bot/main.py:stream",  # an NTFS alternate data stream
        "bot//double.py",
        "bot/./dot.py",
        "bot/con",
        "bot/NUL.txt",
        "bot/com1.py",
        "bot/trailing.",
        "bot/space ",
        "bot/ctrl\x01.py",
        "bot/" + "x" * 300,
    ],
)
def test_unsafe_names_are_refused(name: str, tmp_path: Path) -> None:
    assert _reason(_zip({name: b"evil"}), tmp_path) == "unsafe_archive"
    assert not (tmp_path / "evil.py").exists()
    assert not (tmp_path.parent / "evil.py").exists()


def test_a_symlink_is_refused(tmp_path: Path) -> None:
    link = (stat.S_IFLNK | 0o777) << 16
    assert _reason(_zip({"bot/link": (b"/etc/passwd", link)}), tmp_path) == "unsafe_archive"


def test_a_device_file_is_refused(tmp_path: Path) -> None:
    device = (stat.S_IFCHR | 0o666) << 16
    assert _reason(_zip({"bot/dev": (b"", device)}), tmp_path) == "unsafe_archive"


def test_names_that_differ_only_by_case_are_refused(tmp_path: Path) -> None:
    zf = _zip({"bot/Helper.py": b"a", "bot/helper.py": b"b"})
    assert _reason(zf, tmp_path) == "unsafe_archive"


def test_a_name_cannot_be_a_file_and_a_folder(tmp_path: Path) -> None:
    zf = _zip({"bot/thing": b"a", "bot/thing/inner.py": b"b"})
    assert _reason(zf, tmp_path) == "unsafe_archive"


def test_the_same_member_twice_is_refused(tmp_path: Path) -> None:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile itself warns about duplicate names
        zf = _zip({"bot/a.py": b"1"})
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as out:
            for name in (
                "manifest.json",
                "requirements.lock",
                "bot/main.py",
                "bot/a.py",
                "bot/a.py",
            ):
                out.writestr(name, b"x" if name != "manifest.json" else MANIFEST.to_json())
        zf = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    assert _reason(zf, tmp_path) == "unsafe_archive"


@pytest.mark.parametrize("name", ["evil.exe", "run.bat", "other/dir/file.py", "extra"])
def test_the_layout_is_closed(name: str, tmp_path: Path) -> None:
    assert _reason(_zip({name: b"x"}), tmp_path) == "unsafe_archive"


def test_an_encrypted_member_is_refused(tmp_path: Path) -> None:
    zf = _zip()
    zf.infolist()[2].flag_bits |= 0x1
    assert _reason(zf, tmp_path) == "unsafe_archive"


def test_an_unusual_compression_method_is_refused(tmp_path: Path) -> None:
    zf = _zip()
    zf.infolist()[2].compress_type = zipfile.ZIP_BZIP2
    assert _reason(zf, tmp_path) == "unsafe_archive"


def test_too_many_members_is_too_large(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arch, "MAX_MEMBERS", 5)
    zf = _zip({f"bot/f{n}.py": b"x" for n in range(10)})
    assert _reason(zf, tmp_path) == "too_large"


def test_a_compression_bomb_is_too_large(tmp_path: Path) -> None:
    zf = _zip({"bot/zeros.bin": b"\0" * (20 * 1024 * 1024)})
    assert _reason(zf, tmp_path) == "too_large"
    assert not (tmp_path / "out" / "bot" / "zeros.bin").exists()


def test_a_big_total_is_too_large(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arch, "MAX_UNCOMPRESSED_BYTES", 100)
    zf = _zip({"bot/a.py": b"x" * 60, "bot/b.py": b"y" * 60})
    assert _reason(zf, tmp_path) == "too_large"


def test_a_header_that_lies_about_the_size_is_not_trusted(tmp_path: Path) -> None:
    zf = _zip({"bot/big.bin": b"A" * 5000})
    for info in zf.infolist():
        if info.filename == "bot/big.bin":
            info.file_size = 10  # says 10 bytes, holds 5000
    assert _reason(zf, tmp_path) in ("malformed_package", "too_large")


def test_a_package_without_its_entry_point_is_malformed(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        out.writestr("manifest.json", MANIFEST.to_json())
        out.writestr("requirements.lock", b"")
    zf = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    assert _reason(zf, tmp_path) == "malformed_package"


def test_the_inner_manifest_must_be_there_and_well_formed() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        out.writestr("bot/main.py", b"x")
    with pytest.raises(PackageError, match="malformed_package"):
        arch.read_inner_manifest(zipfile.ZipFile(io.BytesIO(buffer.getvalue())))
    zf = _zip({"manifest.json": b'{"tenant_id": 1}'})
    with pytest.raises(PackageError, match="malformed_package"):
        arch.read_inner_manifest(zf)
    big = _zip({"manifest.json": b" " * 20_000})
    with pytest.raises(PackageError, match="malformed_package"):
        arch.read_inner_manifest(big)


def test_a_file_that_is_not_a_zip_is_malformed(tmp_path: Path) -> None:
    junk = tmp_path / "junk.rgpkg"
    junk.write_bytes(b"not a zip at all")
    with pytest.raises(PackageError, match="malformed_package"):
        arch.open_package(junk)


def test_extraction_needs_an_empty_destination(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "leftover").write_text("x")
    with pytest.raises(PackageError, match="unsafe_archive"):
        arch.safe_extract(_zip(), out)
