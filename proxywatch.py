"""ProxyWatch: small cross-platform proxy monitor. Python + Tk + curl."""
import argparse
import collections
import ipaddress
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import webbrowser

APP = "ProxyWatch"
MODES = ("系统代理", "指定代理", "VPN / TUN")
DEFAULTS = dict(mode=MODES[0], proxy="http://127.0.0.1:7897", interval=300,
                target="https://www.gstatic.com/generate_204", expected_status=204,
                expected_ip="", topmost=True)


def config_path():
    base = Path(os.environ.get("APPDATA", str(Path.home()))) if os.name == "nt" else Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / APP / "settings.json"


def load_config():
    config = DEFAULTS.copy()
    try:
        saved = json.loads(config_path().read_text(encoding="utf-8"))
        config.update({k: saved[k] for k in DEFAULTS if k in saved})
        validate_config(config)
    except (OSError, ValueError, TypeError, KeyError):
        config = DEFAULTS.copy()
    return config


def validate_config(config):
    if config["mode"] not in MODES or config["interval"] not in (60, 300):
        raise ValueError("请选择代理模式和 1/5 分钟刷新间隔")
    parsed = urlsplit(config["target"])
    if parsed.scheme != "https" or not parsed.hostname or parsed.username:
        raise ValueError("检测目标须为不含账号密码的 HTTPS 地址")
    if not 200 <= int(config["expected_status"]) <= 299:
        raise ValueError("预期状态码须为 200–299")
    if config["expected_ip"]:
        ipaddress.ip_address(config["expected_ip"])
    if config["mode"] == MODES[1]:
        validate_proxy(config["proxy"])


def validate_proxy(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https", "socks5", "socks5h") or not parsed.hostname:
        raise ValueError("代理地址示例：http://127.0.0.1:7897 或 socks5h://127.0.0.1:1080")
    if parsed.port is None:
        raise ValueError("代理地址需要端口")
    # Resolve destination names through SOCKS5 to match the proxy route.
    return "socks5h://" + value[len("socks5://"):] if parsed.scheme == "socks5" else value


def system_proxy():
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
                try:
                    enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
                except OSError:
                    enabled = 0
                if enabled:
                    server = winreg.QueryValueEx(key, "ProxyServer")[0]
                    if "=" in server:
                        values = dict(item.split("=", 1) for item in server.split(";") if "=" in item)
                        server = values.get("https") or values.get("http")
                        if not server:
                            raise ValueError("系统代理未配置 HTTP/HTTPS；请指定代理地址")
                    return validate_proxy(server if "://" in server else "http://" + server)
                try:
                    pac = winreg.QueryValueEx(key, "AutoConfigURL")[0]
                except OSError:
                    pac = ""
                if pac:
                    raise ValueError("系统使用 PAC；请在设置中指定 HTTP/SOCKS5 地址")
        except OSError:
            pass
    for key in ("https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY", "http_proxy", "HTTP_PROXY"):
        value = os.environ.get(key)
        if value:
            return validate_proxy(value)
    raise ValueError("未找到系统代理；请指定地址，或选择 VPN / TUN")


def selected_proxy(config):
    if config["mode"] == MODES[2]:
        return ""  # No application proxy; operating system routes via VPN/TUN.
    return system_proxy() if config["mode"] == MODES[0] else validate_proxy(config["proxy"])


def curl_path():
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / ("curl.exe" if os.name == "nt" else "curl")
    return str(bundled) if bundled.is_file() else shutil.which("curl.exe" if os.name == "nt" else "curl")


def request(url, config, timeout=4, head=False):
    executable = curl_path()
    if not executable:
        raise ValueError("未找到 curl；Linux 请安装 curl，Windows 请使用提供的便携包")
    proxy = selected_proxy(config)
    args = [executable, "--disable", "--silent", "--show-error", "--connect-timeout", "3",
            "--max-time", str(timeout), "--max-filesize", "65536", "--proxy", proxy,
            "--noproxy", "*" if config["mode"] == MODES[2] else "",
            "--user-agent", "curl/ProxyWatch-1.0", "--write-out", "\n__PW__%{http_code}|%{time_total}"]
    if head:
        args += ["--head"]
    args += ["--url", url]
    env = os.environ.copy()
    for key in list(env):
        if key.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            env.pop(key)
    result = subprocess.run(args, capture_output=True, timeout=timeout + 2,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                            env=env)
    if result.returncode:
        # Avoid displaying proxy credentials from diagnostic output.
        errors = {5: "代理域名解析失败", 6: "目标域名解析失败", 7: "无法连接代理或目标",
                  28: "请求超时", 35: "TLS 握手失败", 60: "TLS 证书校验失败"}
        raise ValueError(errors.get(result.returncode, "连接失败（curl %d）" % result.returncode))
    body, marker, metadata = result.stdout.decode("utf-8", errors="replace").rpartition("\n__PW__")
    if not marker:
        raise ValueError("响应格式异常")
    status, elapsed = metadata.strip().split("|")
    return body, int(status), float(elapsed) * 1000


def parse_geo(body):
    lines = [line.strip() for line in body.strip().splitlines()]
    if len(lines) < 4:
        raise ValueError("ping0 返回异常；稍后重试")
    ipaddress.ip_address(lines[0])
    if not lines[2].startswith("AS"):
        raise ValueError("ping0 返回异常；稍后重试")
    return dict(ip=lines[0], location=lines[1], asn=lines[2], org=lines[3])


class Monitor:
    def __init__(self, config):
        self.config = config.copy()
        self.generation = 0
        self.lock = threading.Lock()
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.refresh = threading.Event()

    def start(self):
        for kind in ("latency", "geo"):
            threading.Thread(target=self.loop, args=(kind,), daemon=True).start()

    def update(self, config):
        with self.lock:
            self.config = config.copy()
            self.generation += 1
        self.refresh.set()

    def loop(self, kind):
        next_run = 0
        last_generation = -1
        while not self.stop.is_set():
            with self.lock:
                config, generation = self.config.copy(), self.generation
            forced = kind == "geo" and self.refresh.is_set()
            if generation != last_generation:
                next_run = 0
                last_generation = generation
            if time.monotonic() < next_run and not forced:
                self.stop.wait(0.1)
                continue
            if kind == "geo":
                self.refresh.clear()
            started = time.monotonic()
            try:
                if kind == "geo":
                    body, status, _ = request("https://ping0.cc/geo", config, timeout=10)
                    if status != 200:
                        raise ValueError("ping0 HTTP %d" % status)
                    data = parse_geo(body)
                else:
                    _, status, elapsed = request(config["target"], config, head=True)
                    if status != int(config["expected_status"]):
                        raise ValueError("目标 HTTP %d（预期 %s）" % (status, config["expected_status"]))
                    data = {"ms": elapsed}
                self.events.put((generation, kind, True, data, time.time()))
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                self.events.put((generation, kind, False, str(error), time.time()))
            period = config["interval"] if kind == "geo" else 1
            next_run = started + period
            if next_run <= time.monotonic():
                # Never accumulate requests while a previous probe is still running.
                next_run = time.monotonic() + (0.1 if kind == "latency" else period)


class Widget:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk, self.root = tk, ttk, root
        self.config = load_config()
        self.monitor = Monitor(self.config)
        self.samples = collections.deque(maxlen=60)
        self.geo = None
        self.geo_ok = False
        self.geo_time = 0
        self.latency_ok = None
        self.last_error = "等待检测"
        self.settings_window = None
        root.title("ProxyWatch · 代理监测")
        root.geometry("390x450")
        root.minsize(390, 430)
        root.configure(bg="#111827")
        root.attributes("-topmost", bool(self.config["topmost"]))
        self.status = self.label("正在检测…", 17, "#fbbf24")
        self.route = self.label(self.config["mode"], 10, "#94a3b8")
        self.ip_label = self.label("出口 IP：—", 16)
        self.location = self.label("归属地：—", 11)
        self.owner = self.label("ASN / 运营商：—", 10, "#94a3b8")
        self.delay = self.label("HTTPS 响应延时：—", 15)
        self.stats = self.label("最近 60 次失败率：—", 10, "#94a3b8")
        self.chart = tk.Canvas(root, height=55, bg="#111827", highlightthickness=0)
        self.chart.pack(fill="x", padx=16, pady=3)
        self.detail = self.label("", 9, "#94a3b8")
        buttons = tk.Frame(root, bg="#111827")
        buttons.pack(fill="x", padx=14, pady=8)
        for title, command in (("设置", self.settings), ("刷新 IP", self.monitor.refresh.set),
                               ("ping0 详情", self.open_details)):
            ttk.Button(buttons, text=title, command=command).pack(side="left", expand=True, fill="x", padx=2)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.monitor.start()
        root.after(100, self.tick)

    def label(self, value, size, color="#e5e7eb"):
        label = self.tk.Label(self.root, text=value, font=("Segoe UI", size), bg="#111827",
                              fg=color, anchor="w", justify="left", wraplength=360)
        label.pack(fill="x", padx=16, pady=3)
        return label

    def tick(self):
        try:
            while True:
                generation, kind, ok, data, stamp = self.monitor.events.get_nowait()
                if generation != self.monitor.generation:
                    continue
                if kind == "geo":
                    self.geo_ok = ok
                    if ok:
                        self.geo, self.geo_time = data, stamp
                        self.ip_label.config(text="出口 IP：" + data["ip"])
                        self.location.config(text=data["location"])
                        self.owner.config(text=data["asn"] + " · " + data["org"])
                        self.detail.config(text="IP 更新 " + time.strftime("%H:%M:%S", time.localtime(stamp)))
                    else:
                        self.detail.config(text="IP 检测失败：" + data + ("（显示上次结果）" if self.geo else ""))
                else:
                    self.latency_ok = ok
                    self.last_error = "" if ok else data
                    self.samples.append(data["ms"] if ok else None)
                    self.delay.config(text="HTTPS 响应延时：%.0f ms" % data["ms"] if ok else "HTTPS 响应延时：失败")
                    failures = sum(value is None for value in self.samples)
                    self.stats.config(text="最近 %d 次失败率：%.0f%% · 每秒尝试一次" % (len(self.samples), failures / len(self.samples) * 100))
                    self.draw_chart()
        except queue.Empty:
            pass
        if self.latency_ok is False:
            value, color = "链路异常 · " + self.last_error, "#f87171"
        elif self.latency_ok is None:
            value, color = "正在检测…", "#fbbf24"
        elif not self.geo_ok:
            value, color = "目标可达 · IP 待确认", "#fbbf24"
        elif self.config["expected_ip"] and self.geo["ip"] != self.config["expected_ip"]:
            value, color = "出口 IP 与预期不符", "#f87171"
        elif time.time() - self.geo_time > self.config["interval"] + 20:
            value, color = "目标可达 · IP 信息已过期", "#fbbf24"
        else:
            value = "链路可达 · 出口符合预期" if self.config["expected_ip"] else "链路可达 · 出口已更新"
            color = "#34d399"
        self.status.config(text=value, fg=color)
        self.root.after(100, self.tick)

    def draw_chart(self):
        self.chart.delete("all")
        width = max(self.chart.winfo_width(), 100)
        maximum = max([v for v in self.samples if v is not None] + [100])
        for index, value in enumerate(self.samples):
            x = width * index / 60
            height = 48 if value is None else max(2, value / maximum * 48)
            self.chart.create_rectangle(x, 53 - height, x + max(2, width / 60 - 2), 53,
                                        fill="#f87171" if value is None else "#38bdf8", outline="")

    def open_details(self):
        webbrowser.open("https://ping0.cc/ip/" + self.geo["ip"] if self.geo else "https://ping0.cc")

    def settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        tk, ttk = self.tk, self.ttk
        from tkinter import messagebox
        window = self.settings_window = tk.Toplevel(self.root)
        window.title("ProxyWatch 设置")
        window.resizable(False, False)
        variables = {key: tk.StringVar(value=str(self.config[key])) for key in
                     ("mode", "proxy", "target", "expected_status", "expected_ip")}
        interval = tk.StringVar(value="1 分钟" if self.config["interval"] == 60 else "5 分钟")
        topmost = tk.BooleanVar(value=self.config["topmost"])
        rows = (("代理模式", "mode"), ("代理地址", "proxy"), ("检测目标 HTTPS", "target"),
                ("预期 HTTP 状态码", "expected_status"), ("预期出口 IP（可选）", "expected_ip"))
        for row, (title, key) in enumerate(rows):
            ttk.Label(window, text=title).grid(row=row, column=0, sticky="w", padx=10, pady=7)
            control = ttk.Combobox(window, textvariable=variables[key], values=MODES, state="readonly", width=37) if key == "mode" else ttk.Entry(window, textvariable=variables[key], width=40)
            control.grid(row=row, column=1, padx=10, pady=7)
        ttk.Label(window, text="IP 刷新间隔").grid(row=5, column=0, sticky="w", padx=10)
        ttk.Combobox(window, textvariable=interval, values=("1 分钟", "5 分钟"), state="readonly").grid(row=5, column=1, sticky="w", padx=10)
        ttk.Checkbutton(window, text="窗口置顶", variable=topmost).grid(row=6, column=1, sticky="w", padx=10, pady=7)
        ttk.Label(window, text="系统模式读取 Windows 静态代理 / 环境变量；PAC、Linux 桌面代理请手动填地址。\nVPN/TUN 模式跟随系统路由；可填写预期出口 IP 辅助确认。\n延时包含连接、TLS 和服务器响应时间，不等同于 ICMP ping。\n含账号密码的代理地址只在本次运行使用，不保存。").grid(row=7, column=0, columnspan=2, padx=10, pady=8, sticky="w")

        def apply():
            config = {key: value.get().strip() for key, value in variables.items()}
            config.update(interval=60 if interval.get() == "1 分钟" else 300, topmost=topmost.get())
            try:
                config["expected_status"] = int(config["expected_status"])
                validate_config(config)
            except (ValueError, TypeError) as error:
                messagebox.showerror("设置无效", str(error), parent=window)
                return
            persisted = config.copy()
            if urlsplit(config["proxy"]).username is not None:
                persisted["proxy"] = ""
            try:
                path = config_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as error:
                messagebox.showwarning("设置仅本次有效", "无法保存设置：" + str(error), parent=window)
            self.config = config
            self.samples.clear()
            self.geo = None
            self.geo_ok = False
            self.latency_ok = None
            self.ip_label.config(text="出口 IP：—")
            self.location.config(text="归属地：—")
            self.owner.config(text="ASN / 运营商：—")
            self.delay.config(text="HTTPS 响应延时：—")
            self.stats.config(text="最近 60 次失败率：—")
            self.detail.config(text="等待新的检测结果")
            self.draw_chart()
            self.route.config(text=config["mode"])
            self.root.attributes("-topmost", config["topmost"])
            self.monitor.update(config)
            window.destroy()
        ttk.Button(window, text="应用并重新检测", command=apply).grid(row=8, column=0, columnspan=2, pady=10)

    def close(self):
        self.monitor.stop.set()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="ProxyWatch 代理出口 / HTTPS 延时监测")
    parser.add_argument("--check", action="store_true", help="终端执行一次检测，不启动图形窗口")
    parser.add_argument("--proxy", help="HTTP / SOCKS5 代理地址")
    parser.add_argument("--tun", action="store_true", help="使用系统 VPN / TUN 路由")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    config = load_config()
    if args.proxy:
        config.update(mode=MODES[1], proxy=args.proxy)
    elif args.tun:
        config["mode"] = MODES[2]
    if args.check:
        try:
            validate_config(config)
            body, status, _ = request("https://ping0.cc/geo", config, timeout=10)
            if status != 200:
                raise ValueError("ping0 HTTP %d" % status)
            geo = parse_geo(body)
            _, status, ms = request(config["target"], config, head=True)
            print(json.dumps(dict(geo, https_status=status, latency_ms=round(ms, 1)), ensure_ascii=False))
            return 0 if status == int(config["expected_status"]) and (not config["expected_ip"] or config["expected_ip"] == geo["ip"]) else 1
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            print(str(error), file=sys.stderr)
            return 1
    import tkinter as tk
    root = tk.Tk()
    if args.smoke_test:
        # Verify the packaged Tcl/Tk runtime without leaving a window or network workers.
        root.withdraw()
        Monitor.start = lambda self: None
    widget = Widget(root)
    if args.proxy or args.tun:
        widget.config = config
        widget.monitor.update(config)
        widget.route.config(text=config["mode"])
    if args.smoke_test:
        root.update_idletasks()
        widget.settings()
        root.update_idletasks()
        widget.close()
        return 0
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
