# scad-ai-ide-builder

## 干净的开发会话

```sh
npm run dev:vscode:fresh
```

每次启动都会创建独立的临时用户数据目录和用户扩展目录，不读取之前的开发设置、窗口状态、扩展状态和用户数据目录中的缓存。关闭该实例后，脚本自动删除本次临时目录；使用 Ctrl+C 或收到终止信号时也会尝试清理。

内置扩展仍从 `.dev/stage/extensions` 加载。打开项目时，项目自己的 `.vscode` 配置仍会生效；项目文件、构建产物和系统凭据不属于此命令的重置范围。强制杀死进程或断电可能留下临时目录，但下次启动始终使用新的目录。

原来的 `npm run dev:vscode` 继续使用 `.dev/user-data` 和 `.dev/extensions`，保留已有设置；干净会话不会删除或覆盖这些目录。

## 重置已保存的开发配置和缓存

先退出开发版 Code OSS，然后执行：

```sh
npm run reset:vscode
```

删除 `.dev/user-data`，包含设置、快捷键、窗口布局、工作区状态、扩展状态、会话备份和缓存。下次 `npm run dev:vscode` 会创建新的用户目录。用户安装的扩展保留；需要同时删除它们时使用 `npm run reset:vscode -- --all`。

也可以使用 `npm run dev:vscode:reset`，重置后立即启动。命令检测到持久化开发实例仍在运行时会拒绝清理。以上命令均保留 `.dev/stage`、`.build` 和源码；项目自身的 `.vscode` 设置仍会生效。

## 本地预览扩展开发

修改 `sources/vscode-openscad-preview` 后，在 Builder 根目录执行：

```sh
npm run build:extensions -- --preview=working-tree
npm run dev:vscode
```

`working-tree` 会复制子模块当前源码（包含未提交修改）到临时构建工作树，再打包并装配到 Dev Stage。默认配置仍从固定 Tag 构建；`submodule` 只读取子模块当前提交，不包含未提交改动。
