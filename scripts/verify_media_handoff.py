"""Mandatory isolated Docker proof; no production mounts or provider calls.

Run explicitly: python scripts/verify_media_handoff.py
CI runs this separate privilege-scoped proof before the credential-free suite.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

PROBE = r"""
import errno, hashlib, json, os, stat, struct, sys, zlib
from pathlib import Path
from fatty_trader.intake import media
from fatty_trader.analyzer import image_analysis
from fatty_trader.analyzer.codex_runner import CodexRunResult

def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
       + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b''))
digest = hashlib.sha256(png).hexdigest()
revision = hashlib.sha256(b'isolated synthetic source revision').hexdigest()
root = Path('/media')
path = root / 'telegram/7/1' / (revision + '.png')
mode = sys.argv[1]
if mode == 'intake':
    assert (os.getuid(), os.getgid()) == (10001, 10001)
    os.umask(0o077)
    observed = []
    write = Path.write_bytes
    link = os.link
    def staged_write(temporary, content):
        write(temporary, content[:2])
        assert stat.S_IMODE(temporary.stat().st_mode) == 0o600
        assert not path.exists()
        return write(temporary, content)
    def complete_link(temporary, final):
        assert Path(temporary).read_bytes() == png
        assert stat.S_IMODE(Path(temporary).stat().st_mode) == 0o640
        observed.append(True)
        return link(temporary, final)
    Path.write_bytes = staged_write
    os.link = complete_link
    arguments = dict(channel_id=7, message_id=1, revision_hash=revision, mime_type='image/png')
    artifact = media.persist_media_bytes(root, data=png, **arguments)
    Path.write_bytes = write
    os.link = link
    assert observed == [True]
    inode = path.stat().st_ino
    assert media.persist_media_bytes(root, data=png, **arguments) == artifact
    try:
        media.persist_media_bytes(root, data=b'different', **arguments)
        raise AssertionError('collision accepted')
    except ValueError:
        pass
    assert path.stat().st_ino == inode and path.read_bytes() == png
    for message_id, content in ((2, b''), (3, b'x' * (media.MAX_MEDIA_BYTES + 1))):
        try:
            media.persist_media_bytes(root, data=content, **dict(arguments, message_id=message_id))
            raise AssertionError('invalid size accepted')
        except ValueError:
            pass
    sink = media.BoundedMediaBuffer()
    sink.write(b'x' * media.MAX_MEDIA_BYTES)
    try:
        sink.write(b'x')
        raise AssertionError('stream bound not enforced')
    except media.MediaTooLarge:
        assert sink.getbuffer().nbytes == media.MAX_MEDIA_BYTES
    def failed_write(temporary, content):
        write(temporary, content[:2])
        raise OSError('synthetic disk failure')
    Path.write_bytes = failed_write
    try:
        media.persist_media_bytes(root, data=png, **dict(arguments, message_id=4))
        raise AssertionError('failed publication accepted')
    except OSError:
        pass
    finally:
        Path.write_bytes = write
    assert not list(root.rglob('*.tmp'))
    assert list(root.rglob('*.png')) == [path]
    assert (path.stat().st_uid, path.stat().st_gid) == (10001, 10001)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o750 for p in root.rglob('*') if p.is_dir())
    checks = ['private_partial_0600', 'atomic_complete_link_0640', 'collision_preserved',
              'size_bound', 'stream_bound', 'failed_publication_cleanup']
elif mode == 'analyzer':
    assert (os.getuid(), os.getgid()) == (5013, 5013)
    assert 10001 in os.getgroups()
    assert path.read_bytes() == png
    identity = dict(image_path=str(path), channel_id=7, message_id=1, text='synthetic caption',
                    canonical_revision=revision, media_sha256=digest)
    assert image_analysis.image_source_revision(**identity) == revision
    try:
        image_analysis.image_source_revision(**dict(identity, media_sha256='0' * 64))
        raise AssertionError('wrong hash accepted')
    except ValueError as exc:
        assert 'hash' in str(exc)
    attached = []
    def fake_provider(prompt, images):
        assert 'synthetic caption' in prompt
        assert images == (str(path),)
        assert Path(images[0]).read_bytes() == png
        attached.append(images[0])
        return CodexRunResult(True, False, False, 0, None,
                              '{"message":"synthetic image consumed","setup":{}}', '')
    result = image_analysis.analyze_image_json(text='synthetic caption', message_id=1,
                                             image_path=str(path), runner=fake_provider)
    assert result == {'message': 'synthetic image consumed', 'setup': {}}
    assert attached == [str(path)]
    denied = []
    for name, action in [('overwrite', lambda: path.write_bytes(b'forbidden')),
                         ('create', lambda: (root / 'forbidden').write_bytes(b'x')),
                         ('unlink', lambda: path.unlink())]:
        try:
            action()
            raise AssertionError(name + ' allowed')
        except OSError as exc:
            assert exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM)
            denied.append({'operation': name, 'errno': exc.errno})
    assert path.read_bytes() == png
    checks = ['cross_uid_read', 'canonical_revision_reused', 'wrong_hash_rejected',
              'real_analysis_attachment_with_fake_provider', 'analyzer_write_denials']
else:
    assert mode == 'outsider' and os.getuid() == 5013 and 10001 not in os.getgroups()
    try:
        path.read_bytes()
        raise AssertionError('unshared UID read private image')
    except PermissionError:
        pass
    checks = ['no_group_read_denied']
print(json.dumps({'role': mode, 'uid': os.getuid(), 'gid': os.getgid(), 'groups': os.getgroups(),
                  'sha256': digest, 'revision': revision, 'path': str(path), 'checks': checks,
                  'source_hashes': {m.__name__: hashlib.sha256(
                      Path(m.__file__).read_bytes()).hexdigest()
                                    for m in (media, image_analysis)},
                  **({'denied': denied} if mode == 'analyzer' else {})}, sort_keys=True))
"""


def verify_real_default_uid_handoff_and_cleanup():
    repository = Path(__file__).resolve().parents[1]
    token = uuid.uuid4().hex
    volume = f"fatty-media-proof-{token}"
    image = f"fattyremediation:media-proof-{token}"
    containers = []
    evidence = {}
    environment = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}

    def docker(*args, check=True):
        result = subprocess.run(
            ["docker", *args],
            text=True,
            capture_output=True,
            env=environment,
            timeout=180,
            check=False,
        )
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    config = subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "config", "--format", "json"],
        cwd=repository,
        env={**environment, "POSTGRES_PASSWORD": "synthetic-unused"},
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    analyzer = json.loads(config.stdout)["services"]["analyzer"]
    assert analyzer["user"] == "5013:5013"
    assert "10001" in [str(group) for group in analyzer["group_add"]]
    assert any(
        mount["target"] == "/app/runtime/media" and mount["read_only"]
        for mount in analyzer["volumes"]
    )
    evidence["base_image"] = json.loads(
        docker("image", "inspect", "fattyremediation:reviewcandidate").stdout
    )[0]["Id"]
    try:
        with tempfile.TemporaryDirectory(prefix="fatty-media-proof-context-") as temporary:
            context = Path(temporary)
            for name in ("intake/media.py", "analyzer/image_analysis.py"):
                destination = context / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repository / "src/fatty_trader" / name, destination)
            (context / "probe.py").write_text(PROBE)
            (context / "Dockerfile").write_text(
                "FROM fattyremediation:reviewcandidate\n"
                "COPY intake/media.py /app/src/fatty_trader/intake/media.py\n"
                "COPY analyzer/image_analysis.py /app/src/fatty_trader/analyzer/image_analysis.py\n"
                "COPY probe.py /proof.py\n"
            )
            docker("build", "--network", "none", "-t", image, str(context))
        assert not context.exists()
        evidence["build_context_removed"] = True
        evidence["intermediate_image_id"] = json.loads(docker("image", "inspect", image).stdout)[0][
            "Id"
        ]
        docker("volume", "create", volume)
        # Only a new disposable volume is provisioned; never chmod host media.
        initializer = f"fatty-media-init-{token}"
        containers.append(initializer)
        docker(
            "run",
            "--name",
            initializer,
            "--rm",
            "--network",
            "none",
            "--user",
            "0:0",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"type=volume,src={volume},dst=/media,volume-nocopy",
            "--entrypoint",
            "/app/.venv/bin/python",
            image,
            "-c",
            "import os; os.chmod('/media',0o750); os.chown('/media',10001,10001)",
        )
        for role in ("intake", "analyzer", "outsider"):
            name = f"fatty-media-{role}-{token}"
            containers.append(name)
            args = [
                "create",
                "--name",
                name,
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--read-only",
                "--mount",
                f"type=volume,src={volume},dst=/media,volume-nocopy"
                + (",readonly" if role != "intake" else ""),
                "--env",
                "PYTHONPATH=/app/src",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
            ]
            if role != "intake":
                args += ["--user", "5013:5013"]
            if role == "analyzer":
                args += ["--group-add", "10001"]
            docker(*args, "--entrypoint", "/app/.venv/bin/python", image, "/proof.py", role)
            metadata = json.loads(docker("inspect", name).stdout)[0]
            assert metadata["HostConfig"]["NetworkMode"] == "none"
            assert len(metadata["Mounts"]) == 1
            assert metadata["Mounts"][0]["Name"] == volume
            assert metadata["Mounts"][0]["RW"] == (role == "intake")
            output = docker("start", "-a", name)
            assert json.loads(docker("inspect", name).stdout)[0]["State"]["ExitCode"] == 0, (
                output.stderr
            )
            evidence[role] = json.loads(output.stdout)
        for module, relative in (
            ("fatty_trader.intake.media", "intake/media.py"),
            ("fatty_trader.analyzer.image_analysis", "analyzer/image_analysis.py"),
        ):
            expected = hashlib.sha256(
                (repository / "src/fatty_trader" / relative).read_bytes()
            ).hexdigest()
            assert evidence["intake"]["source_hashes"][module] == expected
            assert evidence["analyzer"]["source_hashes"][module] == expected
    finally:
        for name in containers:
            docker("rm", "-f", name, check=False)
        docker("volume", "rm", volume, check=False)
        docker("image", "rm", image, check=False)
        assert all(docker("inspect", name, check=False).returncode != 0 for name in containers)
        assert docker("volume", "inspect", volume, check=False).returncode != 0
        assert docker("image", "inspect", image, check=False).returncode != 0
        evidence["cleanup_verified"] = {"containers": containers, "volume": volume, "image": image}
        print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    verify_real_default_uid_handoff_and_cleanup()
