# OpenSCAD 2021.01 无头 CLI

构建 macOS 和 Windows 的原生架构：

| 目标 | Runner | 包格式 |
| --- | --- | --- |
| Intel x64 | `macos-26-intel` | `.tar.gz` |
| Apple Silicon ARM64 | `macos-latest`（目前为 macOS 26） | `.tar.gz` |
| Windows x64 | `windows-2022` | `.zip` |
| Windows x86 | `windows-2022` | `.zip` |
| Windows ARM64 | `windows-11-arm` | `.zip` |

Linux 暂不参与构建。Windows x64/x86 使用 VS 2022，ARM64 使用 VS 2026 中的 v143 14.44 工具集，随包提供原生 Mesa 24.3.4 softpipe 和 MSVC 运行库。
Windows 使用 `bin/openscad.exe`，其余参数与 macOS 相同。
工作流为 `.github/workflows/openscad-cli.yml`（**Package OpenSCAD CLI**）。
默认分支修改工作流或 `scripts/openscad-ci/` 后自动构建，也可手动 Run workflow。
其他分支只上传 Actions artifacts；默认分支五种目标均通过后，创建或更新
`openscad-cli-2021.01` GitHub Release，标题为 `OpenSCAD CLI 2021.01`，同名附件会被替换。
使用仓库内置 `GITHUB_TOKEN`，发布 job 具有 `contents: write` 权限。
同一分支有新提交时，自动取消旧轮次，只验证最新版本。

发布文件名为 `openscad-cli-macos-{arm64,x86_64}.tar.gz` 和
`openscad-cli-windows-{arm64,x86_64,x86}.zip`。macOS 不提供 x86（32 位）包。

## 固定源码和依赖

- OpenSCAD Tag：`openscad-2021.01`；解引用后的提交为
  `41f58fe57c03457a3a8b4dc541ef5654ec3e8c78`。同时获取固定的 MCAD 子模块。
- vcpkg：`2022.11.14`，提交 `6f7ffeb18f99796233b958aaaf14ec7bd4fb64b2`。
  使用旧版 CGAL 和 Boost API，与 2021.01 源码保持兼容。
- Windows 宿主使用固定的 vcpkg-tool 2026-09-26 和 MSYS2 下载脚本快照，
  依赖库仍来自上述 2022.11.14 基线；pkgconf 3.0.10 与 WinFlexBison 2.5.25 验证 SHA-256。
- GLEW 2.2.0 使用链接器默认 DLL 基址，避免旧的 32 位固定基址导致 Windows ARM64 的 LNK1355 错误。
- macOS 使用当前稳定镜像：Intel 为 `macos-26-intel`，ARM64 为 `macos-latest`。
  使用镜像默认的 `/Applications/Xcode.app`，显式设置对应的 Apple Clang `CC` / `CXX`，
  并在日志输出系统、架构、Xcode 和编译器版本。镜像升级时工具链也可能更新。
- `patches/*-modern-macos.patch` 修复新版 Apple SDK 暴露 `TARGET_OS_MAC`
  导致 zlib 1.2.13 误用 Classic Mac OS 的 `fdopen` 宏，以及 libpng 1.6.38
  尝试包含旧式 `fp.h` 的问题。
- `patches/pixman-modern-clang.patch` 移除两个浮点返回值上多余的 `const`，
  与回调类型保持一致，修复新版 Clang 的函数指针类型检查错误。
- `patches/boost-mpl-lazy-enum-constants.patch` 回移 [Boost.MPL 上游 PR #77](https://github.com/boostorg/mpl/pull/77)，
  延迟实例化枚举的相邻常量，修复 Boost 1.80 在 Clang 21 下的越界枚举常量错误。
  不再使用新版 Clang 已移除的 `-Wno-enum-constexpr-conversion` 开关。
- macOS 的 gperf 3.1 源码改用 GNU HTTPS 下载地址及官方镜像，保留原端口的 SHA-512 校验。
- `patches/harfbuzz-modern-clang.patch` 将 HarfBuzz 5.0.1 内部针对旧式回调转换的
  `-Wcast-function-type` 从错误降为告警，其余源码诊断仍保留。
- `patches/cgal-iterator-bool.patch` 修正 CGAL 5.5.1 三个迭代器的空状态判断，
  直接检查其图指针，与现有空指针比较语义一致。
  OpenSCAD 两处字符串拼接明确使用 `boost::algorithm::join`，避免与 range join 重载歧义。
- OpenSCAD 的多面体文本输出显式遍历顶点和面，保持原 writer 的格式与面方向，
  不再使用 CGAL 已移除的 `generic_print_polyhedron`。
- 三角剖分改用 CGAL 官方替代接口 `Projection_traits_3`，按 kernel 保留过滤谓词。
- `HEADLESS=ON`、`NULLGL=OFF`：不构建 Qt GUI，保留原生离屏 OpenGL、OpenCSG、CGAL
  及 Cairo/PDF。固定依赖基线不含可选 lib3mf，因此暂不提供 3MF 导入/导出。
- macOS 部署目标为 11，应用经过 ad-hoc 签名，未公证；最低系统版本仍需实际机器验收。

OpenSCAD 的构建兼容补丁只应用到 `.build/<target>/source` 副本；不会修改子模块源码。
vcpkg 编译产物按架构及依赖缓存，失败时也保存已完成的依赖。
失败日志上传包含隐藏的 `.build` 目录，并把关键依赖日志打印到 Actions 控制台。

## 验收与 Release

每个包包含 CLI、动态运行库、字体、MCAD、配色、示例、已编译的翻译目录、许可证和 `build-info.json`。
完整解压后即可运行，无需安装 OpenSCAD、Homebrew、Python、Qt 或 MSVC 运行库。
所有第三方运行库随包提供；操作系统自带的系统库与框架由系统提供。
Windows 包包含对应架构的 MSVC CRT 和 Mesa 软件 OpenGL，不需要另装显卡驱动来完成离屏渲染。
MSVC CRT 版本匹配编译工具集，按照实际导入递归收集，避免把 CRT 目录内其他 ABI 专用的 DLL 混入原生包。
发布前逐个审计 EXE、DLL 或 Mach-O 的依赖，拒绝包外第三方库和不可搬移的绝对库路径。
检查 Mach-O / PE 架构后，将实际归档解压到带空格的新路径，隐藏编译目录和 vcpkg 安装目录，
清除库/字体/模型搜索环境变量，仅保留系统 PATH，执行：

1. `--version` 确认版本为 2021.01。
2. 使用 MCAD、布尔运算和文字导出非空 STL。
3. 导出 OpenCSG 预览 PNG 和 CGAL 渲染 PNG，检查文件签名与尺寸。
4. `--info` 确认离屏 OpenGL 可用，无资源丢失或渲染错误。

五种包全部通过后，Release 校验其 SHA-256 和构建元数据，再发布二进制包、逐包校验文件，
以及包含 OpenSCAD、MCAD、Mesa、构建脚本/补丁的源码包，共 11 个附件。
正文提供五种目标的下载和 SHA256 链接表格，链接文字使用对应 Assets 文件名，并附简要构建参数说明。
最低系统版本尚未经过可靠验证，因此不列 Compatibility 字段。
全部新附件上传成功后，自动删除该 Release 中不在上述清单内的旧附件；不发布重复的汇总校验文件。

完整解压后使用：

```sh
./bin/openscad --version
./bin/openscad -o model.stl model.scad
./bin/openscad --preview -o preview.png model.scad
./bin/openscad --render=cgal -o render.png model.scad
```

保留目录结构，启动器会调用包内 `OpenSCAD.app/Contents/MacOS/OpenSCAD`。

本地脚本回归检查：

```sh
python3 -m unittest discover -s scripts/openscad-ci -p 'test_*.py' -v
```

完整编译、动态库重定位和 OpenGL 验收以各原生 GitHub runner 的运行结果为准。
