#!/usr/bin/env python3
"""Verify the built raylib ANGLE native output and its packaged dependencies."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path


SYSTEM_DLL_ALLOWLIST = {
    "advapi32.dll", "bcrypt.dll", "cfgmgr32.dll", "combase.dll", "crypt32.dll",
    "d3d11.dll", "d3d12.dll", "d3dcompiler_47.dll", "dwmapi.dll", "dxgi.dll",
    "gdi32.dll", "imm32.dll", "kernel32.dll", "msvcrt.dll", "ntdll.dll",
    "ole32.dll", "oleaut32.dll", "rpcrt4.dll", "sechost.dll", "setupapi.dll",
    "shell32.dll", "shlwapi.dll", "user32.dll", "userenv.dll", "version.dll",
    "winmm.dll", "ws2_32.dll",
}

# Windows 10/11 API set forwarders are OS components, not files to package.
API_SET_PREFIXES = ("api-ms-win-", "ext-ms-win-")


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def assert_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Missing required file: {path}")
    if path.stat().st_size <= 0:
        raise RuntimeError(f"File is empty: {path}")


def run_capture(args: list[str]) -> str:
    print("> " + " ".join(args), flush=True)
    cp = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(cp.stdout)
    if cp.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {cp.returncode}: {' '.join(args)}")
    return cp.stdout


def parse_dumpbin_dependencies(text: str) -> set[str]:
    deps: set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_.+\-]+\.dll)\s*$", line, re.IGNORECASE)
        if m:
            deps.add(m.group(1))
    return deps


def is_system_dependency(name: str) -> bool:
    lower = name.lower()
    return lower in SYSTEM_DLL_ALLOWLIST or lower.startswith(API_SET_PREFIXES)


def check_imports(native_dir: Path, dll_name: str) -> set[str]:
    dll_path = native_dir / dll_name
    assert_file(dll_path)
    dump = run_capture(["dumpbin.exe", "/dependents", str(dll_path)])
    return parse_dumpbin_dependencies(dump)


def require_packaged_or_system(native_dir: Path, dll_name: str, parent: str) -> None:
    lower = dll_name.lower()
    if is_system_dependency(lower):
        return
    if (native_dir / dll_name).is_file():
        return

    # Case-insensitive local check for files that differ only by capitalization.
    for file in native_dir.glob("*.dll"):
        if file.name.lower() == lower:
            return

    raise RuntimeError(
        f"{parent} imports {dll_name}, but {dll_name} is neither a known Windows system DLL "
        f"nor packaged in {native_dir}."
    )


def main() -> int:
    root = repo_root_from_script()
    parser = argparse.ArgumentParser(description="Verify raylib ANGLE native output.")
    parser.add_argument("--native-dir", default=str(root / "artifacts" / "raylib-angle-win-x64"))
    parser.add_argument("--raylib-source-dir", default=str(root / "_build" / "raylib"))
    args = parser.parse_args()

    native_dir = Path(args.native_dir).resolve()
    raylib_dll = native_dir / "raylib.dll"
    gles_dll = native_dir / "libGLESv2.dll"
    egl_dll = native_dir / "libEGL.dll"
    build_info_path = native_dir / "build-info.txt"

    for file in [raylib_dll, gles_dll, egl_dll, build_info_path]:
        assert_file(file)

    raylib_deps = check_imports(native_dir, "raylib.dll")
    if "libGLESv2.dll" not in {d.lower(): d for d in raylib_deps}.values() and not any(d.lower() == "libglesv2.dll" for d in raylib_deps):
        raise RuntimeError("raylib.dll does not import libGLESv2.dll. This is not the ANGLE/OpenGL ES build.")
    if any(d.lower() == "opengl32.dll" for d in raylib_deps):
        raise RuntimeError("raylib.dll imports OPENGL32.dll. This is desktop OpenGL, not ANGLE/GLES.")

    # Recursively verify the DLLs that matter most for the loader error class:
    # raylib.dll -> libGLESv2.dll -> zlib/VC runtime/etc.
    to_scan = ["raylib.dll", "libGLESv2.dll", "libEGL.dll"]
    scanned: set[str] = set()

    while to_scan:
        current = to_scan.pop(0)
        lower_current = current.lower()
        if lower_current in scanned:
            continue
        scanned.add(lower_current)

        deps = check_imports(native_dir, current)
        for dep in deps:
            require_packaged_or_system(native_dir, dep, current)
            dep_path = native_dir / dep
            if dep_path.is_file() and dep.lower() not in scanned:
                to_scan.append(dep)

    info = build_info_path.read_text(encoding="utf-8", errors="replace")
    print("Build info:")
    print(info)
    required = [
        "opengl.version=ES 2.0",
        "graphics=GRAPHICS_API_OPENGL_ES2",
        "context.api=GLFW_EGL_CONTEXT_API",
        "client.api=GLFW_OPENGL_ES_API",
        "angle.backend=GLFW_ANGLE_PLATFORM_TYPE_D3D11",
        "angle.hint.patch.file=",
    ]
    for item in required:
        if item not in info:
            raise RuntimeError(f"build-info.txt is missing: {item}")

    raylib_source_dir = Path(args.raylib_source_dir).resolve()
    patch_file_name = ""
    for line in info.splitlines():
        if line.startswith("angle.hint.patch.file="):
            patch_file_name = line.split("=", 1)[1].strip()
            break
    if patch_file_name and raylib_source_dir.is_dir():
        patch_file = raylib_source_dir / patch_file_name
        assert_file(patch_file)
        patch_text = patch_file.read_text(encoding="utf-8", errors="replace")
        if "GLFW_ANGLE_PLATFORM_TYPE_D3D11" not in patch_text:
            raise RuntimeError(f"{patch_file} does not contain GLFW_ANGLE_PLATFORM_TYPE_D3D11.")

    print("Verified: raylib.dll imports libGLESv2.dll and avoids OPENGL32.dll.")
    print("Verified: libEGL.dll and libGLESv2.dll are packaged beside raylib.dll.")
    print("Verified: non-system native DLL dependencies are packaged beside the app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
