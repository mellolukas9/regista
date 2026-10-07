"""Getting a package onto the machine: every way it can be refused, and the identity that decides
whose it is."""

import json
import os
import time
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from regista_agent import packages
from regista_agent.config import AgentSettings
from regista_agent.enroll import enroll
from regista_agent.errors import AgentError, EnrollmentRefused
from regista_agent.keystore import KeyStore
from regista_agent.packages import Expected, PackageCache
from regista_pkg import Manifest, PackageError, SignedManifest

from .jobs_support import FakeApi
from .package_support import Built, TestKey, build, new_key
from .support import KEY, SERVER, TENANT_ID, EnrollServer

PACKAGE = "fake_bot"


@pytest.fixture
def key(tmp_path: Path) -> TestKey:
    return new_key(tmp_path / "keys")


@pytest.fixture
def cache(tmp_path: Path) -> PackageCache:
    return PackageCache(tmp_path / "packages")


def expected(**over: object) -> Expected:
    fields: dict[str, object] = {
        "tenant_id": TENANT_ID,
        "package_name": PACKAGE,
        "version": "1.0.0",
    }
    fields.update(over)
    return Expected(**fields)  # type: ignore[arg-type]


def served(api: FakeApi, built: Built, version_id: str = "v-1", **offer: object) -> None:
    api.offers[version_id] = built.offer(version_id, **offer)
    api.package_files[built.url] = built.data


def fetch(
    cache: PackageCache, api: FakeApi, key: TestKey, version_id: str = "v-1", **exp: object
) -> packages.VerifiedPackage:
    return cache.fetch(api, api.package_offer(version_id), expected(**exp), key.trusted())


def refusal(
    cache: PackageCache, api: FakeApi, key: TestKey, version_id: str = "v-1", **exp: object
) -> str:
    with pytest.raises(PackageError) as caught:
        fetch(cache, api, key, version_id, **exp)
    return caught.value.reason


# --- the good path ----------------------------------------------------------------------------


def test_a_good_package_is_downloaded_verified_and_cached(
    key: TestKey, cache: PackageCache, tmp_path: Path
) -> None:
    built = build(key, tenant_id=TENANT_ID)
    api = FakeApi()
    served(api, built)
    verified = fetch(cache, api, key)
    assert verified.path == cache.path_for(PACKAGE, built.sha256) and verified.path.is_file()
    assert verified.path.read_bytes() == built.data
    assert api.downloads == [built.url]
    assert not list(verified.path.parent.glob("*.part")), "no partial file is left"

    packages.extract(verified, tmp_path / "work")
    assert (tmp_path / "work" / "bot" / "main.py").is_file()


def test_the_second_run_uses_the_cache_but_checks_it_again(
    key: TestKey, cache: PackageCache
) -> None:
    built = build(key, tenant_id=TENANT_ID)
    api = FakeApi()
    served(api, built)
    fetch(cache, api, key)
    fetch(cache, api, key)
    assert api.downloads == [built.url], "downloaded once"


def test_a_cached_file_that_was_changed_is_thrown_away_and_fetched_again(
    key: TestKey, cache: PackageCache
) -> None:
    built = build(key, tenant_id=TENANT_ID)
    api = FakeApi()
    served(api, built)
    path = fetch(cache, api, key).path
    path.write_bytes(built.data[:-1] + b"X")  # someone edited the cache
    again = fetch(cache, api, key)
    assert again.path.read_bytes() == built.data
    assert len(api.downloads) == 2


# --- whose it is ------------------------------------------------------------------------------


def test_a_valid_package_of_another_client_is_refused_even_when_the_server_sends_it(
    key: TestKey, cache: PackageCache
) -> None:
    """The scenario of a compromised server: a perfectly signed package, for client A, handed to a
    machine of client B. The signature is fine; it is not this machine's."""
    other_client = uuid.uuid4()
    built = build(key, tenant_id=other_client)
    api = FakeApi()
    served(api, built)
    assert refusal(cache, api, key) == "wrong_client"
    assert api.downloads == [], "it is refused before anything is downloaded"
    assert not (cache.root / PACKAGE).exists()


def test_a_package_for_another_robot_or_version_is_refused(
    key: TestKey, cache: PackageCache
) -> None:
    api = FakeApi()
    served(api, build(key, tenant_id=TENANT_ID, package_name="other_robot"), "v-1")
    assert refusal(cache, api, key, "v-1") == "wrong_package"
    served(api, build(key, tenant_id=TENANT_ID, version="2.0.0"), "v-2")
    assert refusal(cache, api, key, "v-2") == "wrong_version"
    assert api.downloads == []


# --- the signature ----------------------------------------------------------------------------


def test_a_key_the_agent_does_not_trust_is_refused(key: TestKey, cache: PackageCache) -> None:
    stranger = Ed25519PrivateKey.generate()
    built = build(key, tenant_id=TENANT_ID, signer=stranger)  # claims a trusted id, other key
    api = FakeApi()
    served(api, built)
    assert refusal(cache, api, key) == "signature_invalid"

    unknown = build(
        TestKey(stranger, "f" * 16, key.keys_file), tenant_id=TENANT_ID
    )  # an id nobody listed
    served(api, unknown, "v-2")
    assert refusal(cache, api, key, "v-2") == "unknown_key"
    assert api.downloads == []


def test_a_changed_signature_document_is_refused(key: TestKey, cache: PackageCache) -> None:
    built = build(key, tenant_id=TENANT_ID)
    doc = json.loads(built.signature)
    doc["manifest"]["version"] = "1.0.0"
    doc["manifest"]["size"] += 1
    api = FakeApi()
    served(api, built, signature_doc=json.dumps(doc))
    assert refusal(cache, api, key) == "signature_invalid"


@pytest.mark.parametrize("document", ["", "{}", "not json", "[]"])
def test_a_signature_document_that_is_not_one_is_malformed(
    key: TestKey, cache: PackageCache, document: str
) -> None:
    built = build(key, tenant_id=TENANT_ID)
    api = FakeApi()
    served(api, built, signature_doc=document)
    assert refusal(cache, api, key) == "malformed_package"


# --- the file ---------------------------------------------------------------------------------


def test_a_file_that_is_not_what_was_signed_is_refused_before_it_is_opened(
    key: TestKey, cache: PackageCache
) -> None:
    built = build(key, tenant_id=TENANT_ID)
    api = FakeApi()
    served(api, built)
    tampered = bytearray(built.data)
    tampered[len(tampered) // 2] ^= 0xFF
    api.package_files[built.url] = bytes(tampered)
    assert refusal(cache, api, key) == "hash_mismatch"
    assert not cache.path_for(PACKAGE, built.sha256).exists(), "a bad file never enters the cache"
    assert not list((cache.root / PACKAGE).glob("*")), "and no partial file stays behind"


def test_garbage_with_the_wrong_hash_is_a_hash_problem_not_a_zip_problem(
    key: TestKey, cache: PackageCache
) -> None:
    """Proof that the hash is checked first: the bytes are not even a zip."""
    built = build(key, tenant_id=TENANT_ID)
    api = FakeApi()
    served(api, built)
    api.package_files[built.url] = b"Z" * len(built.data)  # same size, not a zip
    assert refusal(cache, api, key) == "hash_mismatch"


def test_a_server_that_lies_about_the_hash_or_size_is_refused(
    key: TestKey, cache: PackageCache
) -> None:
    built = build(key, tenant_id=TENANT_ID)
    api = FakeApi()
    served(api, built, sha256="0" * 64)
    assert refusal(cache, api, key) == "hash_mismatch"
    served(api, built, "v-2", size_bytes=len(built.data) + 1)
    assert refusal(cache, api, key, "v-2") == "hash_mismatch"
    assert api.downloads == []


def test_a_package_over_the_limit_is_refused_before_the_download(
    key: TestKey, cache: PackageCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = build(key, tenant_id=TENANT_ID)
    monkeypatch.setattr(packages, "MAX_PACKAGE_BYTES", 10)
    api = FakeApi()
    served(api, built)
    assert refusal(cache, api, key) == "too_large"
    assert api.downloads == []


def test_a_download_bigger_than_what_was_signed_is_stopped(
    key: TestKey, cache: PackageCache
) -> None:
    built = build(key, tenant_id=TENANT_ID)
    api = FakeApi()
    served(api, built)
    api.package_files[built.url] = built.data + b"A" * 1000
    # The fake enforces the cap like the real download does.
    with pytest.raises(PackageError) as caught:
        fetch(cache, api, key)
    assert caught.value.reason == "too_large"


# --- unpacking --------------------------------------------------------------------------------


def test_a_signed_package_cannot_write_outside_its_folder(
    key: TestKey, cache: PackageCache, tmp_path: Path
) -> None:
    """Right signature, right hash, hostile zip: the extraction refuses it and leaves nothing."""
    built = build(key, tenant_id=TENANT_ID, members={"../../escaped.py": b"boom"})
    api = FakeApi()
    served(api, built)
    verified = fetch(cache, api, key)
    work = tmp_path / "run" / "work"
    with pytest.raises(PackageError) as caught:
        packages.extract(verified, work)
    assert caught.value.reason == "unsafe_archive"
    assert not (tmp_path / "escaped.py").exists() and not (tmp_path / "run" / "escaped.py").exists()
    assert not work.exists(), "nothing half-trusted stays behind"


def test_a_zip_whose_manifest_is_not_the_signed_one_is_refused(
    key: TestKey, cache: PackageCache, tmp_path: Path
) -> None:
    lying = Manifest(
        tenant_id=str(uuid.uuid4()), package_name=PACKAGE, version="1.0.0", python="3.13.5"
    )
    built = build(key, tenant_id=TENANT_ID, inner=lying)
    api = FakeApi()
    served(api, built)
    verified = fetch(cache, api, key)
    with pytest.raises(PackageError) as caught:
        packages.extract(verified, tmp_path / "work")
    assert caught.value.reason == "malformed_package"
    assert not (tmp_path / "work").exists()


def test_the_file_is_checked_again_right_before_it_is_opened(
    key: TestKey, cache: PackageCache, tmp_path: Path
) -> None:
    built = build(key, tenant_id=TENANT_ID)
    api = FakeApi()
    served(api, built)
    verified = fetch(cache, api, key)
    verified.path.write_bytes(b"swapped after the check")
    with pytest.raises(PackageError) as caught:
        packages.extract(verified, tmp_path / "work")
    assert caught.value.reason == "hash_mismatch"


def test_signed_garbage_is_a_malformed_package(
    key: TestKey, cache: PackageCache, tmp_path: Path
) -> None:
    built = build(key, tenant_id=TENANT_ID, raw=b"signed, hashed, but not a zip")
    api = FakeApi()
    served(api, built)
    verified = fetch(cache, api, key)
    with pytest.raises(PackageError) as caught:
        packages.extract(verified, tmp_path / "work")
    assert caught.value.reason == "malformed_package"


# --- cleaning up ------------------------------------------------------------------------------


def test_old_versions_go_but_the_running_one_and_the_newest_stay(
    key: TestKey, cache: PackageCache
) -> None:
    folder = cache.root / PACKAGE
    folder.mkdir(parents=True)
    now = time.time()
    for n in range(6):
        path = folder / f"{n:064x}.rgpkg"
        path.write_bytes(b"x")
        os.utime(path, (now - 1000 + n, now - 1000 + n))  # 5 is the newest, 0 the oldest
    running = f"{0:064x}"  # the oldest, but it is running right now
    cache.prune(PACKAGE, protect={running}, keep=3)
    left = sorted(p.stem for p in folder.glob("*.rgpkg"))
    assert left == sorted([f"{n:064x}" for n in (0, 3, 4, 5)])


def test_pruning_an_unknown_robot_does_nothing(cache: PackageCache) -> None:
    cache.prune("nothing_here", protect=set())


# --- who this machine belongs to --------------------------------------------------------------


def test_enrollment_stores_the_client_in_the_protected_folder(home: Path, no_acl: None) -> None:
    result = enroll(
        AgentSettings(), url=SERVER, key=KEY, agent_account="tester", http=EnrollServer().session()
    )
    store = KeyStore(AgentSettings().keys_dir, agent_account="tester")
    assert store.read_tenant_id() == TENANT_ID and result.machine_id
    assert (home / "keys" / "identity.json").is_file()
    assert not list((home / "keys").glob("*.new")), "no staged file is left"


def test_a_refused_enrollment_stores_no_identity(home: Path, no_acl: None) -> None:
    with pytest.raises(EnrollmentRefused):
        enroll(
            AgentSettings(),
            url=SERVER,
            key=KEY,
            agent_account="tester",
            http=EnrollServer(refuse=True).session(),
        )
    keys = home / "keys"
    assert not keys.exists() or not any(keys.glob("identity*"))


def test_a_machine_enrolled_before_m4_has_no_client_and_a_damaged_file_is_an_error(
    home: Path,
) -> None:
    store = KeyStore(home / "keys", agent_account=None)
    assert store.read_tenant_id() is None
    (home / "keys").mkdir(parents=True)
    store.identity_path.write_text("not json", encoding="utf-8")
    with pytest.raises(AgentError, match="corrompido"):
        store.read_tenant_id()
    store.identity_path.write_text(json.dumps({"tenant_id": "not-a-uuid"}), encoding="utf-8")
    with pytest.raises(AgentError, match="corrompido"):
        store.read_tenant_id()


def test_the_signed_manifest_type_is_what_the_cache_hands_back(
    key: TestKey, cache: PackageCache
) -> None:
    built = build(key, tenant_id=TENANT_ID)
    api = FakeApi()
    served(api, built)
    assert isinstance(fetch(cache, api, key).signed, SignedManifest)
