#!/usr/bin/env python3
"""MomoAnalyze only startup entry for Windows and Android/Termux.

This single entrypoint starts the Python backend and the Vite frontend.
Do not use separate .bat or .sh launchers; configuration is read from config/app.json.

Windows usage:
    python start.py
    python start.py --install
    python start.py --fix-frontend
    python start.py --no-open

Android/Termux usage:
    python start.py
    python start.py --tmux
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
TERMUX_APP_DIRNAME = "app"
PY_DIR = ROOT / "py"
if str(PY_DIR) not in sys.path:
    sys.path.insert(0, str(PY_DIR))

from momo_config import (  # noqa: E402
    backend_host as config_backend_host,
    backend_port as config_backend_port,
    database_path as config_database_path,
    frontend_host as config_frontend_host,
    frontend_port as config_frontend_port,
)

TERMUX_RUNNER_PACKAGES = [
    "vite",
    "@vitejs/plugin-vue",
    "vue",
    "pinia",
    "element-plus",
    "@element-plus/icons-vue",
    "echarts",
    "plotly.js-dist-min",
    "vue-router",
]

TERMUX_RUNNER_INSTALL = [
    "vite@4.5.3",
    "@vitejs/plugin-vue@4.5.2",
    "vue@3",
    "pinia",
    "element-plus",
    "@element-plus/icons-vue",
    "echarts",
    "plotly.js-dist-min",
    "vue-router@4",
]

WINDOWS_STABLE_DEV_DEPS = ["vite@5.4.19", "@vitejs/plugin-vue@5.2.4"]
WINDOWS_EXTRA_DEPS = ["plotly.js-dist-min"]


def now() -> str:
    return time.strftime("[%H:%M:%S]")


def log(message: str) -> None:
    print(f"{now()} {message}", flush=True)


def is_windows() -> bool:
    return os.name == "nt"


def is_termux() -> bool:
    prefix = os.environ.get("PREFIX", "")
    return (
        "com.termux" in prefix
        or Path("/data/data/com.termux/files/usr").exists()
        or Path("/data/data/com.termux").exists()
    )


def exe(name: str) -> str | None:
    return shutil.which(name)


def npm_exe() -> str | None:
    return exe("npm.cmd") or exe("npm")


def node_exe() -> str | None:
    return exe("node.exe") or exe("node")


def quote_cmd(cmd: list[str]) -> str:
    import shlex

    if is_windows():
        return subprocess.list2cmdline(cmd)
    return " ".join(shlex.quote(str(part)) for part in cmd)


def run_checked(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    log("执行: " + quote_cmd(cmd))
    proc = subprocess.run(cmd, cwd=str(cwd), env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"命令失败，退出码 {proc.returncode}: {quote_cmd(cmd)}")


def read_tail(path: Path, lines: int = 80) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    parts = text.splitlines()
    return "\n".join(parts[-lines:])


def health_json(port: int, path: str = "/api/health", timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        return bool(data.get("success"))
    except Exception:
        return False


def probe_host(host: str) -> str:
    """Convert listen-only hosts to a real local address for health checks."""
    if host in ("0.0.0.0", "::", ""):
        return "127.0.0.1"
    return host


def health_http(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=timeout):
            return True
    except Exception:
        return False


def wait_for_backend(proc: subprocess.Popen, port: int, log_path: Path) -> bool:
    for _ in range(60):
        if proc.poll() is not None:
            log(f"后端已退出，退出码: {proc.returncode}")
            tail = read_tail(log_path, 120)
            if tail:
                log("后端日志最后内容:\n" + tail)
            return False
        if health_json(port, "/api/health", timeout=1.0):
            return True
        time.sleep(0.5)
    log("后端启动超时")
    tail = read_tail(log_path, 120)
    if tail:
        log("后端日志最后内容:\n" + tail)
    return False


def wait_for_frontend(proc: subprocess.Popen, host: str, port: int, log_path: Path) -> bool:
    check_host = probe_host(host)
    ready_markers = [
        "ready in",
        "Local:",
        f"http://localhost:{port}/",
        f"http://127.0.0.1:{port}/",
    ]

    for _ in range(60):
        if proc.poll() is not None:
            log(f"前端已退出，退出码: {proc.returncode}")
            tail = read_tail(log_path, 160)
            if tail:
                log("前端日志最后内容:\n" + tail)
            return False

        tail = read_tail(log_path, 80)
        if any(marker in tail for marker in ready_markers):
            return True

        if health_http(port, host=check_host, timeout=1.0):
            return True

        time.sleep(0.5)

    log(f"前端启动超时，检测地址: http://{check_host}:{port}/")
    tail = read_tail(log_path, 160)
    if tail:
        log("前端日志最后内容:\n" + tail)
    return False


def default_backend_host(termux: bool) -> str:
    return "0.0.0.0" if termux else "127.0.0.1"


def default_frontend_host(termux: bool) -> str:
    return "0.0.0.0" if termux else "127.0.0.1"


def resolve_path(value: str | Path | None, default: Path) -> Path:
    if value is None:
        return default.resolve()
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def ensure_base_files(db_path: Path) -> None:
    service_py = ROOT / "py" / "momo_service.py"
    if not service_py.exists():
        raise FileNotFoundError(f"后端入口不存在: {service_py}")
    if not db_path.exists():
        raise FileNotFoundError(
            "数据库不存在: "
            f"{db_path}\n"
            "默认路径是 data/momo.sqlite；请先迁移/生成数据库，或用 --db 指定路径。"
        )
    if not (ROOT / "package.json").exists():
        raise FileNotFoundError(f"package.json 不存在: {ROOT / 'package.json'}")


def ensure_common_commands() -> None:
    missing = []
    if not node_exe():
        missing.append("node")
    if not npm_exe():
        missing.append("npm")
    if missing:
        raise RuntimeError("缺少命令: " + ", ".join(missing))


def npm_install_project() -> None:
    npm = npm_exe()
    if not npm:
        raise RuntimeError("未找到 npm")
    run_checked([npm, "install"], cwd=ROOT)


def fix_frontend_dependencies() -> None:
    npm = npm_exe()
    if not npm:
        raise RuntimeError("未找到 npm")

    node_modules = ROOT / "node_modules"
    package_lock = ROOT / "package-lock.json"

    log("开始修复前端依赖：删除 node_modules 和 package-lock.json，然后安装稳定 Vite 5。")
    if node_modules.exists():
        log(f"删除: {node_modules}")
        shutil.rmtree(node_modules)
    if package_lock.exists():
        log(f"删除: {package_lock}")
        package_lock.unlink()

    run_checked([npm, "install"], cwd=ROOT)
    run_checked([npm, "install", "-D", *WINDOWS_STABLE_DEV_DEPS], cwd=ROOT)
    run_checked([npm, "install", *WINDOWS_EXTRA_DEPS], cwd=ROOT)
    log("前端依赖修复完成。")


def ensure_project_node_modules(install: bool) -> None:
    if (ROOT / "node_modules" / "vite").exists():
        return
    if install:
        npm_install_project()
        return
    raise RuntimeError(
        "未找到 node_modules/vite。请先运行: npm install\n"
        "也可以直接运行: python start.py --install"
    )


def copy_tree_clean(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("node_modules", "dist", ".vite", "__pycache__"))


def sync_termux_frontend_app(runner: Path, args: argparse.Namespace) -> Path:
    """Copy frontend files into Termux home before Vite starts."""
    app_dir = runner / TERMUX_APP_DIRNAME
    app_dir.mkdir(parents=True, exist_ok=True)

    for dirname in ["src", "public"]:
        src = ROOT / dirname
        target = app_dir / dirname
        if src.exists():
            copy_tree_clean(src, target)
        elif target.exists():
            shutil.rmtree(target)

    for filename in ["index.html", "package.json", "tsconfig.json"]:
        src = ROOT / filename
        if src.exists():
            shutil.copy2(src, app_dir / filename)

    config_src = ROOT / "config" / "app.json"
    if config_src.exists():
        config_dst_dir = app_dir / "config"
        config_dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config_src, config_dst_dir / "app.json")

    # Termux/Android 上 esbuild 加载复杂的临时配置文件时可能崩溃。
    # 这里生成普通 vite.config.js，并让 Vite 在 app_dir 下默认加载它。
    # 配置只保留必要项：Vue 插件、@ 别名、/api 代理。
    runtime_config = app_dir / "vite.config.js"
    runtime_config_text = f"""import {{ defineConfig }} from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'node:path'

const backendHost = process.env.BACKEND_HOST || {json.dumps(args.backend_host)}
const backendPort = Number(process.env.BACKEND_PORT || {args.backend_port})
const frontendHost = process.env.FRONTEND_HOST || {json.dumps(args.frontend_host)}
const frontendPort = Number(process.env.FRONTEND_PORT || {args.frontend_port})

export default defineConfig({{
  plugins: [vue()],
  resolve: {{
    alias: {{
      '@': path.resolve(process.cwd(), 'src'),
    }},
  }},
  server: {{
    host: frontendHost,
    port: frontendPort,
    proxy: {{
      '/api': {{
        target: `http://${{backendHost}}:${{backendPort}}`,
        changeOrigin: true,
      }},
    }},
  }},
}})
"""
    runtime_config.write_text(runtime_config_text, encoding="utf-8")

    old_runtime_config = app_dir / "vite.termux.runtime.config.mjs"
    if old_runtime_config.exists():
        old_runtime_config.unlink()

    return app_dir

def ensure_termux_runner(runner: Path, install: bool) -> None:
    npm = npm_exe()
    if not npm:
        raise RuntimeError("未找到 npm")
    runner.mkdir(parents=True, exist_ok=True)

    package_json = runner / "package.json"
    if not package_json.exists():
        run_checked([npm, "init", "-y"], cwd=runner)

    missing = [pkg for pkg in TERMUX_RUNNER_PACKAGES if not (runner / "node_modules" / pkg).exists()]
    if missing:
        if not install:
            log("Termux runner 依赖缺失，将自动安装: " + ", ".join(missing))
        run_checked([npm, "install", *TERMUX_RUNNER_INSTALL], cwd=runner)

    vite_bin = runner / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite_bin.exists():
        raise FileNotFoundError(f"Termux runner Vite 不存在: {vite_bin}")


def build_env(args: argparse.Namespace, runner: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["MOMO_PROJECT"] = str(ROOT)
    env["BACKEND_HOST"] = str(args.backend_host)
    env["BACKEND_PORT"] = str(args.backend_port)
    env["FRONTEND_HOST"] = str(args.frontend_host)
    env["FRONTEND_PORT"] = str(args.frontend_port)
    env["CI"] = "1"
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    if runner is not None:
        env["MOMO_RUNNER"] = str(runner)
        old_node_path = env.get("NODE_PATH", "")
        runner_node_modules = str(runner / "node_modules")
        env["NODE_PATH"] = runner_node_modules + (os.pathsep + old_node_path if old_node_path else "")
    return env


def backend_command(args: argparse.Namespace, db_path: Path) -> list[str]:
    return [
        sys.executable,
        "-u",
        str(ROOT / "py" / "momo_service.py"),
        "--root",
        str(ROOT),
        "--db",
        str(db_path),
        "--host",
        args.backend_host,
        "--port",
        str(args.backend_port),
    ]


def frontend_command(args: argparse.Namespace, termux: bool, runner: Path | None, termux_app: Path | None = None) -> list[str]:
    if termux:
        if runner is None:
            raise RuntimeError("Termux 启动缺少 runner")
        if termux_app is None:
            raise RuntimeError("Termux 启动缺少前端运行副本")
        vite_bin = runner / "node_modules" / "vite" / "bin" / "vite.js"
        return [
            node_exe() or "node",
            str(vite_bin),
            "--host",
            args.frontend_host,
            "--port",
            str(args.frontend_port),
            "--clearScreen",
            "false",
        ]

    npm = npm_exe()
    if not npm:
        raise RuntimeError("未找到 npm")
    script = "preview" if args.production else "dev"
    return [
        npm,
        "run",
        script,
        "--",
        "--host",
        args.frontend_host,
        "--port",
        str(args.frontend_port),
    ]


def open_browser(url: str, termux: bool) -> None:
    if termux:
        opened = False
        termux_open = exe("termux-open-url")
        if termux_open:
            try:
                subprocess.run([termux_open, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                opened = True
            except Exception:
                opened = False
        am = exe("am")
        if am:
            try:
                subprocess.run(
                    [am, "start", "-a", "android.intent.action.VIEW", "-d", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                opened = True
            except Exception:
                pass
        if not opened:
            log("无法自动打开浏览器，请手动打开: " + url)
        return

    webbrowser.open(url, new=2)


def stop_process(proc: subprocess.Popen, name: str) -> None:
    if proc.poll() is not None:
        return
    log(f"正在停止{name}...")
    try:
        if is_windows():
            proc.terminate()
        else:
            proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=3)
        except Exception:
            pass


def start_plain(args: argparse.Namespace, db_path: Path, termux: bool, runner: Path | None, termux_app: Path | None = None) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    backend_log = LOG_DIR / "backend.log"
    frontend_log = LOG_DIR / "frontend.log"

    env = build_env(args, runner)
    backend_cmd = backend_command(args, db_path)
    frontend_cmd = frontend_command(args, termux, runner, termux_app)

    log("启动 Python 后端...")
    log("后端命令: " + quote_cmd(backend_cmd))
    backend_out = backend_log.open("w", encoding="utf-8", errors="replace")
    backend = subprocess.Popen(
        backend_cmd,
        cwd=str(ROOT),
        env=env,
        stdout=backend_out,
        stderr=subprocess.STDOUT,
    )

    if not wait_for_backend(backend, args.backend_port, backend_log):
        stop_process(backend, "后端")
        backend_out.close()
        return 1
    log("后端就绪 ✓")

    log("启动 Vite 前端...")
    log("前端命令: " + quote_cmd(frontend_cmd))
    frontend_out = frontend_log.open("w", encoding="utf-8", errors="replace")
    frontend_cwd = termux_app if termux and termux_app is not None else ROOT
    frontend = subprocess.Popen(
        frontend_cmd,
        cwd=str(frontend_cwd),
        env=env,
        stdout=frontend_out,
        stderr=subprocess.STDOUT,
    )

    if not wait_for_frontend(frontend, args.frontend_host, args.frontend_port, frontend_log):
        stop_process(frontend, "前端")
        stop_process(backend, "后端")
        frontend_out.close()
        backend_out.close()
        return 1
    log("前端就绪 ✓")

    url = f"http://127.0.0.1:{args.frontend_port}/"
    log("访问地址: " + url)
    log("后端日志: " + str(backend_log))
    log("前端日志: " + str(frontend_log))
    if not args.no_open:
        open_browser(url, termux)
        log("已请求打开浏览器")

    log("按 Ctrl+C 停止所有服务。")
    try:
        while backend.poll() is None and frontend.poll() is None:
            time.sleep(1)
        if backend.poll() is not None:
            log(f"后端已退出，退出码: {backend.returncode}")
        if frontend.poll() is not None:
            log(f"前端已退出，退出码: {frontend.returncode}")
    except KeyboardInterrupt:
        log("收到 Ctrl+C，正在停止服务...")
    finally:
        stop_process(frontend, "前端")
        stop_process(backend, "后端")
        frontend_out.close()
        backend_out.close()
    log("所有服务已停止。")
    return 0


def start_tmux(args: argparse.Namespace, db_path: Path, runner: Path, termux_app: Path) -> int:
    if not exe("tmux"):
        raise RuntimeError("未找到 tmux。请安装 tmux，或直接运行: python start.py")

    import shlex

    env_prefix = " ".join(
        [
            f"MOMO_PROJECT={shlex.quote(str(ROOT))}",
            f"MOMO_RUNNER={shlex.quote(str(runner))}",
            f"BACKEND_HOST={shlex.quote(args.backend_host)}",
            f"BACKEND_PORT={shlex.quote(str(args.backend_port))}",
            f"FRONTEND_HOST={shlex.quote(args.frontend_host)}",
            f"FRONTEND_PORT={shlex.quote(str(args.frontend_port))}",
        ]
    )

    backend = quote_cmd(backend_command(args, db_path))
    frontend = quote_cmd(frontend_command(args, True, runner, termux_app))
    session = args.session

    subprocess.run(["tmux", "kill-session", "-t", session], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.check_call(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            session,
            f"sh -lc 'cd {shlex.quote(str(ROOT))} && echo ===== Python backend starting ===== && {backend}; echo; echo ===== Python backend exited. Error is above. =====; tail -f /dev/null'",
        ]
    )
    subprocess.check_call(
        [
            "tmux",
            "split-window",
            "-h",
            "-t",
            session,
            f"sh -lc 'cd {shlex.quote(str(termux_app))} && echo ===== Vue frontend starting ===== && {env_prefix} {frontend}; echo; echo ===== Vue frontend exited. Error is above. =====; tail -f /dev/null'",
        ]
    )

    url = f"http://127.0.0.1:{args.frontend_port}/"
    if not args.no_open:
        time.sleep(2)
        open_browser(url, True)
    log(f"tmux 会话已启动: {session}")
    log(f"访问地址: {url}")
    return subprocess.call(["tmux", "attach", "-t", session])


def parse_args() -> argparse.Namespace:
    backend_host_default = config_backend_host(ROOT)
    backend_port_default = config_backend_port(ROOT)
    frontend_host_default = config_frontend_host(ROOT)
    frontend_port_default = config_frontend_port(ROOT)
    db_default = str(config_database_path(ROOT))

    parser = argparse.ArgumentParser(description="MomoAnalyze 统一启动器：Windows 和 Termux 共用。")
    parser.add_argument("--db", default=os.environ.get("DB", db_default), help="SQLite 数据库路径，默认读取 config/app.json")
    parser.add_argument("--backend-host", default=os.environ.get("BACKEND_HOST", backend_host_default))
    parser.add_argument("--backend-port", type=int, default=int(os.environ.get("BACKEND_PORT", backend_port_default)))
    parser.add_argument("--frontend-host", default=os.environ.get("FRONTEND_HOST", frontend_host_default))
    parser.add_argument("--frontend-port", type=int, default=int(os.environ.get("FRONTEND_PORT", frontend_port_default)))
    parser.add_argument("--runner", default=os.environ.get("RUNNER"), help="Termux Vite runner 目录，默认 ~/momo-vite-runner")
    parser.add_argument("--session", default=os.environ.get("SESSION", "momo"), help="Termux tmux 会话名")
    parser.add_argument("--install", action="store_true", help="缺少前端依赖时自动安装")
    parser.add_argument("--fix-frontend", action="store_true", help="重装前端依赖并固定到 Windows 稳定 Vite 5")
    parser.add_argument("--production", action="store_true", help="Windows/桌面端使用 npm run preview，而不是 npm run dev")
    parser.add_argument("--tmux", action="store_true", help="Termux 下使用 tmux 分屏启动")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    termux = is_termux()
    db_path = resolve_path(args.db, ROOT / "data" / "momo.sqlite")
    runner = Path(args.runner).expanduser().resolve() if args.runner else (Path.home() / "momo-vite-runner")

    log("=" * 64)
    log("MomoAnalyze 统一启动器")
    log(f"系统: {platform.system()} {platform.release()}" + (" / Termux" if termux else ""))
    log(f"项目目录: {ROOT}")
    log(f"数据库: {db_path}")
    log(f"后端: http://{args.backend_host}:{args.backend_port}")
    log(f"前端: http://{args.frontend_host}:{args.frontend_port}")
    if termux:
        log(f"Termux runner: {runner}")
    log("=" * 64)

    try:
        ensure_base_files(db_path)
        ensure_common_commands()

        if args.fix_frontend:
            fix_frontend_dependencies()
            if not termux:
                log("修复完成。继续启动...")

        if termux:
            ensure_termux_runner(runner, args.install)
            termux_app = sync_termux_frontend_app(runner, args)
            log(f"Termux 前端运行副本: {termux_app}")
            if args.tmux:
                return start_tmux(args, db_path, runner, termux_app)
            return start_plain(args, db_path, termux=True, runner=runner, termux_app=termux_app)

        ensure_project_node_modules(args.install)
        return start_plain(args, db_path, termux=False, runner=None)
    except KeyboardInterrupt:
        log("已取消。")
        return 130
    except Exception as exc:
        log("启动失败: " + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
