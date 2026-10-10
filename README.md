# ProxyDetection / ProxyWatch

一个用于确认代理链路和出口 IP 的桌面小部件。免费调用 ping0.cc，支持系统代理、指定 HTTP/SOCKS5 代理、VPN/TUN 系统路由。

## 直接使用 Windows 版

- [下载完整便携包（exe、源码和说明）](https://github.com/Beiyouanxiang/ProxyDetection/raw/refs/heads/main/packages/ProxyWatch-Windows-x64.zip)
- [详细使用说明](使用说明.md)

解压便携包后，双击 `ProxyWatch.exe`，无需安装 Python。面向 Windows 10/11 x64，依赖系统自带 curl。本次已在 Windows 11 构建并检查启动。为避免重复存放二进制文件，仓库只在便携包内保存 exe。

## 已有功能

- 出口 IP 和归属地，每 1 或 5 分钟刷新。
- 每 6 秒发起一次 HTTPS 检测，显示响应延时和最近 60 次失败率；连续失败 10 次（约一分钟）会响铃并显示告警，检测成功后重置。
- 窗口可自由缩放，字体和内容换行会随窗口宽度调整，图表会利用可用空间；Windows 使用紧凑标题栏。支持拖动、置顶和最小化。
- 三种代理模式可切换；指定代理请求不自动回退直连。
- 可填写预期出口 IP，出口不符时显示异常。
- 默认目标是 `https://www.gstatic.com/generate_204`，预期 HTTP 204，可在设置中修改。

这里的延时包括连接、TLS 和服务器响应耗时，不是 ICMP ping。上一次请求未完成时不会堆积请求，慢请求会使间隔超过六秒。检测成功表示目标可达，不代表所有网站可达；TUN 分流下不同域名也可能有不同出口。

免费接口不提供风控值、原生 IP 等完整字段；本项目未接入付费 API。

## 流量消耗

当前每次检测调用独立 curl 进程并新建 HTTPS 连接，只请求响应头，不下载测速文件。每 6 秒一次的累计开销主要来自 TLS/代理握手。

按每次 **5–20 KB** 粗略估算：

| 运行时间 | 估算消耗 |
| --- | ---: |
| 1 小时 | 18–72 MB |
| 8 小时 | 144–576 MB |
| 24 小时 | 432 MB–1.73 GB |

这是估算而非实测，实际取决于证书链、代理协议、超时、实际请求频率和网络开销；成功请求耗时超过六秒时，发起次数会减少。ping0 的低频查询通常只占较小部分。

后续可通过复用连接、增加可调检测间隔来减少流量，目前 exe 尚未包含这两项优化。

按同样单次开销估算，每 6 秒检测时 8 小时约 24–96 MB。

## 换电脑继续开发

```bash
git clone git@github.com:Beiyouanxiang/ProxyDetection.git
cd ProxyDetection
```

源码入口为 `proxywatch.py`；开发电脑需要 Python 3.8+、Tk 和 curl。

```bash
python proxywatch.py
python -m unittest discover -s tests -v
```

Windows 重建：在 PowerShell 运行 `./build-windows.ps1`，生成 `dist/ProxyWatch.exe`。脚本会在项目内创建虚拟环境并下载 PyInstaller。重建后，需要手动更新 `packages/` 中的便携包。

Linux 重建：须在目标 Linux 兼容环境内构建，并验证 curl/Tk 动态库和 glibc 兼容性。

CentOS 7 可使用项目内的 Conda 环境，不改系统 Python：

```bash
sudo yum install -y curl git
git clone https://github.com/Beiyouanxiang/ProxyDetection.git
cd ProxyDetection
eval "$(conda shell.bash hook)"
conda env create --prefix ./.conda-linux --file environment-linux.yml
conda activate ./.conda-linux
bash build-linux.sh
./dist/ProxyWatch --smoke-test
./dist/ProxyWatch
```

环境定义保存在 `environment-linux.yml`；实际 Conda 环境位于 `.conda-linux/` 并由 Git 忽略。此环境须在 CentOS/Linux 上创建，不能用 Windows Conda 环境代替。

CentOS 目标为 CentOS 7、x86_64、GNOME。Linux 可执行文件尚未在该目标系统上构建和验证；Windows exe 不能直接在 CentOS 上运行。

[开发交接与待办](DEVELOPMENT.md)
