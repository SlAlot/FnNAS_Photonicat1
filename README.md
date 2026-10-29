# 光影猫一代 · 飞牛 ARM 适配

[![Build](https://github.com/SlAlot/FnNAS_Photonicat1/actions/workflows/build.yml/badge.svg)](https://github.com/SlAlot/FnNAS_Photonicat1/actions/workflows/build.yml)

面向 **Photonicat 1 / RK3568** 的飞牛 ARM 社区适配，由 **SlAlot** 维护，基于 [ophub/fnnas](https://github.com/ophub/fnnas) 的 `fnnas_rockchip_1253` 镜像与官方 `6.18.18-trim` 内核。不适用于光影猫二代。

**当前为适配测试阶段。已进行一台真机的有线网络和 5G 联网验证；不同模组、长期运行和完整硬件功能仍需测试。**

[测试固件](https://github.com/SlAlot/FnNAS_Photonicat1/releases) · [自动构建](https://github.com/SlAlot/FnNAS_Photonicat1/actions/workflows/build.yml) · [问题反馈](https://github.com/SlAlot/FnNAS_Photonicat1/issues)

## 解决的问题

现有镜像能启动飞牛，但有线网口出现 PHY 挂接错误：

| 接口 | 现象 | 本项目处理 |
| --- | --- | --- |
| eth0 / RGMII | MDIO 地址 3 找不到 PHY，启用返回 `-ENODEV` | 按官方板级方案将 PHY 复位放在 MDIO 探测前 |
| eth1 / SGMII | 能力校验失败，启用返回 `-EINVAL` | 从源码编译 Rockchip 网口模块，补齐 SGMII 能力声明与初始化 |

保留原 PHY 地址、GPIO 与复位延时，保留飞牛官方内核、Btrfs 和文件权限功能；同步更新 initramfs，避免早期启动继续加载旧模块。

固定版本和摘要见 [sources.lock.json](sources.lock.json)，源码来源及许可见 [vendor/UPSTREAM.md](vendor/UPSTREAM.md)。

## 下载与刷入

1. 从 Releases 下载标注为**测试版**的 `.img.gz`，核对同次发布的 `sha256sums`。
2. 备份当前系统、配置、容器和重要数据。测试时确保不依赖光影猫供网。
3. 将 `.img.gz` 解压为 `.img`，按[光影猫官方教程](https://photonicat.com/wiki/Photonicat_刷机操作手册)使用 RKDevTool 和一代 Loader，完整镜像写入地址为 `0x00000000`。
4. 电脑用网线连接光影猫 **LAN 口**，电脑设为自动获取 IP，访问 **`http://192.168.17.1:5666`** 完成飞牛初始化。

不可将本镜像上传到 OpenWrt 的 sysupgrade 页面。此前不含网络共享配置的旧测试镜像仍需连接上级路由器获取地址。

## 5G 上网与电脑直连

| 接口 | 默认用途 |
| --- | --- |
| LAN / `pcatlan0` | `192.168.17.1/24`，为电脑提供 DHCP、DNS 与 IPv4 网络共享 |
| 内置 5G 模组 | USB ECM / RNDIS / NCM 模式自动获取地址，优先用于上网 |
| WAN / `pcatwan0` | 向上级路由器获取地址；5G 链路断开后可作为备用出口 |

无需外接路由器即可通过 LAN 打开管理页。模组已联网时，LAN 客户端通过 NAT 共享上网；模组尚未联网时仍可访问本地管理页。**WAN 口用于接上级网络，LAN 口用于接电脑或下级交换机。**

两个有线口按硬件控制器命名，避免 `eth0/eth1` 顺序变化导致 DHCP 开错端口。默认配置只随刷机预置，不在每次启动时覆盖用户设置。

飞牛网络设置中，LAN 使用 **「LAN 地址（提供 DHCP）」**。可以修改局域网 IP、掩码、DNS 和 MTU，独立的 DHCP / NAT 服务随 LAN 地址调整；LAN 不作为上级 DHCP 客户端、默认出口或 OVS 端口。更改 IP 后，请让电脑重新获取地址，再用新地址的 `:5666` 端口访问。

已针对 RM500U-CNV 的 NCM 自联网模式处理 ModemManager 接管冲突，避免电脑能上网但 NAS 丢失 DNS。其他模组仍按原有方式管理；不修改模组的 AT 配置和拨号模式。

当前为 IPv4 共享，不额外开启 Wi-Fi 热点。5G 模组需已处于可通过 USB 网卡获取地址的联网模式；原始 QMI / MBIM 模式的拨号、APN 和 SIM 配置尚未覆盖。`192.168.100.1` 是模组侧地址，不是 LAN 管理地址。

## GPU 信息

设备信息页补充显示内核实际检测到的 Arm Mali-G52 型号；未加载驱动时不伪造型号。网页适配通过独立资源与 Nginx 配置扩展接入，保留飞牛原始网页文件及校验机制。上游网页版本不匹配时自动保留原始界面。

**型号显示不等于硬件加速已验证。** GPU 图形计算和 MPP 视频编解码是不同部分，当前未把 OpenGL / Vulkan 或影视转码作为已验证能力。

## 验证与反馈

首轮重点检查两个网口、LAN 地址分配、5G 共享上网、DNS、千兆协商、持续传输和重启。后续还需验证电池供电、MCU 心跳、正常关机、USB 磁盘、TF / eMMC、无线和不同 5G 模组。

测试镜像内可运行只读诊断：

```sh
sudo photonicat-diagnostics > photonicat-network.txt 2>&1
```

反馈时附上镜像名称、硬件版本和诊断输出，公开前隐去个人信息。**测试适配尚未覆盖飞牛内核 OTA，更新内核可能覆盖网口模块；当前不把 OTA 后继续可用作为已验证能力。**

## 自动构建

推送构建代码到 `main` 自动执行检查、模块编译和测试镜像打包；成功后发布 **Pre-release 测试版**。也可在 Actions 手动运行 **Build Photonicat FnNAS**，选择是否发布测试版。Pull Request 仅运行检查。

流程包括：

1. 核对上游镜像、内核包和 Linux 源文件摘要，来源变化时停止构建。
2. 执行接口能力和设备树回归，使用对应内核头文件在原生 ARM64 编译，核对内核标识和导入符号。当前飞牛内核未启用模块符号 CRC，仍需真机验证运行时兼容性。
3. 仅在构建机的镜像 loop 设备中修改根分区、启动分区和 initramfs；核对原始模块与设备树，保留镜像内恢复副本。
4. 在隔离网络命名空间中使用镜像自带的 NetworkManager，实测离线 LAN 分配地址、NAS 本机 DNS、客户端 DNS / NAT、出口切换、保存 LAN 设置及修改子网；测试临时修改不写入固件。
5. 检查启动区与分区表未改变，输出测试镜像、SHA-256 清单及对应模块源码。

本地检查使用 Linux，安装 GCC、device-tree-compiler 后执行：

```sh
python3 scripts/fetch-inputs.py sources work/source
python3 -m unittest discover -s tests -v
```

## 来源与许可

感谢 [FnNAS](https://www.fnnas.com/)、[ophub/fnnas](https://github.com/ophub/fnnas)、[Linux](https://github.com/gregkh/linux) 和[光影猫官方](https://github.com/photonicat/rockchip_rk3568_linux_mainline)的贡献者。

本仓库使用 [GPL-2.0](LICENSE)，保留上游补丁和组件原有版权与许可。飞牛系统及专有组件遵循各自条款；本项目为社区适配，不代表飞牛或光影猫官方发行。
