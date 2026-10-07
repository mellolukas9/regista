"""`regista-pack`: keygen, build and verify."""

import argparse
import sys
from pathlib import Path

from regista_pack import build as builder
from regista_pack import keys
from regista_pack.keys import PackError
from regista_pkg import (
    Manifest,
    PackageError,
    load_trusted_keys,
    parse_key_file,
    parse_signature_doc,
    verify,
    verify_file_hash,
)
from regista_pkg import archive as arch


def _keygen(args: argparse.Namespace) -> int:
    password = keys.passphrase(confirm=True)
    private, public, key_id = keys.generate(Path(args.out), args.name, password)
    print(f"Chave criada. key_id: {key_id}")
    print(f"  privada (cifrada): {private}")
    print(f"  pública:           {public}")
    print("Guarde a privada e a senha em lugares separados (docs/runbooks/chave-de-assinatura.md).")
    print("Para o agente confiar nela, a pública entra em regista_pkg/trusted_keys.py.")
    return 0


def _build(args: argparse.Namespace) -> int:
    key, key_id = keys.load_private(Path(args.key), keys.passphrase(confirm=False))
    built = builder.build(
        Path(args.robot),
        version=args.version,
        clients=args.client,
        key=key,
        key_id=key_id,
        out_dir=Path(args.out),
        python=args.python,
        find_links=Path(args.find_links) if args.find_links else None,
        no_index=args.no_index,
    )
    for item in built:
        print(f"cliente {item.client}")
        print(f"  pacote:     {item.package_path} ({item.size / 1048576:.1f} MB)")
        print(f"  assinatura: {item.signature_path}")
        print(f"  sha256:     {item.sha256}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    extra = {}
    if args.keys:
        extra = parse_key_file(Path(args.keys).read_text("utf-8"))
    try:
        signed, signature = parse_signature_doc(Path(args.signature).read_bytes())
        verify(signed, signature, load_trusted_keys(extra))
        verify_file_hash(Path(args.package), signed)
        with arch.open_package(Path(args.package)) as zf:
            arch.validate_members(zf)
            arch.require_layout(zf)
            inner: Manifest = arch.read_inner_manifest(zf)
        if inner != signed.manifest:
            raise PackageError("malformed_package", "manifest.json differs from the signed one")
    except PackageError as exc:
        print(f"RECUSADO: {exc.reason}" + (f" ({exc.detail})" if exc.detail else ""))
        return 1
    m = signed.manifest
    print(f"OK: {m.package_name} {m.version} para o cliente {m.tenant_id}")
    print(f"  chave {signed.key_id}; sha256 {signed.sha256}; Python {m.python}")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="regista-pack", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    k = sub.add_parser("keygen", help="gera um par de chaves de assinatura")
    k.add_argument("--out", required=True, help="pasta fora do repositório")
    k.add_argument("--name", default="ativa", help="nome da chave (ativa, reserva...)")
    k.set_defaults(run=_keygen)

    b = sub.add_parser("build", help="empacota e assina um robô")
    b.add_argument("robot", help="pasta do robô (o nome dela é o nome do pacote)")
    b.add_argument("--version", required=True, help="X.Y.Z")
    b.add_argument("--client", action="append", default=[], help="id do cliente; pode repetir")
    b.add_argument("--key", required=True, help="arquivo .rgkey")
    b.add_argument("--out", default="dist", help="pasta de saída")
    b.add_argument("--python", help="Python exato do robô (X.Y.Z); padrão: .python-version")
    b.add_argument("--find-links", help="pasta com wheels locais (testes, build sem rede)")
    b.add_argument("--no-index", action="store_true", help="não acessar o PyPI")
    b.set_defaults(run=_build)

    v = sub.add_parser("verify", help="confere um pacote e a assinatura dele")
    v.add_argument("package")
    v.add_argument("signature")
    v.add_argument("--keys", help="arquivo com chaves públicas extras (só para testes)")
    v.set_defaults(run=_verify)

    args = parser.parse_args(argv)
    try:
        sys.exit(args.run(args))
    except PackError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        sys.exit(2)
