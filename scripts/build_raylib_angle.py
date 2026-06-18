#!/usr/bin/env python3
"""Build raylib for Windows x64 using OpenGL ES 2.0 + ANGLE + Direct3D11.

The workflow builds raylib 6.0 to match Raylib-cs 8.0.0.

Important packaging rule:
  The final C# app must contain raylib.dll and every non-system native DLL that
  raylib/ANGLE depend on.  With vcpkg's dynamic Windows triplet this can include
  transitive DLLs such as zlib1.dll, plus the Visual C++ runtime DLLs on machines
  that do not already have the VC++ Redistributable installed.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def run(args: list[str], cwd: Path | None = None) -> None:
    print("> " + " ".join(args), flush=True)
    completed = subprocess.run(args, cwd=str(cwd) if cwd else None)
    if completed.returncode != 0:
        raise SystemExit(f"Command failed with exit code {completed.returncode}: {' '.join(args)}")


def assert_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Missing required file: {path}")


def assert_dir(path: Path) -> None:
    if not path.is_dir():
        raise FileNotFoundError(f"Missing required directory: {path}")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_library_config(raylib_dir: Path) -> None:
    path = raylib_dir / "cmake" / "LibraryConfigurations.cmake"
    assert_file(path)
    text = read_text(path)
    marker = "Desktop Windows OpenGL ES selected: linking raylib against ANGLE libEGL/libGLESv2"
    if marker in text:
        print(f"{path} already contains ANGLE Windows link patch.")
        return

    pattern = re.compile(
        r"elseif\s*\(\s*WIN32\s*\)\s*"
        r"add_definitions\s*\(\s*-D_CRT_SECURE_NO_WARNINGS\s*\)\s*"
        r"find_package\s*\(\s*OpenGL\s+QUIET\s*\)\s*"
        r"set\s*\(\s*LIBS_PRIVATE\s+\$\{OPENGL_LIBRARIES\}\s+winmm\s*\)",
        re.DOTALL,
    )
    replacement = """elseif (WIN32)
    add_definitions(-D_CRT_SECURE_NO_WARNINGS)
    if (${OPENGL_VERSION} MATCHES "ES 2.0|ES 3.0")
        message(STATUS "Desktop Windows OpenGL ES selected: linking raylib against ANGLE libEGL/libGLESv2")
        find_path(ANGLE_INCLUDE_DIR EGL/egl.h REQUIRED)
        find_library(ANGLE_EGL_LIBRARY NAMES libEGL EGL REQUIRED)
        find_library(ANGLE_GLESV2_LIBRARY NAMES libGLESv2 GLESv2 REQUIRED)
        include_directories(${ANGLE_INCLUDE_DIR})
        add_definitions(-DGLFW_INCLUDE_ES2)
        set(OPENGL_INCLUDE_DIR ${ANGLE_INCLUDE_DIR})
        set(LIBS_PRIVATE ${ANGLE_GLESV2_LIBRARY} ${ANGLE_EGL_LIBRARY} winmm)
    else()
        find_package(OpenGL QUIET)
        set(LIBS_PRIVATE ${OPENGL_LIBRARIES} winmm)
    endif()"""
    new_text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        print(f"Could not find expected WIN32 OpenGL link block in {path}.")
        for i, line in enumerate(text.splitlines(), 1):
            if any(token in line for token in ("WIN32", "OPENGL_LIBRARIES", "LIBS_PRIVATE", "OpenGL")):
                print(f"{i}: {line}")
        raise RuntimeError("Could not patch LibraryConfigurations.cmake. raylib CMake layout changed.")
    write_text(path, new_text)
    print(f"Patched {path} for Windows Desktop + OpenGL ES + ANGLE.")


def candidate_glfw_sources(raylib_dir: Path) -> list[Path]:
    preferred = [
        raylib_dir / "src" / "platforms" / "rcore_desktop_glfw.c",
        raylib_dir / "src" / "rcore.c",
    ]
    result: list[Path] = []
    for p in preferred:
        if p.is_file():
            result.append(p)

    src_dir = raylib_dir / "src"
    if src_dir.is_dir():
        for p in src_dir.rglob("*.c"):
            sp = str(p).replace("\\", "/")
            if "/external/glfw/" in sp:
                continue
            if p not in result:
                result.append(p)
    return result


def patch_glfw_angle_hint(raylib_dir: Path) -> Path:
    hint = "glfwInitHint(GLFW_ANGLE_PLATFORM_TYPE, GLFW_ANGLE_PLATFORM_TYPE_D3D11);"
    for path in candidate_glfw_sources(raylib_dir):
        text = read_text(path)
        if hint in text:
            print(f"{path} already requests GLFW_ANGLE_PLATFORM_TYPE_D3D11.")
            return path

    insertion_lines = [
        "#if (defined(GRAPHICS_API_OPENGL_ES2) || defined(GRAPHICS_API_OPENGL_ES3)) && defined(_WIN32)",
        "    // Force ANGLE to use its Direct3D 11 backend on Windows when raylib is built as OpenGL ES.",
        "    glfwInitHint(GLFW_ANGLE_PLATFORM_TYPE, GLFW_ANGLE_PLATFORM_TYPE_D3D11);",
        "#endif",
        "",
    ]

    glfw_init_call = re.compile(r"\bglfwInit\s*\(\s*\)")
    ignored_calls = ("glfwInitHint", "glfwInitAllocator", "glfwInitVulkanLoader")

    for path in candidate_glfw_sources(raylib_dir):
        text = read_text(path)
        if "glfwInit" not in text:
            continue

        lines = text.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if any(ignored in line for ignored in ignored_calls):
                continue
            if not glfw_init_call.search(line):
                continue

            indent = re.match(r"^[ \t]*", line).group(0)
            insertion = "".join(indent + item + "\n" for item in insertion_lines)
            lines.insert(index, insertion)
            write_text(path, "".join(lines))
            print(f"Patched {path} to request GLFW_ANGLE_PLATFORM_TYPE_D3D11 before glfwInit().")
            print(f"Patch inserted before line {index + 1}: {line.strip()}")
            return path

    print("Could not find a patchable glfwInit() call. Diagnostics:")
    for path in candidate_glfw_sources(raylib_dir):
        text = read_text(path)
        if "glfw" in text.lower():
            print(f"--- {path}")
            for i, line in enumerate(text.splitlines(), 1):
                if "glfwInit" in line or "GLFW_ANGLE" in line:
                    print(f"{i}: {line}")
    raise RuntimeError("Could not find GLFW init insertion point. raylib platform layout changed.")


def copy_file(src: Path, dst: Path) -> None:
    assert_file(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def find_raylib_dll(build_dir: Path, configuration: str) -> Path:
    candidates = sorted(
        build_dir.rglob("raylib.dll"),
        key=lambda p: (configuration.lower() not in str(p).lower(), len(str(p))),
    )
    if not candidates:
        raise FileNotFoundError("raylib.dll was not produced.")
    return candidates[0]


def copy_vcpkg_runtime_dlls(vcpkg_bin: Path, out_dir: Path) -> list[str]:
    """Copy all dynamic-triplet runtime DLLs from vcpkg's bin folder.

    vcpkg documents that Windows dynamic triplets such as x64-windows require the
    needed DLL files to be next to the executable or on PATH.  Copying the full
    installed/<triplet>/bin set is intentionally conservative and avoids missing
    transitive DLLs such as zlib1.dll.
    """
    assert_dir(vcpkg_bin)
    copied: list[str] = []
    for dll in sorted(vcpkg_bin.glob("*.dll")):
        copy_file(dll, out_dir / dll.name)
        copied.append(dll.name)
    if not copied:
        raise RuntimeError(f"No runtime DLLs found in {vcpkg_bin}")
    return copied


def copy_visual_cpp_runtime_dlls(out_dir: Path) -> list[str]:
    """Copy VC++ runtime DLLs when available.

    The artifact is meant to run on machines that may not have the VC++
    Redistributable installed.  MSVC builds with the default dynamic CRT import
    vcruntime/msvcp DLLs, so package them when the runner exposes the redist dir.
    """
    copied: list[str] = []

    candidate_dirs: list[Path] = []
    redist_root = os.environ.get("VCToolsRedistDir")
    if redist_root:
        candidate_dirs.append(Path(redist_root) / "x64" / "Microsoft.VC143.CRT")
        candidate_dirs.append(Path(redist_root) / "x64" / "Microsoft.VC142.CRT")

    system32 = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"
    candidate_dirs.append(system32)

    names = [
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
        "concrt140.dll",
    ]

    for name in names:
        for directory in candidate_dirs:
            src = directory / name
            if src.is_file():
                copy_file(src, out_dir / name)
                copied.append(name)
                break

    if copied:
        print("Copied VC++ runtime DLLs: " + ", ".join(copied))
    else:
        print("WARNING: no VC++ runtime DLLs were copied. Target machines may need the VC++ Redistributable.")
    return copied


def main() -> int:
    root = repo_root_from_script()
    parser = argparse.ArgumentParser(description="Build raylib ANGLE Direct3D11 native DLL for raylib-cs.")
    parser.add_argument("--raylib-ref", default="6.0")
    parser.add_argument("--raylib-repo", default="https://github.com/raysan5/raylib.git")
    parser.add_argument("--vcpkg-root", default=os.environ.get("VCPKG_INSTALLATION_ROOT") or os.environ.get("VCPKG_ROOT") or r"C:\vcpkg")
    parser.add_argument("--triplet", default="x64-windows")
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--work-dir", default=str(root / "_build"))
    parser.add_argument("--out-dir", default=str(root / "artifacts" / "raylib-angle-win-x64"))
    args = parser.parse_args()

    work_dir = Path(args.work_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    raylib_dir = work_dir / "raylib"
    build_dir = work_dir / "raylib-build-angle"
    vcpkg_root = Path(args.vcpkg_root).resolve()

    assert_dir(vcpkg_root)
    angle_installed = vcpkg_root / "installed" / args.triplet
    angle_include = angle_installed / "include"
    angle_lib = angle_installed / "lib"
    angle_bin = angle_installed / "bin"
    for file in [
        angle_include / "EGL" / "egl.h",
        angle_include / "GLES2" / "gl2.h",
        angle_lib / "libEGL.lib",
        angle_lib / "libGLESv2.lib",
        angle_bin / "libEGL.dll",
        angle_bin / "libGLESv2.dll",
    ]:
        assert_file(file)

    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    if raylib_dir.exists():
        shutil.rmtree(raylib_dir)
    run(["git", "clone", "--depth", "1", "--branch", args.raylib_ref, args.raylib_repo, str(raylib_dir)])

    patch_library_config(raylib_dir)
    patched_glfw_file = patch_glfw_angle_hint(raylib_dir)

    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    toolchain = vcpkg_root / "scripts" / "buildsystems" / "vcpkg.cmake"
    assert_file(toolchain)

    run([
        "cmake",
        "-S", str(raylib_dir),
        "-B", str(build_dir),
        "-G", "Visual Studio 17 2022",
        "-A", "x64",
        f"-DCMAKE_TOOLCHAIN_FILE={toolchain}",
        f"-DVCPKG_TARGET_TRIPLET={args.triplet}",
        f"-DCMAKE_PREFIX_PATH={angle_installed}",
        f"-DCMAKE_INCLUDE_PATH={angle_include}",
        f"-DCMAKE_LIBRARY_PATH={angle_lib}",
        "-DPLATFORM=Desktop",
        "-DOPENGL_VERSION=ES 2.0",
        "-DBUILD_SHARED_LIBS=ON",
        "-DBUILD_EXAMPLES=OFF",
        "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded",
        "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
        "-DCMAKE_MINIMUM_REQUIRED_VERSION=3.15",
        "-DUSE_AUDIO=ON",
        f"-DCMAKE_BUILD_TYPE={args.configuration}",
    ])
    run(["cmake", "--build", str(build_dir), "--config", args.configuration, "--parallel"])

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    copy_file(find_raylib_dll(build_dir, args.configuration), out_dir / "raylib.dll")
    copied_vcpkg_dlls = copy_vcpkg_runtime_dlls(angle_bin, out_dir)

    d3d_compiler = angle_bin / "d3dcompiler_47.dll"
    if d3d_compiler.is_file():
        copy_file(d3d_compiler, out_dir / "d3dcompiler_47.dll")
    else:
        system_d3d = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "d3dcompiler_47.dll"
        if system_d3d.is_file():
            copy_file(system_d3d, out_dir / "d3dcompiler_47.dll")
        else:
            print("WARNING: d3dcompiler_47.dll not found. Some ANGLE deployments may need it.")

    copied_vc_runtime_dlls = copy_visual_cpp_runtime_dlls(out_dir)

    build_info = f"""raylib.ref={args.raylib_ref}
platform=Desktop
opengl.version=ES 2.0
graphics=GRAPHICS_API_OPENGL_ES2
context.api=GLFW_EGL_CONTEXT_API
client.api=GLFW_OPENGL_ES_API
angle.backend=GLFW_ANGLE_PLATFORM_TYPE_D3D11
angle.hint.patch.file={patched_glfw_file.relative_to(raylib_dir).as_posix()}
expected.raylib.import=libGLESv2.dll
expected.runtime.dynamic=libEGL.dll
cmake.msvc.runtime.library=MultiThreaded
cmake.policy.default.CMP0091=NEW
cmake.minimum.required.version=3.15
packaging.vcpkg.bin.dlls={",".join(copied_vcpkg_dlls)}
packaging.vc.runtime.dlls={",".join(copied_vc_runtime_dlls)}
note=libEGL.dll may be loaded dynamically by GLFW's EGL backend; it is not required to appear in dumpbin /dependents raylib.dll.
"""
    write_text(out_dir / "build-info.txt", build_info)

    print(f"ANGLE raylib output: {out_dir}")
    for item in sorted(out_dir.iterdir()):
        print(f"{item.name}\t{item.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
