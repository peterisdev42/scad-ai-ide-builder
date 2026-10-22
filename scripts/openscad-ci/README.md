# OpenSCAD 2021.01 无头 CLI

构建 macOS 和 Windows 的原生架构：

| 目标 | Runner | 包格式 |
| --- | --- | --- |
| Intel x64 | `macos-15-intel` | `.tar.gz` |
| Apple Silicon ARM64 | `macos-15` | `.tar.gz` |

| Windows x64 | `windows-2022` | `.zip` |
| Windows x86 | `windows-2022` | `.zip` |
| Windows ARM64 | `windows-11-arm` | `.zip` |

Linux 暂不参与构建。Windows x64/x86 使用 VS 2022，ARM64 使用 VS 2026 中的 v143 14.44 工具集，随包提供原生 Mesa 24.3.4 softpipe 和 MSVC 运行库。
Windows 使用 `bin/openscad.exe`，其余参数与 macOS 相同。
工作流为 `.github/workflows/openscad-cli.yml`（**Package OpenSCAD CLI**）。
默认分支修改工作流或 `scripts/openscad-ci/` 后自动构建，也可手动 Run workflow。
其他分支只上传 Actions artifacts；默认分支五种目标均通过后，创建或更新
`openscad-cli-2021.01` GitHub Release，同名附件会被替换。
使用仓库内置 `GITHUB_TOKEN`，发布 job 具有 `contents: write` 权限。
同一分支有新提交时，自动取消旧轮次，只验证最新版本。

## 固定源码和依赖

- OpenSCAD Tag：`openscad-2021.01`；解引用后的提交为
  `41f58fe57c03457a3a8b4dc541ef5654ec3e8c78`。同时获取固定的 MCAD 子模块。
- vcpkg：`2022.11.14`，提交 `6f7ffeb18f99796233b958aaaf14ec7bd4fb64b2`。
  使用旧版 CGAL 和 Boost API，与 2021.01 源码保持兼容。
- Windows 宿主使用固定的 vcpkg-tool 2026-09-26 和 MSYS2 下载脚本快照，
  依赖库仍来自上述 2022.11.14 基线；pkgconf 3.0.10 与 WinFlexBison 2.5.25 验证 SHA-256。
- 两个 macOS runner 显式选择 Xcode 16.4（Apple Clang 17），避免 runner 默认编译器漂移。
- `patches/*-modern-macos.patch` 修复新版 Apple SDK 暴露 `TARGET_OS_MAC`
  导致 zlib 1.2.13 误用 Classic Mac OS 的 `fdopen` 宏，以及 libpng 1.6.38
  尝试包含旧式 `fp.h` 的问题。
- `patches/pixman-modern-clang.patch` 移除两个浮点返回值上多余的 `const`，
  与回调类型保持一致，修复新版 Clang 的函数指针类型检查错误。
- 依赖与 OpenSCAD 的 C++ 编译使用 `-Wno-enum-constexpr-conversion`，允许固定
  Boost 1.80 MPL 使用其原有的枚举常量写法；其他编译诊断保持启用。
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
检查 Mach-O / PE 架构后，将实际归档解压到带空格的新路径，隐藏编译目录和 vcpkg 安装目录，
清除库/字体/模型搜索环境变量，执行：

1. `--version` 确认版本为 2021.01。
2. 使用 MCAD、布尔运算和文字导出非空 STL。
3. 导出 OpenCSG 预览 PNG 和 CGAL 渲染 PNG，检查文件签名与尺寸。
4. `--info` 确认离屏 OpenGL 可用，无资源丢失或渲染错误。

五种包全部通过后，Release 校验其 SHA-256 和构建元数据，再发布二进制包、逐包校验文件、
`SHA256SUMS` 以及包含 OpenSCAD、MCAD、Mesa、构建脚本/补丁的源码包。

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
