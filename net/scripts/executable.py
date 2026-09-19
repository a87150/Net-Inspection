"""Package the Windows collector with a prebuilt, resource-validating host."""
from hashlib import sha256
from pathlib import Path
import os
import subprocess


HOST_DIRECTORY = Path(__file__).resolve().parents[2] / 'agents' / 'pc' / 'windows'
PREBUILT_HOST = HOST_DIRECTORY / 'PCCollectorHost.exe'
HOST_TEMPLATE = Path(__file__).with_name('templates') / 'PCCollectorHost.cs'
MAGIC = b'PCCOLV02'
FOOTER_SIZE = len(MAGIC) + 8 + 32
PAYLOAD_HEADER_SIZE = 8 + 8 + 32 + 32
# Updated by the reproducible Windows build command whenever the host changes.
PREBUILT_HOST_SHA256 = '4b46884681ce458d8de46c0ef55286e873b09cecc1b8eb3dad35e4fb2588bf12'


def _u64(value: int) -> bytes:
    return value.to_bytes(8, 'little', signed=False)


def package_collector(host: bytes, script: bytes, library: bytes) -> bytes:
    """Append a checksummed collector payload to the generic PE host.

    Windows PE loaders ignore an overlay, so a web server only appends public
    terminal configuration and never needs a C# compiler.
    """
    if not host.startswith(b'MZ'):
        raise ValueError('预构建采集宿主无效，请恢复官方 PCCollectorHost.exe。')
    payload = (_u64(len(script)) + _u64(len(library)) + sha256(script).digest()
               + sha256(library).digest() + script + library)
    return host + payload + MAGIC + _u64(len(payload)) + sha256(payload).digest()


def prebuilt_host() -> bytes:
    """Read the versioned generic host and reject a changed deployment artifact."""
    try:
        host = PREBUILT_HOST.read_bytes()
    except OSError as exc:
        raise ValueError('预构建采集宿主缺失，请部署 PCCollectorHost.exe。') from exc
    digest = sha256(host).hexdigest()
    if not PREBUILT_HOST_SHA256 or digest != PREBUILT_HOST_SHA256:
        raise ValueError('预构建采集宿主校验失败，请恢复受信任的 PCCollectorHost.exe。')
    return host


def build_prebuilt_host(output: Path = PREBUILT_HOST) -> str:
    """Windows release-maintenance command; never called by web downloads."""
    windows = Path(os.environ.get('WINDIR', r'C:\\Windows'))
    compiler = windows / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319' / 'csc.exe'
    if not compiler.is_file():
        compiler = windows / 'Microsoft.NET' / 'Framework' / 'v4.0.30319' / 'csc.exe'
    if os.name != 'nt' or not compiler.is_file():
        raise ValueError('构建通用 EXE 宿主需要 Windows 上的 .NET Framework 4 编译器。')
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run([str(compiler), '/nologo', '/target:exe', '/platform:anycpu',
                                 '/reference:System.IO.Compression.dll', '/out:' + str(output), str(HOST_TEMPLATE)],
                                capture_output=True, timeout=60,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('通用 EXE 宿主构建失败或超时，请检查编译环境。') from exc
    if result.returncode or not output.is_file():
        raise ValueError('通用 EXE 宿主构建失败，请检查 .NET Framework 编译器与宿主源文件。')
    return sha256(output.read_bytes()).hexdigest()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Build the generic PC collector host on Windows.')
    parser.add_argument('--build-host', action='store_true')
    arguments = parser.parse_args()
    if not arguments.build_host:
        parser.error('use --build-host')
    print(build_prebuilt_host())