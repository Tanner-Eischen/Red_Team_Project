"""Harbor 0.13.2 Docker transport with no agent-writable host mounts.

The supported environment import hook keeps upstream Harbor unchanged. Only
bounded regular files cross the container/host boundary; links and special files
never become host artifacts or verifier inputs.
"""
from __future__ import annotations

import base64
import asyncio
import hashlib
import json
from pathlib import Path, PurePosixPath
import shlex
import stat
import tempfile

from harbor.environments.capabilities import EnvironmentCapabilities
from harbor.environments.docker.docker import DockerEnvironment

POLICY_VERSION = "hf-docker-v1"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TRANSFER_BYTES = 16 * 1024 * 1024
MAX_FILES = 128

# Isolated Python ignores the candidate's cwd, PYTHONPATH and user site packages.
# File descriptors use NOFOLLOW and are fstat'ed after opening, closing the usual
# lstat/open race. There are no host bind mounts to traverse from this namespace.
_EXPORT = r'''
import base64,json,os,stat,sys
from pathlib import Path
root=Path(sys.argv[1]); is_dir=sys.argv[2]=='directory'
assert '..' not in root.parts and str(root)==sys.argv[1], 'Noncanonical artifact source'
allowed={'/app/pipeline.py','/app/output','/logs/agent','/logs/verifier','/logs/artifacts'}
assert str(root) in allowed or any(str(root).startswith(x+'/') for x in allowed if x.startswith('/logs/'))
parts=list(root.parents); assert not any(p.is_symlink() for p in parts)
records=[]; total=0
def add(path,relative):
 global total
 info=path.lstat()
 assert not stat.S_ISLNK(info.st_mode) and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)), 'Unsafe artifact type'
 if stat.S_ISDIR(info.st_mode):
  records.append({'path':relative,'directory':True})
  assert len(records)<=128, 'Too many artifact paths'
  for child in sorted(path.iterdir()): add(child,relative+'/'+child.name if relative else child.name)
 else:
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
  try:
   actual=os.fstat(fd); assert stat.S_ISREG(actual.st_mode) and actual.st_nlink==1, 'Linked artifact'
   assert actual.st_size<=8*1024*1024, 'Oversized artifact'
   with os.fdopen(fd,'rb',closefd=False) as stream: data=stream.read(8*1024*1024+1)
   assert len(data)<=8*1024*1024, 'Growing artifact'
  finally: os.close(fd)
  total+=len(data); assert total<=16*1024*1024, 'Oversized artifact collection'
  records.append({'path':relative,'data':base64.b64encode(data).decode()})
  assert len(records)<=128, 'Too many artifact paths'
assert root.is_dir() if is_dir else root.is_file()
add(root,'' )
print(json.dumps(records,separators=(',',':')))
'''

_IMPORT = r'''
import base64,json,os,stat,sys
from pathlib import Path
root=Path(sys.argv[1]); directory=sys.argv[2]=='directory'
assert root.is_absolute() and '..' not in root.parts
records=json.load(sys.stdin);assert len(records)<=128
total=0
for record in records:
 name=record['path'];relative=Path(name)
 assert not relative.is_absolute() and '..' not in relative.parts and '\\' not in name and ':' not in name
 path=root/relative if directory else root
 for parent in (path,*path.parents): assert not parent.is_symlink()
 if record.get('directory'):
  path.mkdir(parents=True,exist_ok=True)
 else:
  data=base64.b64decode(record['data'],validate=True);total+=len(data)
  assert len(data)<=8*1024*1024 and total<=16*1024*1024
  path.parent.mkdir(parents=True,exist_ok=True)
  fd=os.open(path,os.O_CREAT|os.O_WRONLY|os.O_TRUNC|os.O_NOFOLLOW,0o644)
  try:
   assert os.fstat(fd).st_nlink==1 and stat.S_ISREG(os.fstat(fd).st_mode)
   with os.fdopen(fd,'wb',closefd=False) as stream: stream.write(data)
  finally: os.close(fd)
'''


def _host_tree(path: Path) -> None:
    """Reject every linked/special path before a host tree is read or replaced."""
    for current in (path, *path.parents):
        if current.is_symlink():
            raise ValueError("Symlinked host transfer path")
    total = 0
    paths = [path, *path.rglob("*")] if path.is_dir() else [path]
    if len(paths) > MAX_FILES:
        raise ValueError("Too many transfer paths")
    for current in paths:
        info = current.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Linked or special host artifact")
        total += info.st_size
        if info.st_size > MAX_FILE_BYTES or total > MAX_TRANSFER_BYTES:
            raise ValueError("Oversized host transfer")


def materialize(records: list[dict], target: Path, *, directory: bool) -> dict[str, str]:
    """Validate the entire bounded transfer before creating any destination file."""
    if not isinstance(records, list) or len(records) > MAX_FILES:
        raise ValueError("Invalid transfer manifest")
    payloads = []
    total = 0
    seen = set()
    for record in records:
        name = record.get("path")
        if not isinstance(name, str) or "\\" in name or ":" in name:
            raise ValueError("Unsafe artifact name")
        relative = PurePosixPath(name)
        if (relative.is_absolute() or ".." in relative.parts or name in seen
                or (name and relative.as_posix() != name)):
            raise ValueError("Unsafe or duplicate artifact destination")
        seen.add(name)
        if not directory and name:
            raise ValueError("File transfer contains child paths")
        if record.get("directory"):
            if not directory:
                raise ValueError("Directory in file transfer")
            payloads.append((name, None))
            continue
        data = base64.b64decode(record["data"], validate=True)
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_TRANSFER_BYTES:
            raise ValueError("Oversized transfer")
        payloads.append((name, data))
    for current in (target, *target.parents):
        if current.is_symlink():
            raise ValueError("Symlinked artifact destination")
    if target.exists():
        _host_tree(target)
    hashes = {}
    for name, data in payloads:
        destination = target / name if directory else target
        for current in (destination, *destination.parents):
            if current.is_symlink():
                raise ValueError("Symlinked artifact destination")
        if destination.is_file() and destination.stat().st_nlink != 1:
            raise ValueError("Hardlinked artifact destination")
        if data is None:
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Destination directories are controller-owned and never mounted in
            # a candidate container; no candidate process can race this write.
            destination.write_bytes(data)
            hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes


class IsolatedDockerEnvironment(DockerEnvironment):
    """Pinned Linux-only Harbor environment with a deliberately narrow contract."""

    @property
    def capabilities(self) -> EnvironmentCapabilities:
        return EnvironmentCapabilities(disable_internet=True, windows=False, mounted=False, docker_compose=True)

    def __init__(self, *args, **kwargs):
        # Harbor passes its normal verifier/agent/artifact bind mounts. None are
        # needed when mounted=False; transport below copies allowlisted bytes.
        kwargs["mounts"] = []
        super().__init__(*args, **kwargs)
        if (self._is_windows_container or self.extra_docker_compose_paths
                or self._environment_docker_compose_path.exists() or self.task_env_config.env
                or self.task_env_config.docker_image):
            raise ValueError("The isolated runtime supports generated Linux tasks only")
        self._security = tempfile.TemporaryDirectory(prefix="hf-docker-policy-")
        self._security_path = Path(self._security.name) / "policy.json"
        temporary_filesystems = ["/app:size=32m,mode=0755,exec,nosuid,nodev", "/tmp:size=64m,mode=1777,exec,nosuid,nodev",
                                 "/home/agent:size=32m,uid=1000,gid=1000,mode=0700,exec,nosuid,nodev",
                                 "/logs:size=16m,mode=0755,noexec,nosuid,nodev",
                                 "/solution:size=8m,mode=0755,exec,nosuid,nodev"]
        if self.environment_dir.name == "tests":
            temporary_filesystems.append("/tests:size=32m,mode=0700,exec,nosuid,nodev")
        self._security_path.write_text(json.dumps({"services": {"main": {
            "read_only": True, "init": True, "tmpfs": temporary_filesystems,
            "pids_limit": 64, "security_opt": ["no-new-privileges:true"], "cap_drop": ["ALL"],
            "deploy": {"resources": {"limits": {"pids": 64}}},
            "cap_add": ["SETUID", "SETGID", "KILL", "DAC_OVERRIDE", "FOWNER", "CHOWN"],
            "ulimits": {"fsize": {"soft": MAX_TRANSFER_BYTES, "hard": MAX_TRANSFER_BYTES}},
        }}}), encoding="utf-8")

    @property
    def _docker_compose_paths(self) -> list[Path]:
        return [*super()._docker_compose_paths, self._security_path]

    async def start(self, force_build: bool):
        await super().start(force_build)
        listed = await self._run_docker_compose_command(["ps", "--quiet", "main"])
        self._container_id = listed.stdout.strip()
        setup = await self.exec("mkdir -p /logs/agent /logs/artifacts /logs/verifier && "
                                "chown 1000:1000 /logs/agent /logs/artifacts && "
                                "chmod 700 /logs/agent /logs/artifacts /logs/verifier", user="root")
        if setup.return_code:
            raise RuntimeError("Could not initialize private log directories")
        if self.environment_dir.name == "tests":
            await self.upload_dir(self.environment_dir, "/tests")
            protected = await self.exec("chmod 700 /tests /logs/verifier", user="root")
        else:
            await self.upload_dir(self.environment_dir / "public", "/app")
            protected = await self.exec("chown -R 1000:1000 /app", user="root")
        if protected.return_code:
            raise RuntimeError("Could not initialize task workspace")
        process = await asyncio.create_subprocess_exec("docker", "inspect", self._container_id,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        raw, _ = await process.communicate()
        if process.returncode:
            raise RuntimeError("Could not inspect runtime isolation")
        inspect = json.loads(raw)[0]
        host = inspect["HostConfig"]
        passed = (all(mount["Type"] == "tmpfs" for mount in inspect["Mounts"])
                  and host["ReadonlyRootfs"] and host["Init"]
                  and host["PidMode"] in ("", "private")
                  and host["NetworkMode"] == "none" and host["PidsLimit"] == 64
                  and host["Memory"] > 0 and host["NanoCpus"] > 0
                  and "no-new-privileges:true" in host["SecurityOpt"] and "ALL" in host["CapDrop"])
        role = "verifier" if self.environment_dir.name == "tests" else "agent"
        proof = {"policy_version": POLICY_VERSION, "passed": passed, "role": role, "image_id": inspect["Image"],
                 "mounts": inspect["Mounts"], "network_mode": host["NetworkMode"], "pids_limit": host["PidsLimit"],
                 "memory_bytes": host["Memory"], "nano_cpus": host["NanoCpus"],
                 "read_only_root": host["ReadonlyRootfs"], "tmpfs": host["Tmpfs"], "init": host["Init"],
                 "pid_mode": host["PidMode"],
                 "security_options": host["SecurityOpt"], "cap_drop": host["CapDrop"], "cap_add": host["CapAdd"]}
        (self.trial_paths.trial_dir / f"runtime-{role}.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
        if not passed:
            raise RuntimeError("Runtime did not enforce the declared isolation policy")
        if role == "verifier":
            marker = await self.exec("python3 -I -c " + shlex.quote(
                "from pathlib import Path; p=Path('/tests/.harborforge-isolated-verifier'); "
                "p.write_text('{\"policy_version\":\"hf-docker-v1\",\"private_pid_namespace\":true}'); p.chmod(0o400)"),
                user="root")
            if marker.return_code:
                raise RuntimeError("Could not mark the inspected private verifier namespace")

    async def stop(self, delete: bool):
        try:
            await super().stop(delete)
        finally:
            self._security.cleanup()

    async def _download(self, source: str, target: Path | str, directory: bool):
        destination = Path(target)
        if not destination.resolve().is_relative_to(self.trial_paths.trial_dir.resolve()):
            raise ValueError("Artifact destination escapes this trial")
        kind = "directory" if directory else "file"
        result = await self.exec("python3 -I -c " + shlex.quote(_EXPORT) + " " + shlex.quote(str(source)) + " " + kind,
                                 user="root", timeout_sec=20)
        if result.return_code or len(result.stdout or "") > MAX_TRANSFER_BYTES * 2:
            raise ValueError("Unsafe, missing or oversized container artifact")
        hashes = materialize(json.loads(result.stdout), destination, directory=directory)
        log = self.trial_paths.trial_dir / "transfer-audit.jsonl"
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"source": str(source), "directory": directory, "files_sha256": hashes}) + "\n")

    async def download_file(self, source_path, target_path):
        await self._download(str(source_path), target_path, False)

    async def download_dir(self, source_dir, target_dir):
        await self._download(str(source_dir), target_dir, True)

    async def upload_file(self, source_path, target_path):
        await self._upload(Path(source_path), str(target_path), False)

    async def upload_dir(self, source_dir, target_dir):
        await self._upload(Path(source_dir), str(target_dir), True)

    async def _upload(self, source: Path, target: str, directory: bool):
        _host_tree(source)
        records = []
        paths = [source, *sorted(source.rglob("*"))] if directory else [source]
        for path in paths:
            name = path.relative_to(source).as_posix() if path != source else ""
            if path.is_dir():
                records.append({"path": name, "directory": True})
            else:
                records.append({"path": name, "data": base64.b64encode(path.read_bytes()).decode()})
        # Docker cp refuses read-only-root containers even for writable tmpfs.
        # Stream bytes to a trusted root utility instead; no payload is in argv.
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", "--interactive", "--user", "root", self._container_id,
            "python3", "-I", "-c", _IMPORT, target, "directory" if directory else "file",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(process.communicate(json.dumps(records).encode()), timeout=30)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError("Bounded upload timed out") from None
        if process.returncode:
            raise ValueError("Container rejected bounded upload: " + stderr.decode(errors="replace")[-1000:])
