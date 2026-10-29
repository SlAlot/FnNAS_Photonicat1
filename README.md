# 光影猫一代 · 飞牛 ARM 适配

[![Build](https://github.com/SlAlot/FnNAS_Photonicat1/actions/workflows/build.yml/badge.svg)](https://github.com/SlAlot/FnNAS_Photonicat1/actions/workflows/build.yml)

面向 **Photonicat 1 / RK3568** 的飞牛 ARM 社区适配，由 **SlAlot** 维护，基于 [ophub/fnnas](https://github.com/ophub/fnnas) 的 `fnnas_rockchip_1253` 镜像与官方 `6.18.18-trim` 内核。不适用于光影猫二代。

**当前为适配测试阶段。构建通过仅代表编译和静态检查通过，尚待真机验收。**

[测试固件](https://github.com/SlAlot/FnNAS_Photonicat1/releases) · [自动构建](https://github.com/SlAlot/FnNAS_Photonicat1/actions/workflows/build.yml) · [问题反馈](https://github.com/SlAlot/FnNAS_Photonicat1/issues)

## 解决的问题

现有镜像能启动飞牛，但有线网口出现 PHY 挂接错误：

| 接口 | 现象 | 本项目处理 |
| --- | --- | --- |
| eth0 / RGMII | MDIO 地址 3 找不到 PHY，启用返回 `-ENODEV` | 按官方板级方案将 PHY 复位放在 MDIO 探测前；需真机验证 |
| eth1 / SGMII | 能力校验失败，启用返回 `-EINVAL` | 从源码编译 Rockchip 网口模块，补齐 SGMII 能力声明与初始化 |

保留原 PHY 地址、GPIO 与复位延时。只替换网口模块及对应设备树，保留飞牛官方内核、Btrfs 和文件权限功能；同步更新 initramfs，避免早期启动继续加载旧模块。

固定版本和摘要见 [sources.lock.json](sources.lock.json)，源码来源及许可见 [vendor/UPSTREAM.md](vendor/UPSTREAM.md)。

## 下载与刷入

1. 从 Releases 下载标注为**测试版**的 `.img.gz`，核对同次发布的 `sha256sums`。
2. 备份当前系统、配置、容器和重要数据。测试时确保不依赖光影猫供网。
3. 将 `.img.gz` 解压为 `.img`，按[光影猫官方教程](https://photonicat.com/wiki/Photonicat_刷机操作手册)使用 RKDevTool 和一代 Loader，完整镜像写入地址为 `0x00000000`。
4. 启动后连接上级路由器，获取 IP，访问 `http://IP:5666` 完成飞牛初始化。

飞牛默认向上级路由器申请地址，不是 OpenWrt DHCP 服务器。`usb0` 上从 5G 模组拿到的地址不等于有线 LAN 地址。不可将本镜像上传到 OpenWrt 的 sysupgrade 页面。

## 验证与反馈

首轮重点检查两个网口：冷启动、手动启用、DHCP、千兆协商、持续传输和重启。后续还需验证电池供电、MCU 心跳、正常关机、USB 磁盘、TF / eMMC、无线和 5G 模组。

测试镜像内可运行只读诊断：

```sh
sudo photonicat-diagnostics > photonicat-network.txt 2>&1
```

反馈时附上镜像名称、硬件版本和诊断输出，公开前隐去个人信息。**测试适配尚未覆盖飞牛内核 OTA，更新内核可能覆盖网口模块；当前不把 OTA 后继续可用作为已验证能力。**

## 自动构建

推送构建代码到 `main` 自动执行检查、模块编译和测试镜像打包；成功后发布 **Pre-release 测试版**。也可在 Actions 手动运行 **Build Photonicat FnNAS**，选择是否发布测试版。Pull Request 仅运行检查。

流程包括：

1. 核对上游镜像、内核包和 Linux 源文件摘要，来源变化时停止构建。
2. 执行接口能力和设备树回归，原生 ARM64 编译网口模块，核对内核版本与符号 CRC。
3. 仅在构建机的镜像 loop 设备中修改根分区、启动分区和 initramfs；核对原始模块与设备树，保留镜像内恢复副本。
4. 检查启动区与分区表未改变，输出测试镜像、SHA-256 清单及对应模块源码。

本地检查使用 Linux，安装 GCC、device-tree-compiler 后执行：

```sh
python3 scripts/fetch-inputs.py sources work/source
python3 -m unittest discover -s tests -v
```

## 来源与许可

感谢 [FnNAS](https://www.fnnas.com/)、[ophub/fnnas](https://github.com/ophub/fnnas)、[Linux](https://github.com/gregkh/linux) 和[光影猫官方](https://github.com/photonicat/rockchip_rk3568_linux_mainline)的贡献者。

本仓库使用 [GPL-2.0](LICENSE)，保留上游补丁和组件原有版权与许可。飞牛系统及专有组件遵循各自条款；本项目为社区适配，不代表飞牛或光影猫官方发行。
光影猫一代 Photonicat 1 / RK3568 飞牛 ARM 适配与自动构建，基于 ophub/fnnas。
