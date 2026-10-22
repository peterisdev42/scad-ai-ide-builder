# OpenSCAD 2021.01 无头 CLI 打包

工作流：`.github/workflows/openscad-cli.yml`，名称 **Package OpenSCAD CLI**。
在 GitHub Actions 页面选择 **Run workflow** 即可启动。在默认分支修改此工作流或
`scripts/openscad-ci/` 时也会自动执行；其他分支只生成 Actions artifacts。
默认分支上的八组构建全部成功后，自动创建或更新 GitHub Release
`openscad-cli-2021.01`。重复执行会替换同名附件。无需额外配置 PAT，发布 job 使用
仓库 `GITHUB_TOKEN` 的 `contents: write` 权限。

| 操作系统 | 架构 | 构建方式 |
| --- | --- | --- |
| Linux | x64 | Ubuntu 22.04 容器，原生执行 |
| Linux | ARM64 | Ubuntu 22.04 容器，ARM64 runner 原生执行 |
| Linux | ARMv7 hard-float | Ubuntu 22.04 arm/v7 容器，QEMU 执行 |
| macOS | Intel x64 | Intel runner 原生执行 |
| macOS | Apple Silicon ARM64 | ARM64 runner 原生执行 |
| Windows | x64、x86 | VS 2022，分别设置目标工具链并执行对应二进制 |
| Windows | ARM64 | ARM64 runner 上编译并执行 ARM64 二进制 |

ARMv7 在 QEMU 下编译 CGAL 可能接近 GitHub hosted runner 的六小时时限。
所有组独立执行；任一组失败都会阻止 Release 发布。没有使用 `continue-on-error`。
首次运行需要在 GitHub 上验证完整平台矩阵，静态检查不能替代编译和渲染验收。

## 固定版本与构建方式

- OpenSCAD：`openscad-2021.01`，提交
  `41f58fe57c03457a3a8b4dc541ef5654ec3e8c78`，同时获取其固定的 MCAD 子模块。
  此处固定的是 Tag 解引用后的提交 SHA，不是附注 Tag 对象本身的 SHA。
- macOS/Windows 依赖：vcpkg `2022.11.14`，提交
  `6f7ffeb18f99796233b958aaaf14ec7bd4fb64b2`。固定旧基线，避免 CGAL 6、Boost
  新版本移除 2021 源码使用的 API。二进制缓存按架构、依赖清单及 triplet 区分。
- Linux 依赖：Ubuntu 22.04 软件源；包内记录实际安装版本。
- Windows 软件 OpenGL：从官方源码编译 Mesa 24.3.4 softpipe，下载时校验固定 SHA-256。
  三种 Windows 架构都使用对应原生 DLL，不依赖虚拟机的 GPU 驱动。
- `HEADLESS=ON`、`NULLGL=OFF`，不编译 Qt GUI。OpenGL / OpenCSG / CGAL 均保留。
- 使用独立的 `.build/<target>/source` 副本打构建兼容补丁，不改上游子模块内容：
  统一依赖发现、跳过 GUI 测试目标、固定版本号、Windows console subsystem、macOS 最低版本 11。
  补丁在上游结构不匹配时直接失败。
- 保留 Cairo/PDF 功能。Linux 启用 lib3MF 1.x；macOS/Windows 固定依赖基线缺少
  lib3mf port，因此不提供可选的 3MF 导入/导出。

## 产物与验收

Release 包含八个架构压缩包（Windows `.zip`，其他 `.tar.gz`）、各包的 `.sha256`、
汇总 `SHA256SUMS`，以及源码包。源码包包含 OpenSCAD、MCAD、构建脚本与 Mesa 源码；
vcpkg 的固定提交和依赖清单可用于定位其他依赖的源码配方。
每个二进制包附带字体、MCAD、配色、示例、许可证与 `build-info.json`。

上传前解压实际归档到带空格的新路径，隐藏编译目录和 vcpkg 安装目录，并清除
库路径/字体/模型搜索环境变量，检查：

1. ELF、PE 或 Mach-O 架构与目标匹配，ARMv7 使用 hard-float ABI。
2. `--version` 为 2021.01。
3. 使用 MCAD、布尔运算与文字的模型能导出非空 STL。
4. 能生成 OpenCSG 预览 PNG 与 CGAL 渲染 PNG，检查 PNG 签名与尺寸。
5. `--info` 成功，未报告资源丢失或渲染错误。

## 使用压缩包

完整解压，保留原目录结构：

```sh
./bin/openscad --version
./bin/openscad -o model.stl model.scad
./bin/openscad --preview= -o preview.png model.scad
./bin/openscad --render=cgal -o render.png model.scad
```

Windows 使用 `bin\openscad.exe`。Mesa DLL 与运行库放在同一目录。
macOS 的 `bin/openscad` 调用包内的 `OpenSCAD.app`；应用经过 ad-hoc 签名，未公证。
macOS 最低部署目标为 11，仍需在实际最低系统版本上做兼容性验收。

Linux 需要 glibc 2.35 或更新版本。OpenSCAD 2021.01 的无头 OpenGL 后端是 GLX，
仍需要 X server；没有 `DISPLAY` 时启动器自动调用 `xvfb-run`。
Ubuntu/Debian 可安装运行依赖：

```sh
sudo apt-get install xvfb xauth libgl1 libglu1-mesa libglx-mesa0 libgl1-mesa-dri libx11-6
```

有可用 X11/OpenGL 环境时使用其 `DISPLAY`。未随包附带 glibc 或显卡驱动；
Linux 是 glibc 发行版包，不覆盖 Alpine/musl、BSD 或 Android。

本地脚本检查：`python3 -m unittest discover -s scripts/openscad-ci -p 'test_*.py'`。
