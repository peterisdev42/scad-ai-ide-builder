# OpenSCAD 无头 CLI

构建五种 macOS / Windows / Linux 原生目标，以及一套跨平台 Node.js WASM 几何回退包：

| 目标 | Runner | 包格式 |
| --- | --- | --- |
| darwin-x64 | `macos-26-intel` | `.tar.gz` |
| darwin-arm64 | `macos-latest`（目前为 macOS 26） | `.tar.gz` |
| win32-x64 | `windows-2022` | `.zip` |
| linux-x64（glibc） | `ubuntu-22.04` | `.tar.gz` |
| linux-arm64（glibc） | `ubuntu-22.04-arm` | `.tar.gz` |
| WASM（Node.js 22+） | `ubuntu-22.04` | `.tar.gz` |

Windows 仅构建 x64，使用 VS 2022，随包提供 Mesa 24.3.4 softpipe 和 MSVC 运行库。Linux 两种架构均在原生 Ubuntu 22.04 runner 上使用 GCC 11 构建，glibc 基线为 2.35；WASM 保留单个 Node.js 打包任务。
Windows 使用 `bin/openscad.exe`，其余参数与 macOS 相同。
工作流为 `.github/workflows/openscad-cli.yml`（**Package OpenSCAD CLI**）。
默认分支修改工作流或 `scripts/openscad-ci/` 后自动构建 `openscad-2026.10-TEST4`。
手动 Run workflow 可通过 `source_tag` 选择 `openscad-2026.10-TEST4` 或 `openscad-2021.01`。
其他分支只上传 Actions artifacts；默认分支五种原生目标和 WASM 打包均通过后，创建或更新
与所选源码版本对应的 `openscad-cli-<version>` GitHub Release，标题为 `OpenSCAD CLI <version>`，同名附件会被替换。
使用仓库内置 `GITHUB_TOKEN`，发布 job 具有 `contents: write` 权限。
创建 Release 时以默认分支名为目标，避免构建期间分支前进后，对旧工作流提交创建标签触发 403。
Release 标签作为下载入口；实际构建脚本提交以每个包内的 `build-info.json` 为准。
同一分支有新提交时，自动取消旧轮次，只验证最新版本。

发布文件名为 `openscad-cli-macos-{arm64,x86_64}.tar.gz` 和
`openscad-cli-windows-x86_64.zip`、`openscad-cli-linux-{arm64,x86_64}.tar.gz`，另有 `openscad-cli-wasm.tar.gz`。不提供 32 位原生包。

## 固定源码和依赖

- 默认源码 Tag：`openscad-2026.10-TEST4`，固定提交
  `5a864f17b6b022904004704f64e6168d2cfb8435`。
  递归检出并归档该提交固定的全部子模块，包含 MCAD、Clipper2 和 Manifold。
  使用上游 `OpenSCADExe` 目标及依赖发现，保持 CGAL 和 Manifold 两个后端；
  关闭 GUI、内置 Python、mimalloc 和 GUI 输入设备驱动；Linux 启用系统 lib3mf，macOS/Windows 关闭可选 3MF。
  版本字符串显式设为 `2026.10-TEST4`，资源包包含 shaders、templates 和子模块许可证。
  vcpkg 基线补充 OpenSSL、TBB 及新源码所需的 Boost 组件。
- 2021.01 的源码 API 补丁仅在选择旧 Tag 时应用，新版使用独立适配，避免混用旧补丁。

- OpenSCAD Tag：`openscad-2021.01`；解引用后的提交为
  `41f58fe57c03457a3a8b4dc541ef5654ec3e8c78`。同时获取固定的 MCAD 子模块。
- vcpkg：`2022.11.14`，提交 `6f7ffeb18f99796233b958aaaf14ec7bd4fb64b2`。
  macOS/Windows 两个源码配置使用同一固定基线；Linux 使用 Ubuntu 22.04 的原生开发库。2021.01 保留原有 CGAL 和 Boost 兼容补丁。
- Windows 宿主使用固定的 vcpkg-tool 2026-09-26 和 MSYS2 下载脚本快照，
  依赖库仍来自上述 2022.11.14 基线；pkgconf 3.0.10 与 WinFlexBison 2.5.25 验证 SHA-256。
- GLEW 2.2.0 使用链接器默认 DLL 基址，避免旧的固定基址与 Windows 工具链冲突。
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
  及 Cairo/PDF。macOS/Windows 的固定 vcpkg 基线不含 lib3mf；Linux 使用系统 lib3mf，打包时额外检查 3MF 导出。
- macOS 部署目标为 11，应用经过 ad-hoc 签名，未公证；最低系统版本仍需实际机器验收。

OpenSCAD 的构建兼容补丁只应用到 `.build/<target>/source` 副本；不会修改子模块源码。
TEST4 离屏视图先在仍有效的 OpenGL 上下文中释放 renderer、shader 和 FBO，
再销毁上下文，避免 x86 在 PNG 导出后的析构阶段访问失效的 OpenGL 状态。
vcpkg 编译产物按架构及依赖缓存，失败时也保存已完成的依赖。
失败日志上传包含隐藏的 `.build` 目录，并把关键依赖日志打印到 Actions 控制台。

## 验收与 Release

每个包包含 CLI、动态运行库、字体、MCAD、配色、示例、已编译的翻译目录、许可证和 `build-info.json`。
完整解压后即可运行，无需安装 OpenSCAD、Homebrew、Python、Qt 或 MSVC 运行库。
macOS/Windows 的第三方运行库随包提供；Linux 收集包括 libstdc++ 在内的非系统依赖，glibc、动态加载器和 GL/GLX 驱动栈由宿主提供。
Windows 包包含对应架构的 MSVC CRT 和 Mesa 软件 OpenGL，不需要另装显卡驱动来完成离屏渲染。
MSVC CRT 版本匹配编译工具集，按照实际导入递归收集，避免把 CRT 目录内其他 ABI 专用的 DLL 混入原生包。
发布前逐个审计 EXE、DLL、ELF 或 Mach-O 的依赖，拒绝包外第三方库和不可搬移的绝对库路径。
检查 Mach-O / PE / ELF 架构后，将实际归档解压到带空格的新路径，隐藏编译目录和 vcpkg 安装目录，
清除库/字体/模型搜索环境变量，仅保留系统 PATH，执行：

1. `--version` 确认版本与所选源码 Tag 对应。
2. 使用 MCAD、布尔运算和文字导出非空 STL。
3. 导出 OpenCSG 预览 PNG 和 CGAL 渲染 PNG，检查文件签名与尺寸。
4. TEST4 使用中文文件名验证参数编码，额外导出 Manifold 渲染 PNG，验证新后端。
5. `--info` 确认离屏 OpenGL 可用，无资源丢失或渲染错误。

五种原生包与 WASM 包全部通过后，Release 校验其 SHA-256 和构建元数据，仅发布六种运行时包及其逐包 SHA256 校验文件，共 12 个附件。
发布步骤不生成或上传源码压缩包。
正文提供六种目标的下载和 SHA256 链接表格，链接文字使用对应 Assets 文件名，并附简要构建参数说明。
Linux 明确声明 glibc 2.35+ 基线，并检查包内所有 ELF 的 GLIBC 符号版本；macOS 的最低系统版本仍需实际机器验证。
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

## Linux 原生包

`linux-x64` 与 `linux-arm64` 分别在 Ubuntu 22.04 的 x64 / ARM64 runner 上编译，不使用 CPU 模拟。
要求构建机 `getconf GNU_LIBC_VERSION` 为 glibc 2.35，并审计包内 ELF 的架构、动态库解析路径及 GLIBC 符号版本。
`build-info.json` 记录 glibc 基线与实际最高 GLIBC 需求；发布门禁拒绝超过 2.35 的产物。
这两个目标为 glibc Linux ELF 包，不是 musl / Alpine 原生包。

运行环境需提供 GL/GLX 和驱动；没有 DISPLAY 时启动器使用宿主的 `xvfb-run`。
Ubuntu/Debian 的无桌面环境可安装：

```sh
sudo apt-get install libgl1 libglx-mesa0 libgl1-mesa-dri xvfb xauth
```

完整解压后运行 `bin/openscad`。库的 RPATH 使用相对路径，打包后会隐藏构建目录，并执行版本、STL、PNG、3MF 和 OpenGL 检查；失败即打包失败。

## WASM 几何回退

WASM 与原生包使用相同的所选 tag/SHA，支持 2021.01 和 2026.10-TEST4。
使用 OpenSCAD 官方 `wasm-base` 镜像并固定 SHA256 digest（见 `wasm.py`），
其中提供 Emscripten 4.0.10 和已交叉编译的依赖；不使用浮动 `latest`。
2021.01 额外固定 CGAL 5.5.1 头文件并使用与原生旧版一致的 API 兼容补丁。
TEST4 保留上游 Emscripten 构建分支，启用 CGAL 和 Manifold；不混用原生 Windows/macOS 补丁。

WASM 采用上游 `HEADLESS=ON` / `NULLGL=ON` 策略：支持几何导出、MCAD、文字与字体，
不提供 OpenGL/OpenCSG PNG 预览、Cairo/PDF 或 3MF。Manifold 采用单线程模式。
这与原生包 `NULLGL=OFF` 的完整离屏渲染定位不同，包内 `build-info.json` 明确记录能力。
WASM 只运行一个打包任务，在 `ubuntu-22.04` 内完成编译、归档与基础可运行检查；任一步失败即打包失败。
它是宿主无原生 CLI 时的可选回退产物，本次不修改 VSCode 插件的运行时选择逻辑。

完整解压（包括 `bin` 与 `share`）后执行：

```sh
node bin/openscad.mjs --version
node bin/openscad.mjs -o model.stl model.scad
# TEST4 可明确选择后端
node bin/openscad.mjs --backend=Manifold -o model.stl model.scad
```

需要宿主提供 Node.js 22 或更新版本；包内不重复捆绑各架构 Node。
入口映射当前工作目录、绝对输入/输出路径和 `OPENSCADPATH` / `OPENSCAD_FONT_PATH` 到 NodeFS，
因此 `.wasm` 本身不依赖宿主 CPU 架构。SCAD 源码中的绝对宿主路径需改为相对路径或传入映射后的虚拟路径。
建议以 `node` 可执行文件与 `bin/openscad.mjs` 启动前缀集成，单独启动进程以支持取消和退出码隔离。

构建命令（Linux amd64、Docker、Python 3.11、Node.js 22）：

```sh
python scripts/openscad-ci/wasm.py
```

WASM 验收使用实际归档的新解压目录，隐藏源码与构建目录，清理环境中的外部库/字体路径，
检查版本、中文输入文件名、相对 `use`、MCAD、布尔运算、文字字体以及 CGAL/Manifold 的 ASCII STL。
还检查错误模型返回非零退出码。发布门禁独立检查 WASM 文件头、JS 加载文件及能力元数据，
不会为 WASM 放宽原生包必须具备 OpenGL 的要求。
